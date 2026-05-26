import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "export_price_ohlc_to_influx.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("export_price_ohlc_to_influx", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PriceOhlcInfluxExportTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def test_trade_date_timestamp_uses_china_close_time(self):
        module = load_module()

        timestamp_ns = module.trade_date_to_timestamp_ns("20260417")

        self.assertEqual(timestamp_ns, 1776409200000000000)

    def test_load_tushare_daily_records_filters_dates_and_scales_volume(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_parquet(root / "daily" / "ts_code=000001.SZ" / "data.parquet", [
                {"ts_code": "000001.SZ", "trade_date": "20260415", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.5, "vol": 1000.0},
                {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 11.0, "high": 11.6, "low": 10.8, "close": 11.3, "vol": 1200.5},
            ])

            records = module.load_tushare_daily_records(
                root,
                symbols=["000001.SZ"],
                start_date="20260416",
                end_date="20260417",
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "tushare_daily")
        self.assertEqual(records[0].symbol, "000001.SZ")
        self.assertEqual(records[0].trade_date, "20260417")
        self.assertEqual(records[0].volume, 120050.0)

    def test_load_live_archive_records_keeps_latest_fetch_per_symbol_date(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_parquet(root / "date=20260417" / "quotes.parquet", [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260417",
                    "open": 8.1,
                    "high": 8.4,
                    "low": 8.0,
                    "close": 8.2,
                    "vol": 10,
                    "fetch_timestamp": "2026-04-17T10:00:00+08:00",
                    "source_api": "sim_rt_k",
                },
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260417",
                    "open": 8.1,
                    "high": 8.8,
                    "low": 7.9,
                    "close": 8.7,
                    "vol": 12,
                    "fetch_timestamp": "2026-04-17T10:01:00+08:00",
                    "source_api": "sim_rt_k",
                },
            ])

            records = module.load_live_archive_records(root, symbols=["600000.SH"])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "live_archive")
        self.assertEqual(records[0].close, 8.7)
        self.assertEqual(records[0].high, 8.8)
        self.assertEqual(records[0].volume, 1200.0)
        self.assertEqual(records[0].source_api, "sim_rt_k")

    def test_record_to_line_protocol_uses_candlestick_field_names(self):
        module = load_module()

        record = module.PriceOhlcRecord(
            symbol="000001.SZ",
            trade_date="20260417",
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=123400.0,
            source="tushare_daily",
            source_api=None,
            amount=123456.7,
            pre_close=9.9,
            pct_chg=3.03,
        )

        line = module.record_to_line_protocol(record)

        self.assertTrue(line.startswith("lean_price_ohlc,source=tushare_daily,symbol=000001.SZ "))
        self.assertIn("open=10", line)
        self.assertIn("high=10.5", line)
        self.assertIn("low=9.8", line)
        self.assertIn("close=10.2", line)
        self.assertIn("volume=123400", line)
        self.assertTrue(line.endswith(" 1776409200000000000"))

    def test_collect_price_ohlc_records_supports_all_sources(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            archive_root = root / "archive"
            self.write_parquet(tushare_root / "daily" / "ts_code=000001.SZ" / "data.parquet", [
                {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "vol": 10},
            ])
            self.write_parquet(archive_root / "date=20260417" / "quotes.parquet", [
                {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2, "vol": 11, "source_api": "sim_rt_k"},
            ])

            records = module.collect_price_ohlc_records(
                source="all",
                tushare_data_path=tushare_root,
                archive_path=archive_root,
                symbols=["000001.SZ"],
            )

        self.assertEqual([record.source for record in records], ["live_archive", "tushare_daily"])

    def test_influx_token_has_no_hardcoded_secret_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            module = load_module()

        self.assertIsNone(module.DEFAULT_INFLUX_TOKEN)
        with self.assertRaises(ValueError):
            module.resolve_influx_token(None)
        self.assertEqual(module.resolve_influx_token("env-token"), "env-token")


if __name__ == "__main__":
    unittest.main()
