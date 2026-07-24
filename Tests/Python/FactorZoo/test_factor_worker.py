"""Tests for factor_worker (Phase 3, spec §3.1). Hermetic."""
import json, sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
import factor_worker as fw  # noqa: E402

TRADE_CAL_ROWS = [
    {"cal_date": "20260720", "is_open": 1},
    {"cal_date": "20260721", "is_open": 0},
    {"cal_date": "20260722", "is_open": 1},
    {"cal_date": "20260723", "is_open": 1},
    {"cal_date": "20261231", "is_open": 1},  # future, must be filtered
]

@pytest.fixture
def tmp_data_root(tmp_path):
    d = tmp_path / "trade_cal"; d.mkdir(parents=True)
    tbl = pa.Table.from_pylist(TRADE_CAL_ROWS)
    pq.write_table(tbl, d / "data.parquet")
    return tmp_path

@pytest.fixture
def isolated_fw(tmp_path, tmp_data_root, monkeypatch):
    sd = tmp_path / "Results" / "factor-zoo"
    monkeypatch.setattr(fw, "TUSHARE_DATA_PATH", str(tmp_data_root))
    monkeypatch.setattr(fw, "SCHEDULER_STATE_FILE", tmp_path / "scheduler_state.json")
    monkeypatch.setattr(fw, "STATE_FILE", sd / "factor_worker_state.json")
    monkeypatch.setattr(fw, "FRESHNESS_FILE", sd / "freshness.json")
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    monkeypatch.setattr(fw, "BARRA_OUTPUT_ROOT", tmp_path / "barra-cne5v2-factors")
    monkeypatch.setattr(fw, "INFLUX_TOKEN", "")
    return tmp_path

def _write_sched(path, last_finished):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_finished_target_date": last_finished}), encoding="utf-8")

def _fake_builder(fid, t_f, recorder, raise_on=None):
    raise_on = raise_on or set()
    def _build(d):
        recorder.append((fid, d))
        if d in raise_on: raise RuntimeError(f"fake {fid} boom on {d}")
        return {"rows": 1, "duration_ms": 10}
    def _resolve(): return t_f
    return fw.FactorBuilder(factor_id=fid, build_callable=_build, latest_date_resolver=_resolve)

def test_t_raw_less_than_t_cal_skips(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260722")
    calls=[]; monkeypatch.setattr(fw, "BUILDERS", [_fake_builder("alpha", None, calls)])
    r = fw.run_once(dry_run=False)
    assert r["status"] == "waiting_tushare"
    assert r["t_cal"] == "20260723" and r["t_raw"] == "20260722"
    assert calls == []

def test_factor_already_fresh_skips(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    calls=[]; monkeypatch.setattr(fw, "BUILDERS", [_fake_builder("alpha", "20260723", calls)])
    r = fw.run_once(dry_run=False)
    assert r["built"]["alpha"]["status"] == "fresh"
    assert calls == []

def test_one_day_gap_builds_once(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    calls=[]; monkeypatch.setattr(fw, "BUILDERS", [_fake_builder("alpha", "20260722", calls)])
    r = fw.run_once(dry_run=False)
    assert r["built"]["alpha"]["built_dates"] == ["20260723"]
    assert calls == [("alpha", "20260723")]
    st = json.loads(fw.STATE_FILE.read_text(encoding="utf-8"))
    assert st["factors"]["alpha"]["last_built_date"] == "20260723"

def test_single_failure_does_not_abort_others(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    calls=[]
    monkeypatch.setattr(fw, "BUILDERS", [
        _fake_builder("alpha", "20260722", calls, raise_on={"20260723"}),
        _fake_builder("beta", "20260722", calls)])
    r = fw.run_once(dry_run=False)
    assert r["built"]["alpha"]["status"] == "failed"
    assert r["built"]["beta"]["status"] == "ok"
    st = json.loads(fw.STATE_FILE.read_text(encoding="utf-8"))
    assert st["factors"]["alpha"]["fail_count"] == 1

def test_crash_resume_uses_state(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    fw.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fw.STATE_FILE.write_text(json.dumps({"factors": {"alpha": {"last_built_date": "20260722"}}, "updated_at": None}), encoding="utf-8")
    calls=[]
    def _res_from_state():
        st = json.loads(fw.STATE_FILE.read_text(encoding="utf-8"))
        return st["factors"]["alpha"]["last_built_date"]
    monkeypatch.setattr(fw, "BUILDERS", [fw.FactorBuilder("alpha",
        lambda d: calls.append(("alpha", d)) or {"rows":1,"duration_ms":5}, _res_from_state)])
    r = fw.run_once(dry_run=False)
    assert r["built"]["alpha"]["built_dates"] == ["20260723"]

def test_poisoned_after_three_failures(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    monkeypatch.setattr(fw, "POISON_THRESHOLD", 3)
    fw.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fw.STATE_FILE.write_text(json.dumps({"factors": {"alpha": {"last_built_date": "20260722", "last_status": "failed", "fail_count": 2}}, "updated_at": None}), encoding="utf-8")
    calls=[]
    monkeypatch.setattr(fw, "BUILDERS", [_fake_builder("alpha", "20260722", calls, raise_on={"20260723"})])
    r1 = fw.run_once(dry_run=False)
    assert r1["built"]["alpha"]["status"] == "poisoned"
    calls.clear()
    r2 = fw.run_once(dry_run=False)
    assert r2["built"]["alpha"]["status"] == "poisoned_skipped"
    assert calls == []
    fresh = json.loads(fw.FRESHNESS_FILE.read_text(encoding="utf-8"))
    assert fresh["alpha"]["status"] == "poisoned"

def test_freshness_json_influx_skipped_without_token(isolated_fw, monkeypatch):
    _write_sched(fw.SCHEDULER_STATE_FILE, "20260723")
    influx=[]; monkeypatch.setattr(fw, "_write_influx", lambda *a, **k: influx.append(1) or 0)
    monkeypatch.setattr(fw, "BUILDERS", [_fake_builder("alpha", "20260722", [])])
    fw.run_once(dry_run=False)
    assert fw.FRESHNESS_FILE.exists()
    fresh = json.loads(fw.FRESHNESS_FILE.read_text(encoding="utf-8"))
    assert fresh["alpha"]["lag_days"] == 0
    assert influx == []
