import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_live_market_cache.py"
    spec = importlib.util.spec_from_file_location("ashare_live_market_cache_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareLiveMarketCacheTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def test_load_full_market_universe_combines_stocks_and_etfs(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", [
                {"ts_code": "600000.SH", "list_status": "L", "market": "主板", "list_date": "19991110"},
                {"ts_code": "300750.SZ", "list_status": "L", "market": "创业板", "list_date": "20180611"},
                {"ts_code": "830001.BJ", "list_status": "L", "market": "北交所", "list_date": "20210101"},
            ])
            self.write_parquet(tushare_root / "etf_basic" / "data.parquet", [
                {"ts_code": "510300.SH", "list_status": "L", "exchange": "SH", "list_date": "20120528"},
                {"ts_code": "159915.SZ", "list_status": "L", "exchange": "SZ", "list_date": "20111209"},
                {"ts_code": "999999.OF", "list_status": "L", "exchange": "OF", "list_date": "20100101"},
            ])

            universe = module.load_full_market_universe(tushare_root, "20260318")

        self.assertEqual(universe, ["159915.SZ", "300750.SZ", "510300.SH", "600000.SH"])

    def test_ensure_full_market_snapshot_writes_and_reuses_cached_snapshot(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            snapshot_path = root / "shared_snapshot.json"
            report_path = root / "shared_report.json"
            archive_root = root / "archive"
            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", [
                {"ts_code": "600000.SH", "list_status": "L", "market": "主板", "list_date": "19991110"},
            ])
            self.write_parquet(tushare_root / "etf_basic" / "data.parquet", [
                {"ts_code": "510300.SH", "list_status": "L", "exchange": "SH", "list_date": "20120528"},
            ])

            class FakeQuoteClient:
                def __init__(self):
                    self.calls = 0
                    self.last_fetch_metadata = {}

                def fetch_quotes(self, ts_codes, trade_date=None, progress_callback=None):
                    self.calls += 1
                    self.last_fetch_metadata = {"batch_count": 1, "minute_window_count": 1}
                    return pd.DataFrame([
                        {
                            "ts_code": "600000.SH",
                            "trade_date": trade_date,
                            "close": 10.25,
                            "price": 10.25,
                            "pre_close": 10.14,
                            "pct_chg": 1.08,
                            "vol": 1000000,
                            "amount": 11000000,
                            "source_api": "rt_k",
                        },
                        {
                            "ts_code": "510300.SH",
                            "trade_date": trade_date,
                            "close": 4.12,
                            "price": 4.12,
                            "pre_close": 4.07,
                            "pct_chg": 1.23,
                            "vol": 200000,
                            "amount": 820000,
                            "source_api": "rt_etf_k",
                        },
                    ])

            realtime_client = FakeQuoteClient()
            config = {
                "tushare-data-path": str(tushare_root),
                "shared-live-market-snapshot-file": str(snapshot_path),
                "shared-live-market-report-file": str(report_path),
                "shared-live-market-archive-path": str(archive_root),
                "shared-live-market-refresh-interval-seconds": 60,
                "shared-live-market-batch-size": 200,
                "shared-live-market-max-workers": 1,
                "shared-live-market-max-requests-per-minute": 50,
                "timezone": "Asia/Shanghai",
            }
            market_context = {
                "requested_mode": "realtime",
                "selected_mode": "tushare-realtime",
                "session_state": "regular-session-morning",
                "market_open": True,
                "reason": "test",
                "now": datetime(2026, 3, 18, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
            }

            result = module.ensure_full_market_snapshot(
                config,
                "20260318",
                market_context,
                realtime_client=realtime_client,
                simulated_client=None,
            )
            cached_result = module.ensure_full_market_snapshot(
                config,
                "20260318",
                {
                    **market_context,
                    "now": datetime(2026, 3, 18, 10, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                },
                realtime_client=realtime_client,
                simulated_client=None,
            )

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            archive_exists = (archive_root / "date=20260318" / "quotes.parquet").exists()

        self.assertEqual(realtime_client.calls, 1)
        self.assertFalse(result["used_cached_snapshot"])
        self.assertTrue(cached_result["used_cached_snapshot"])
        self.assertEqual(snapshot["quote_count"], 2)
        self.assertEqual(snapshot["requested_symbol_count"], 2)
        self.assertTrue(archive_exists)


if __name__ == "__main__":
    unittest.main()
