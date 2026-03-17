import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo
from datetime import datetime

import pandas as pd


def load_module(module_name: str, relative_path: str):
    module_path = Path(__file__).resolve().parents[3] / relative_path
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5LiveBridgeTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def write_factor_csv(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, index=False)

    def test_resolve_tushare_token_falls_back_to_runtime_config(self):
        live_bridge = load_module("barra_cne5_live_bridge_token", "Scripts/barra_cne5_live_bridge.py")

        with mock.patch.dict("os.environ", {}, clear=True):
            token = live_bridge.resolve_tushare_token({"tushare-token": "", "tushare-token-env-var": "TUSHARE_TOKEN"})

        self.assertEqual(token, live_bridge.tushare_runtime_config.TUSHARE_TOKEN)

    def test_build_quote_refresh_plan_refreshes_full_universe_each_cycle(self):
        live_bridge = load_module("barra_cne5_live_bridge_refresh_plan", "Scripts/barra_cne5_live_bridge.py")

        universe = ["000001.SZ", "000002.SZ", "000063.SZ", "600000.SH", "600519.SH"]
        refresh = live_bridge.build_quote_refresh_plan(universe, 2, {}, "20260316")

        self.assertEqual(refresh["refresh_symbols"], universe)
        self.assertEqual(refresh["refresh_count"], len(universe))
        self.assertEqual(refresh["estimated_full_refresh_minutes"], 1)

    def test_merge_live_quotes_carries_forward_previous_snapshot_rows(self):
        live_bridge = load_module("barra_cne5_live_bridge_merge_quotes", "Scripts/barra_cne5_live_bridge.py")

        previous = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20260316", "close": 10.0, "fetch_timestamp": "2026-03-16T10:00:00+08:00", "source_api": "rt_k"},
            {"ts_code": "000002.SZ", "trade_date": "20260316", "close": 20.0, "fetch_timestamp": "2026-03-16T10:00:00+08:00", "source_api": "rt_k"},
        ])
        fresh = pd.DataFrame([
            {"ts_code": "000002.SZ", "trade_date": "20260316", "close": 21.0, "fetch_timestamp": "2026-03-16T10:01:00+08:00", "source_api": "rt_k"},
        ])

        merged = live_bridge.merge_live_quotes(["000001.SZ", "000002.SZ"], "20260316", previous, fresh)
        merged = merged.sort_values("ts_code").reset_index(drop=True)

        self.assertEqual(merged["ts_code"].tolist(), ["000001.SZ", "000002.SZ"])
        self.assertEqual(merged["close"].tolist(), [10.0, 21.0])

    def test_resolve_market_data_context_switches_to_simulation_after_hours(self):
        live_bridge = load_module("barra_cne5_live_bridge_mode", "Scripts/barra_cne5_live_bridge.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            self.write_parquet(tushare_root / "trade_cal" / "data.parquet", [
                {"cal_date": "20260312", "is_open": 1},
            ])
            config = live_bridge.load_live_bridge_config(overrides={
                "tushare-data-path": str(tushare_root),
                "market-symbol": "000300.SH",
                "live-price-source-mode": "auto",
            })

            context = live_bridge.resolve_market_data_context(
                config,
                now=datetime(2026, 3, 12, 20, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

            self.assertEqual(context["selected_mode"], "gbm-simulated")
            self.assertEqual(context["session_state"], "after-hours")
            self.assertIn("market closed", context["reason"])

    def test_normalize_live_quotes_preserves_simulated_source_api(self):
        live_bridge = load_module("barra_cne5_live_bridge_norm", "Scripts/barra_cne5_live_bridge.py")

        frame = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260312",
                "open": 10.0,
                "high": 10.3,
                "low": 9.9,
                "close": 10.2,
                "price": 10.2,
                "pre_close": 10.0,
                "pct_chg": 2.0,
                "vol": 12345,
                "amount": 125000,
                "fetch_timestamp": "2026-03-12T20:15:00+08:00",
                "source_api": "sim_rt_k",
            },
        ])

        normalized = live_bridge.normalize_live_quotes(
            frame,
            "20260312",
            datetime(2026, 3, 12, 20, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        self.assertEqual(normalized.iloc[0]["source_api"], "sim_rt_k")

    def test_live_bridge_once_generates_current_snapshot_for_csi300_universe(self):
        live_bridge = load_module("barra_cne5_live_bridge", "Scripts/barra_cne5_live_bridge.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            output_root = root / "live_factors"
            report_path = root / "live_bridge_report.json"
            snapshot_path = root / "live_price_snapshot.json"
            archive_root = root / "daily_quote_archive"

            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", [
                {"ts_code": "600000.SH", "name": "浦发银行", "list_status": "L", "market": "主板", "list_date": "19991110"},
                {"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "market": "主板", "list_date": "19910403"},
                {"ts_code": "300750.SZ", "name": "宁德时代", "list_status": "L", "market": "创业板", "list_date": "20180611"},
            ])
            self.write_parquet(tushare_root / "trade_cal" / "data.parquet", [
                {"cal_date": "20260312", "is_open": 1},
            ])
            self.write_parquet(tushare_root / "index_daily" / "ts_code=000300.SH" / "data.parquet", [
                {"ts_code": "000300.SH", "trade_date": "20260312", "close": 3998.12, "pct_chg": 0.56, "vol": 123456789, "amount": 987654321},
            ])
            self.write_parquet(tushare_root / "index_weight" / "date=20260312" / "data.parquet", [
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "600000.SH", "weight": 1.0},
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "000001.SZ", "weight": 1.0},
            ])
            self.write_parquet(tushare_root / "daily" / "ts_code=600000.SH" / "data.parquet", [
                {"ts_code": "600000.SH", "trade_date": "20260312", "close": 10.25, "pct_chg": 1.12, "vol": 1000000, "amount": 11000000},
            ])
            self.write_parquet(tushare_root / "daily" / "ts_code=000001.SZ" / "data.parquet", [
                {"ts_code": "000001.SZ", "trade_date": "20260312", "close": 12.34, "pct_chg": -0.45, "vol": 2000000, "amount": 25000000},
            ])
            self.write_parquet(tushare_root / "daily_basic" / "ts_code=600000.SH" / "data.parquet", [
                {"ts_code": "600000.SH", "trade_date": "20260312", "turnover_rate": 0.88, "total_mv": 123456789000},
            ])
            self.write_parquet(tushare_root / "daily_basic" / "ts_code=000001.SZ" / "data.parquet", [
                {"ts_code": "000001.SZ", "trade_date": "20260312", "turnover_rate": 1.23, "total_mv": 223456789000},
            ])

            class FakeQuoteClient:
                def fetch_quotes(self, ts_codes, trade_date=None):
                    requested = set(ts_codes)
                    assert requested == {"600000.SH", "000001.SZ"}
                    return pd.DataFrame([
                        {
                            "ts_code": "600000.SH",
                            "trade_date": trade_date,
                            "open": 10.10,
                            "high": 10.40,
                            "low": 10.00,
                            "close": 10.25,
                            "pre_close": 10.14,
                            "pct_chg": 1.08,
                            "vol": 1000000,
                            "amount": 11000000,
                        },
                        {
                            "ts_code": "000001.SZ",
                            "trade_date": trade_date,
                            "open": 12.40,
                            "high": 12.58,
                            "low": 12.20,
                            "close": 12.34,
                            "pre_close": 12.28,
                            "pct_chg": 0.49,
                            "vol": 2000000,
                            "amount": 25000000,
                        },
                    ])

            config = live_bridge.load_live_bridge_config(overrides={
                "tushare-data-path": str(tushare_root),
                "factor-data-path": str(output_root),
                "live-factor-report-file": str(report_path),
                "live-price-snapshot-file": str(snapshot_path),
                "daily-quote-archive-path": str(archive_root),
                "factor-source-mode": "random",
                "random-factor-seed": 17,
                "universe": "csi300",
                "index-code": "000300.SH",
                "market-symbol": "000300.SH",
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
                "live-factor-poll-interval-seconds": 1,
            })

            trade_date = live_bridge.resolve_live_trade_date(
                config,
                now=datetime(2026, 3, 12, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertEqual(trade_date, "20260312")

            with mock.patch.object(live_bridge, "resolve_live_trade_date", return_value="20260312"):
                returncode = live_bridge.run_live_bridge(config, once=True, quote_client=FakeQuoteClient())

            self.assertEqual(returncode, 0)
            self.assertTrue((output_root / "sse" / "daily" / "600000.csv").exists())
            self.assertTrue((output_root / "szse" / "daily" / "000001.csv").exists())
            self.assertFalse((output_root / "szse" / "daily" / "300750.csv").exists())
            self.assertTrue(snapshot_path.exists())
            self.assertTrue((archive_root / "date=20260312" / "quotes.parquet").exists())

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["trade_date"], "20260312")
            self.assertEqual(report["bridge_report"]["mode"], "random")
            self.assertEqual(report["bridge_report"]["written_symbol_count"], 2)
            self.assertEqual(report["live_quote_report"]["received_quote_count"], 2)
            self.assertEqual(report["live_quote_report"]["missing_quote_count"], 0)
            self.assertEqual(report["live_quote_report"]["source_api_counts"]["rt_k"], 2)
            self.assertEqual(report["market_preview"]["trade_date"], "20260312")
            self.assertEqual(len(report["market_preview"]["rows"]), 2)
            self.assertEqual(report["market_preview"]["rows"][0]["source_api"], "rt_k")

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["trade_date"], "20260312")
            self.assertEqual(snapshot["quote_count"], 2)
            self.assertEqual(snapshot["quotes"][0]["ts_code"], "000001.SZ")
            self.assertEqual(snapshot["quotes"][1]["ts_code"], "600000.SH")

    def test_live_bridge_auto_mode_seeds_live_factors_from_external_history(self):
        live_bridge = load_module("barra_cne5_live_bridge_external_seed", "Scripts/barra_cne5_live_bridge.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            output_root = root / "live_factors"
            external_root = root / "historical_factors"
            report_path = root / "live_bridge_report.json"
            snapshot_path = root / "live_price_snapshot.json"
            archive_root = root / "daily_quote_archive"

            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", [
                {"ts_code": "600000.SH", "name": "浦发银行", "list_status": "L", "market": "主板", "list_date": "19991110"},
                {"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "market": "主板", "list_date": "19910403"},
            ])
            self.write_parquet(tushare_root / "trade_cal" / "data.parquet", [
                {"cal_date": "20260312", "is_open": 1},
            ])
            self.write_parquet(tushare_root / "index_daily" / "ts_code=000300.SH" / "data.parquet", [
                {"ts_code": "000300.SH", "trade_date": "20260312", "close": 3998.12, "pct_chg": 0.56, "vol": 123456789, "amount": 987654321},
            ])
            self.write_parquet(tushare_root / "index_weight" / "date=20260312" / "data.parquet", [
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "600000.SH", "weight": 1.0},
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "000001.SZ", "weight": 1.0},
            ])
            self.write_factor_csv(external_root / "sse" / "daily" / "600000.csv", [
                {
                    "trade_date": "20260311",
                    "beta": 0.11,
                    "momentum": 0.21,
                    "size": 0.31,
                    "earnyld": 0.41,
                    "resvol": 0.51,
                    "growth": 0.61,
                    "btop": 0.71,
                    "leverage": 0.81,
                    "liquidity": 0.91,
                    "nlsize": 1.01,
                    "total_mv": 1000,
                    "turnover_rate": 1.5,
                    "listed_days": 5000,
                    "missing_factor_count": 0,
                    "is_st": 0,
                },
            ])
            self.write_factor_csv(external_root / "szse" / "daily" / "000001.csv", [
                {
                    "trade_date": "20260312",
                    "beta": -0.11,
                    "momentum": -0.21,
                    "size": -0.31,
                    "earnyld": -0.41,
                    "resvol": -0.51,
                    "growth": -0.61,
                    "btop": -0.71,
                    "leverage": -0.81,
                    "liquidity": -0.91,
                    "nlsize": -1.01,
                    "total_mv": 2000,
                    "turnover_rate": 2.5,
                    "listed_days": 6000,
                    "missing_factor_count": 0,
                    "is_st": 0,
                },
            ])

            class FakeQuoteClient:
                def fetch_quotes(self, ts_codes, trade_date=None):
                    requested = set(ts_codes)
                    assert requested == {"600000.SH", "000001.SZ"}
                    return pd.DataFrame([
                        {
                            "ts_code": "600000.SH",
                            "trade_date": trade_date,
                            "open": 10.10,
                            "high": 10.40,
                            "low": 10.00,
                            "close": 10.25,
                            "pre_close": 10.14,
                            "pct_chg": 1.08,
                            "vol": 1000000,
                            "amount": 11000000,
                        },
                        {
                            "ts_code": "000001.SZ",
                            "trade_date": trade_date,
                            "open": 12.40,
                            "high": 12.58,
                            "low": 12.20,
                            "close": 12.34,
                            "pre_close": 12.28,
                            "pct_chg": 0.49,
                            "vol": 2000000,
                            "amount": 25000000,
                        },
                    ])

            config = live_bridge.load_live_bridge_config(overrides={
                "tushare-data-path": str(tushare_root),
                "factor-data-path": str(output_root),
                "external-factor-path": str(external_root),
                "live-factor-report-file": str(report_path),
                "live-price-snapshot-file": str(snapshot_path),
                "daily-quote-archive-path": str(archive_root),
                "factor-source-mode": "auto",
                "universe": "csi300",
                "index-code": "000300.SH",
                "market-symbol": "000300.SH",
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
                "live-factor-poll-interval-seconds": 1,
            })

            with mock.patch.object(live_bridge, "resolve_live_trade_date", return_value="20260312"):
                returncode = live_bridge.run_live_bridge(config, once=True, quote_client=FakeQuoteClient())

            self.assertEqual(returncode, 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["bridge_report"]["mode"], "external-seed")
            self.assertEqual(report["bridge_report"]["written_symbol_count"], 2)
            self.assertEqual(report["bridge_report"]["exact_trade_date_symbol_count"], 1)
            self.assertEqual(report["bridge_report"]["carry_forward_symbol_count"], 1)
            self.assertEqual(report["bridge_report"]["missing_symbol_count"], 0)

            seeded_frame = pd.read_csv(output_root / "sse" / "daily" / "600000.csv", dtype={"trade_date": str})
            self.assertIn("20260312", set(seeded_frame["trade_date"]))
            carried_row = seeded_frame[seeded_frame["trade_date"] == "20260312"].iloc[-1]
            self.assertAlmostEqual(float(carried_row["beta"]), 0.11)

            exact_frame = pd.read_csv(output_root / "szse" / "daily" / "000001.csv", dtype={"trade_date": str})
            self.assertIn("20260312", set(exact_frame["trade_date"]))
            exact_row = exact_frame[exact_frame["trade_date"] == "20260312"].iloc[-1]
            self.assertAlmostEqual(float(exact_row["beta"]), -0.11)


if __name__ == "__main__":
    unittest.main()
