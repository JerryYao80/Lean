# Tests/Python/FactorZoo/test_alpha101_vwap_units.py
"""Pin vwap = amount*10/vol and regression vs bak_daily.avg_price (post-2020)."""
import sys, os, glob
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))


def test_vwap_derivation_units(tmp_path):
    # amount=1200 千元 = 1,200,000 元 ; vol=12 手 = 1200 shares ; vwap = 1000 元/share
    # formula amount*10/vol = 1200*10/12 = 1000 ✓
    d = tmp_path / "daily" / "ts_code=000001.SZ"; d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": "000001.SZ", "trade_date": "20260101", "open": 990, "high": 1010,
         "low": 985, "close": 1000, "pre_close": 995, "change": 5, "pct_chg": 0.5,
         "vol": 12.0, "amount": 1200.0}
    ]), d / "data.parquet")
    a = tmp_path / "adj_factor" / "ts_code=000001.SZ"; a.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": "000001.SZ", "trade_date": "20260101", "adj_factor": 1.0}
    ]), a / "data.parquet")
    from alpha101.panel_loader import load_panel
    p = load_panel(["000001.SZ"], "20260101", data_root=str(tmp_path), lookback_days=5)
    assert p is not None
    assert p.vwap.iloc[-1]["000001.SZ"] == pytest.approx(1000.0)


def test_vwap_regression_vs_bak_daily():
    ts = os.environ.get("TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2")
    bak = Path(ts) / "bak_daily"
    if not bak.exists():
        pytest.skip("bak_daily not available")
    found = None
    # bak_daily has ~1827 trade_date= partitions; many recent ones are empty
    # placeholders. Scan from newest backward using metadata row-count (fast,
    # no data read) to find the latest non-empty partition.
    parts = sorted(glob.glob(str(bak / "trade_date=*")), reverse=True)
    for p in parts:
        pf = Path(p) / "data.parquet"
        if not pf.exists():
            continue
        try:
            nrows = pq.ParquetFile(pf).metadata.num_rows
        except Exception:
            continue
        if nrows > 0:
            found = Path(p).name.replace("trade_date=", "")
            break
    if not found:
        pytest.skip("no non-empty bak_daily partition")
    bdf = pd.read_parquet(bak / f"trade_date={found}" / "data.parquet")
    bdf = bdf[(bdf["vol"] > 0) & bdf["avg_price"].notna()]
    if bdf.empty:
        pytest.skip("bak_daily has no usable rows")
    sample = bdf.head(20)
    from alpha101.panel_loader import load_panel
    p = load_panel(sample["ts_code"].tolist(), found, data_root=ts, lookback_days=5)
    if p is None:
        pytest.skip("panel load failed")
    derived = p.vwap.iloc[-1]
    for _, row in sample.iterrows():
        if row["ts_code"] in derived.index:
            assert abs(derived[row["ts_code"]] - row["avg_price"]) < 0.05, (
                f"{row['ts_code']}: derived={derived[row['ts_code']]} vs bak={row['avg_price']}")
