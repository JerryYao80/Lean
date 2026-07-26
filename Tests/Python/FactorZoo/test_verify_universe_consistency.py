"""STEP 0a: verify CSI300 + CSI500 universe is usable + consistent (spec §7)."""
import sys
from pathlib import Path

# test file lives at Tests/Python/FactorZoo/test_*.py so parents[3] is repo root
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))

import pyarrow as pa
import pyarrow.parquet as pq

from factor_zoo.verify_universe_consistency import (
    load_index_members,
    resolve_universe,
    main,
    UniverseReport,
)


def _write_index_weight_partition(root, trade_date, rows):
    """Write one trade_date= partition with mixed index_code rows."""
    d = root / "index_weight" / f"trade_date={trade_date}"
    d.mkdir(parents=True)
    tbl = pa.table(rows)
    pq.write_table(tbl, d / "data.parquet")


def test_load_index_members_filters_by_index_code(tmp_path):
    """index_weight partitions mix all index_codes; loader must filter."""
    _write_index_weight_partition(tmp_path, "20260101", {
        "index_code": ["000300.SH", "000905.SH", "000300.SH"],
        "con_code": ["600519.SH", "000001.SZ", "000002.SZ"],
        "trade_date": ["20260101", "20260101", "20260101"],
        "weight": [0.05, 0.02, 0.04],
    })
    members = load_index_members("000300.SH", data_root=str(tmp_path), asof="20260101")
    assert members == ["000002.SZ", "600519.SH"]  # sorted


def test_resolve_universe_dedupes_across_indices(tmp_path):
    """CSI300 and CSI500 overlap; universe must be the union, deduped + sorted."""
    _write_index_weight_partition(tmp_path, "20260120", {
        "index_code": ["000300.SH", "000300.SH", "000905.SH", "000905.SH"],
        "con_code": ["600519.SH", "000001.SZ", "000001.SZ", "000002.SZ"],
        "trade_date": ["20260120"] * 4,
        "weight": [0.05, 0.04, 0.02, 0.02],
    })
    rep = resolve_universe(["000300.SH", "000905.SH"], data_root=str(tmp_path), asof="20260120")
    assert isinstance(rep, UniverseReport)
    assert rep.members == ["000001.SZ", "000002.SZ", "600519.SH"]
    assert rep.csi300_count == 2
    assert rep.csi500_count == 2
    assert rep.union_count == 3
    assert rep.overlap_count == 1


def test_resolve_universe_missing_index_returns_flag(tmp_path):
    """If CSI500 members can't be resolved (missing), report it, don't crash."""
    _write_index_weight_partition(tmp_path, "20260120", {
        "index_code": ["000300.SH"],
        "con_code": ["600519.SH"],
        "trade_date": ["20260120"],
        "weight": [0.05],
    })
    rep = resolve_universe(["000300.SH", "000905.SH"], data_root=str(tmp_path), asof="20260120")
    assert "000905.SH" in rep.unresolved
    assert rep.csi300_count == 1


def test_resolve_universe_skips_corrupt_parquet(tmp_path):
    """A corrupt partition file must be skipped silently, not crash the run.

    Mirrors the Task 1 audit resilience pattern (audit_tushare_coverage.py:85-98):
    one bad footer among thousands of partitions must not abort universe
    resolution.
    """
    # one good partition with a 000300.SH member
    _write_index_weight_partition(tmp_path, "20260120", {
        "index_code": ["000300.SH"],
        "con_code": ["600519.SH"],
        "trade_date": ["20260120"],
        "weight": [0.05],
    })
    # one corrupt partition: garbage bytes where a parquet footer should be
    bad_dir = tmp_path / "index_weight" / "trade_date=20260121"
    bad_dir.mkdir(parents=True)
    (bad_dir / "data.parquet").write_bytes(b"NOT A PARQUET FILE")
    # must not raise; must still resolve the good partition's member
    members = load_index_members("000300.SH", data_root=str(tmp_path), asof="20260121")
    assert members == ["600519.SH"]


def _write_trade_cal(root, cal_dates, is_opens):
    """Write a trade_cal parquet with the given cal_date / is_open rows."""
    d = root / "trade_cal"
    d.mkdir(parents=True)
    tbl = pa.table({"cal_date": cal_dates, "is_open": is_opens})
    pq.write_table(tbl, d / "data.parquet")


def test_main_returns_nonzero_when_index_unresolved(tmp_path):
    """Gate (STEP 0a) must exit non-zero when an index is unresolved.

    This is a GATE (前置验收闸): CI/callers detect failure programmatically
    via exit code. A unresolved index (e.g. 000905.SH missing) means the
    gate FAILED and main() must return non-zero.
    """
    # trade_cal: a single past open date so _resolve_latest_trade_date succeeds
    import datetime as dt
    past = (dt.date.today() - dt.timedelta(days=5)).strftime("%Y%m%d")
    _write_trade_cal(tmp_path, [past], [1])
    # index_weight: only 000300.SH present; 000905.SH is unresolved
    _write_index_weight_partition(tmp_path, past, {
        "index_code": ["000300.SH"],
        "con_code": ["600519.SH"],
        "trade_date": [past],
        "weight": [0.05],
    })
    rc = main(["--data-root", str(tmp_path), "--json"])
    assert rc != 0, (
        f"gate FAILED (000905.SH unresolved) but main() returned {rc} (expected non-zero)"
    )


def test_main_returns_zero_when_all_indices_resolve(tmp_path):
    """Gate passes (all resolve) -> main() returns 0."""
    import datetime as dt
    past = (dt.date.today() - dt.timedelta(days=5)).strftime("%Y%m%d")
    _write_trade_cal(tmp_path, [past], [1])
    _write_index_weight_partition(tmp_path, past, {
        "index_code": ["000300.SH", "000905.SH"],
        "con_code": ["600519.SH", "000001.SZ"],
        "trade_date": [past, past],
        "weight": [0.05, 0.02],
    })
    rc = main(["--data-root", str(tmp_path), "--json"])
    assert rc == 0, (
        f"gate PASSED (all indices resolved) but main() returned {rc} (expected 0)"
    )
