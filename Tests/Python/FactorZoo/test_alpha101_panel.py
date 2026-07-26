# Tests/Python/FactorZoo/test_alpha101_panel.py
"""Alpha101Panel dataclass + load_csi800_universe tests (hermetic)."""
import sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))

from alpha101.panel_loader import Alpha101Panel, load_csi800_universe, load_panel  # noqa: E402


def test_alpha101_panel_fields():
    for f in ("open", "high", "low", "close", "close_raw", "volume", "vwap",
              "returns", "cap", "adv", "industry", "dates", "asof"):
        assert f in Alpha101Panel.__dataclass_fields__


@pytest.fixture
def tmp_data_root(tmp_path):
    for code in ("000001.SZ", "600000.SH"):
        d = tmp_path / "daily" / f"ts_code={code}"; d.mkdir(parents=True)
        rows = []
        for i, dt in enumerate(["20260101", "20260102", "20260103", "20260104", "20260105"]):
            rows.append({"ts_code": code, "trade_date": dt,
                         "open": 10+i, "high": 11+i, "low": 9+i, "close": 10.5+i,
                         "pre_close": 9.5+i, "change": 1.0, "pct_chg": 10.0,
                         "vol": 100.0+i, "amount": 1000.0+i*10})
        pq.write_table(pa.Table.from_pylist(rows), d / "data.parquet")
        a = tmp_path / "adj_factor" / f"ts_code={code}"; a.mkdir(parents=True)
        arows = [{"ts_code": code, "trade_date": r["trade_date"], "adj_factor": 1.0} for r in rows]
        pq.write_table(pa.Table.from_pylist(arows), a / "data.parquet")
        b = tmp_path / "daily_basic" / f"ts_code={code}"; b.mkdir(parents=True)
        brows = [{"ts_code": code, "trade_date": r["trade_date"], "total_mv": 1e6} for r in rows]
        pq.write_table(pa.Table.from_pylist(brows), b / "data.parquet")
    m = tmp_path / "index_member_all"; m.mkdir(parents=True)
    mrows = [
        {"l1_code": "801880", "l1_name": "汽车", "l2_code": "801881", "l2_name": "摩托车",
         "l3_code": "858811", "l3_name": "其他运输", "ts_code": "000001.SZ", "name": "平安",
         "in_date": "20100101", "out_date": None, "is_new": "Y"},
        {"l1_code": "801180", "l1_name": "房地产", "l2_code": "801181", "l2_name": "开发",
         "l3_code": "851811", "l3_name": "住宅", "ts_code": "600000.SH", "name": "浦发",
         "in_date": "20100101", "out_date": None, "is_new": "Y"},
    ]
    pq.write_table(pa.Table.from_pylist(mrows), m / "data.parquet")
    return tmp_path


def test_load_csi800_universe_dedup(tmp_data_root, monkeypatch):
    class FakeLoader:
        def load_index_constituents(self, asof_date, index_code):
            return ["000001.SZ", "600000.SH"] if index_code == "000300.SH" else ["600000.SH"]
    codes = load_csi800_universe(FakeLoader(), "20260105")
    assert sorted(codes) == ["000001.SZ", "600000.SH"]


def test_industry_renames_l_levels(tmp_data_root):
    p = load_panel(["000001.SZ", "600000.SH"], "20260105", data_root=str(tmp_data_root))
    assert p is not None
    for col in ("l1", "l2", "l3"):
        assert col in p.industry.columns, f"missing {col} in industry columns: {list(p.industry.columns)}"
    # l*_code columns retained (harmless; future callers may want codes)
    for col in ("l1_code", "l2_code", "l3_code"):
        assert col in p.industry.columns, f"missing {col} in industry columns: {list(p.industry.columns)}"
    # l1 is the industry NAME label (e.g. "汽车"), not the code
    assert p.industry.set_index("ts_code").loc["000001.SZ", "l1"] == "汽车"
