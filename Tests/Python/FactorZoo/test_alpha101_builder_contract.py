# Tests/Python/FactorZoo/test_alpha101_builder_contract.py
"""build_day: parquet layout + Influx line-protocol + per-alpha try/except."""
import sys, os
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))


@pytest.fixture
def tiny_data_root(tmp_path):
    for code in ("000001.SZ", "600000.SH"):
        d = tmp_path / "daily" / f"ts_code={code}"; d.mkdir(parents=True)
        rows = []
        for i in range(60):
            dt = f"2026{(i//30)+1:02d}{(i%30)+1:02d}"
            rows.append({"ts_code": code, "trade_date": dt, "open": 10+i*0.1, "high": 11+i*0.1,
                         "low": 9+i*0.1, "close": 10.5+i*0.1, "pre_close": 10.4+i*0.1,
                         "change": 0.1, "pct_chg": 1.0, "vol": 100.0+i, "amount": 1000.0+i*10})
        pq.write_table(pa.Table.from_pylist(rows), d / "data.parquet")
        a = tmp_path / "adj_factor" / f"ts_code={code}"; a.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(
            [{"ts_code": code, "trade_date": r["trade_date"], "adj_factor": 1.0} for r in rows]), a / "data.parquet")
        b = tmp_path / "daily_basic" / f"ts_code={code}"; b.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(
            [{"ts_code": code, "trade_date": r["trade_date"], "total_mv": 1e6} for r in rows]), b / "data.parquet")
    m = tmp_path / "index_member_all"; m.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"l1_code": "s1", "l1_name": "s", "l2_code": "i1", "l2_name": "i", "l3_code": "u1",
         "l3_name": "u", "ts_code": "000001.SZ", "name": "A", "in_date": "20100101", "out_date": None, "is_new": "Y"},
        {"l1_code": "s2", "l1_name": "s", "l2_code": "i2", "l2_name": "i", "l3_code": "u2",
         "l3_name": "u", "ts_code": "600000.SH", "name": "B", "in_date": "20100101", "out_date": None, "is_new": "Y"},
    ]), m / "data.parquet")
    return tmp_path


def test_build_day_writes_parquet(tiny_data_root, tmp_path, monkeypatch):
    from alpha101 import builder
    captured = []
    monkeypatch.setattr(builder, "write_influx", lambda lines, **kw: (captured.extend(lines), len(lines))[1])
    out = builder.build_day("2026-02-28", ["000001.SZ", "600000.SH"],
                            data_root=str(tiny_data_root), result_root=str(tmp_path / "result"),
                            write_influxdb=True)
    p = tmp_path / "result" / "factor-zoo" / "alpha101" / "2026-02-28" / "000001.SZ.parquet"
    assert p.exists()
    df = pd.read_parquet(p)
    assert "alpha101" in df.columns
    assert len(captured) > 0
    line = captured[0]
    assert line.startswith("lean_factor_") and "ts_code=" in line and "trade_date=" in line


def test_per_alpha_failure_isolated(tiny_data_root, tmp_path, monkeypatch):
    from alpha101 import builder, formulas
    def boom(p):
        raise RuntimeError("boom")
    monkeypatch.setitem(formulas.ALPHAS, "alpha055", boom)
    out = builder.build_day("2026-02-28", ["000001.SZ"], data_root=str(tiny_data_root),
                            result_root=str(tmp_path / "result"), write_influxdb=False)
    assert (tmp_path / "result" / "factor-zoo" / "alpha101" / "2026-02-28" / "000001.SZ.parquet").exists()
    assert "alpha055" in out.get("failed", {})
