import importlib.util
import sys
import unittest.mock as mock
import unittest
from pathlib import Path

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

        def fake_fetch_single_quote_task(request_index, ts_code, trade_date):
            return {
                "request_index": request_index,
                "ts_code": ts_code,
                "trade_date": trade_date,
                "api_name": "rt_etf_k" if client.is_etf_code(ts_code) else "rt_k",
                "batch_index": request_index,
                "data": pd.DataFrame([
                    {"ts_code": ts_code, "trade_date": trade_date, "close": 10.0 + request_index, "pct_chg": 1.0}
                ]),
                "error": None,
            }

        with mock.patch.object(client, "_fetch_single_quote_task", side_effect=fake_fetch_single_quote_task):
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


if __name__ == "__main__":
    unittest.main()
