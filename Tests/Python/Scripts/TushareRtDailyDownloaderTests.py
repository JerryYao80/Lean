import importlib.util
import sys
import unittest.mock as mock
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import tempfile

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "data-source" / "tushare" / "rt_daily_downloader.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("rt_daily_downloader", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TushareRtDailyDownloaderTests(unittest.TestCase):
    def test_is_etf_code_distinguishes_stock_and_etf(self):
        module = load_module()
        client = module.TushareRtDailyClient

        self.assertTrue(client.is_etf_code("510300.SH"))
        self.assertTrue(client.is_etf_code("159915.SZ"))
        self.assertTrue(client.is_etf_code("511880.SH"))

        self.assertFalse(client.is_etf_code("600000.SH"))
        self.assertFalse(client.is_etf_code("000001.SZ"))
        self.assertFalse(client.is_etf_code("300750.SZ"))

    def test_fetch_quotes_splits_requests_into_minute_windows(self):
        module = load_module()
        client = module.TushareRtDailyClient(
            token="test-token",
            batch_size=1,
            max_workers=2,
            max_requests_per_minute=2,
            verbose=False,
        )
        events = []

        def fake_fetch_quote_batch_task(request_index, symbols, api_name, trade_date):
            return {
                "request_index": request_index,
                "symbols": list(symbols),
                "trade_date": trade_date,
                "api_name": api_name,
                "data": pd.DataFrame([
                    {"ts_code": ts_code, "trade_date": trade_date, "close": 10.0 + request_index, "pct_chg": 1.0}
                    for ts_code in symbols
                ]),
                "error": None,
            }

        with mock.patch.object(client, "_fetch_quote_batch_task", side_effect=fake_fetch_quote_batch_task):
            with mock.patch.object(module.time, "sleep", return_value=None):
                frame = client.fetch_quotes(
                    ["000001.SZ", "000002.SZ", "510300.SH"],
                    trade_date="20260316",
                    progress_callback=events.append,
                )

        self.assertEqual(len(frame.index), 3)
        self.assertEqual(frame["ts_code"].tolist(), ["000001.SZ", "000002.SZ", "510300.SH"])
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[0]["minute_window_count"], 2)
        self.assertEqual(events[0]["max_requests_per_minute"], 2)
        self.assertEqual([event["event"] for event in events].count("minute_window_start"), 2)
        self.assertEqual([event["event"] for event in events].count("minute_window_wait"), 1)
        self.assertEqual(events[-1]["event"], "finish")

    def test_gbm_synthetic_client_generates_incremental_quotes(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_root = root / "tushare"
            daily_path = data_root / "daily" / "ts_code=000001.SZ" / "data.parquet"
            daily_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([
                {"trade_date": "20260105", "open": 9.80, "high": 10.10, "low": 9.70, "close": 10.00, "vol": 1000000, "amount": 10000000},
                {"trade_date": "20260106", "open": 10.00, "high": 10.30, "low": 9.95, "close": 10.20, "vol": 1100000, "amount": 11220000},
                {"trade_date": "20260107", "open": 10.20, "high": 10.40, "low": 10.10, "close": 10.35, "vol": 1200000, "amount": 12420000},
                {"trade_date": "20260108", "open": 10.35, "high": 10.55, "low": 10.30, "close": 10.50, "vol": 1300000, "amount": 13650000},
                {"trade_date": "20260109", "open": 10.50, "high": 10.80, "low": 10.45, "close": 10.70, "vol": 1250000, "amount": 13375000},
            ]).to_parquet(daily_path, index=False)

            client = module.GbmSyntheticRtDailyClient(
                tushare_data_path=data_root,
                batch_size=50,
                poll_interval_seconds=60,
                lookback_days=5,
                min_history_days=2,
                random_seed=7,
                volatility_scale=8.0,
                min_daily_volatility=0.80,
                jump_probability=0.22,
                jump_scale=0.10,
                verbose=False,
            )

            with mock.patch.object(module, "datetime") as mocked_datetime:
                mocked_datetime.now.side_effect = [
                    datetime(2026, 3, 12, 20, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
                    for minute in range(10)
                ]
                mocked_datetime.side_effect = datetime
                frames = [
                    client.fetch_quotes(["000001.SZ"], trade_date="20260312")
                    for _ in range(10)
                ]

            first = frames[0]
            second = frames[1]
            max_abs_pct = max(abs(float(frame.iloc[0]["pct_chg"])) for frame in frames)
            self.assertEqual(first.iloc[0]["source_api"], "sim_rt_k")
            self.assertEqual(second.iloc[0]["source_api"], "sim_rt_k")
            self.assertGreater(float(second.iloc[0]["vol"]), float(first.iloc[0]["vol"]))
            self.assertNotEqual(float(second.iloc[0]["close"]), float(first.iloc[0]["close"]))
            self.assertGreaterEqual(float(second.iloc[0]["high"]), float(second.iloc[0]["close"]))
            self.assertLessEqual(float(second.iloc[0]["low"]), float(second.iloc[0]["close"]))
            self.assertGreater(max_abs_pct, 5.0)
            self.assertEqual(client.last_fetch_metadata["volatility_scale"], 8.0)


if __name__ == "__main__":
    unittest.main()
