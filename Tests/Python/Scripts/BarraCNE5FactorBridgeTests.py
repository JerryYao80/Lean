import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "barra_cne5_factor_bridge.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("barra_cne5_factor_bridge", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5FactorBridgeTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def test_resolve_factor_worker_count_auto_uses_up_to_four_workers_for_large_single_day_snapshot(self):
        module = load_module()
        with mock.patch.object(module.os, "cpu_count", return_value=8):
            worker_count = module.resolve_factor_worker_count(
                {"factor-worker-count": "auto"},
                trading_dates=["20240131"],
                universe_size=5181,
            )
        self.assertEqual(worker_count, 4)

    def test_chunk_trading_dates_splits_range_for_parallel_blocks(self):
        module = load_module()
        blocks = module.chunk_trading_dates(
            ["20250102", "20250103", "20250106", "20250107", "20250108", "20250109"],
            block_size=2,
        )
        self.assertEqual(
            blocks,
            [["20250102", "20250103"], ["20250106", "20250107"], ["20250108", "20250109"]],
        )

    def test_run_factor_bridge_writes_symbol_factor_files(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            output_root = root / "output"
            report_path = root / "report.json"

            stock_basic_rows = [
                {"ts_code": "600000.SH", "name": "浦发银行", "list_status": "L", "market": "主板", "list_date": "19991110"},
                {"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "market": "主板", "list_date": "19910403"},
            ]
            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", stock_basic_rows)
            self.write_parquet(tushare_root / "trade_cal" / "data.parquet", [
                {"cal_date": "20240131", "is_open": 1},
            ])
            self.write_parquet(tushare_root / "shibor" / "year=2024" / "data.parquet", [
                {"date": "20240131", "1y": 2.0},
            ])

            market_rows = []
            for day in range(1, 261):
                trade_date = (pd.Timestamp("2023-05-17") + pd.Timedelta(days=day - 1)).strftime("%Y%m%d")
                market_rows.append({
                    "ts_code": "000300.SH",
                    "trade_date": trade_date,
                    "pre_close": 1000 + day - 1,
                    "close": 1000 + day,
                    "pct_chg": 0.1,
                })
            self.write_parquet(tushare_root / "index_daily" / "ts_code=000300.SH" / "data.parquet", market_rows)

            for symbol, bias in [("600000.SH", 0.2), ("000001.SZ", -0.1)]:
                daily_rows = []
                daily_basic_rows = []
                for day in range(1, 261):
                    trade_date = (pd.Timestamp("2023-05-17") + pd.Timedelta(days=day - 1)).strftime("%Y%m%d")
                    close = 10 + bias * day / 100 + day / 200
                    daily_rows.append({
                        "ts_code": symbol,
                        "trade_date": trade_date,
                        "pre_close": close - 0.05,
                        "close": close,
                        "pct_chg": 0.2 + bias,
                        "vol": 100000 + day * 100,
                    })
                    daily_basic_rows.append({
                        "ts_code": symbol,
                        "trade_date": trade_date,
                        "total_mv": 1000000 + day * 1000,
                        "float_share": 100000 + day * 10,
                        "turnover_rate_f": 1.5 + bias,
                    })
                self.write_parquet(tushare_root / "daily" / f"ts_code={symbol}" / "data.parquet", daily_rows)
                self.write_parquet(tushare_root / "daily_basic" / f"ts_code={symbol}" / "data.parquet", daily_basic_rows)

            config = {
                "tushare-data-path": str(tushare_root),
                "output-path": str(output_root),
                "report-file": str(report_path),
                "symbols": ["600000.SH", "000001.SZ"],
                "date": "20240131",
                "market-symbol": "000300.SH",
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                report = module.run_factor_bridge(module.load_pipeline_config(config_path))

            file_a = output_root / "sse" / "daily" / "600000.csv"
            file_b = output_root / "szse" / "daily" / "000001.csv"
            self.assertEqual(report["written_symbol_count"], 2)
            self.assertTrue(file_a.exists())
            self.assertTrue(file_b.exists())
            text = file_a.read_text(encoding="utf-8")
            self.assertIn("trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st", text)
            stdout_text = output.getvalue()
            self.assertIn("Building Barra CNE5 factors for 2 symbols", stdout_text)
            self.assertIn("[build 20240131]", stdout_text)
            self.assertIn("[write files]", stdout_text)

    def test_run_factor_bridge_imports_external_factor_files_with_aliases(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "external"
            output_root = root / "normalized"
            report_path = root / "report.json"
            input_root.mkdir(parents=True, exist_ok=True)

            pd.DataFrame([
                {
                    "date": "2024-01-31",
                    "ticker": "600000",
                    "beta": -0.5,
                    "rstr": 1.2,
                    "lncap": -0.1,
                    "earnings_yield": 0.8,
                    "residual_volatility": -0.3,
                    "growth": 0.2,
                    "book_to_price": 0.4,
                    "lev": -0.2,
                    "liq": 0.1,
                    "non_linear_size": 0.05,
                    "market_cap": 1234567,
                }
            ]).to_csv(input_root / "barra_snapshot.csv", index=False)

            config = {
                "factor-source-mode": "import",
                "external-factor-path": str(input_root),
                "tushare-data-path": str(root / "unused_tushare"),
                "output-path": str(output_root),
                "report-file": str(report_path),
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
            }
            output = io.StringIO()
            with redirect_stdout(output):
                report = module.run_factor_bridge(module.load_pipeline_config(overrides=config))

            file_a = output_root / "sse" / "daily" / "600000.csv"
            self.assertEqual(report["mode"], "import")
            self.assertTrue(file_a.exists())
            frame = pd.read_csv(file_a, dtype={"trade_date": str})
            self.assertEqual(frame.loc[0, "trade_date"], "20240131")
            self.assertAlmostEqual(float(frame.loc[0, "earnyld"]), 0.8, places=6)
            self.assertEqual(int(frame.loc[0, "missing_factor_count"]), 0)
            stdout_text = output.getvalue()
            self.assertIn("Importing external Barra CNE5 factors", stdout_text)
            self.assertIn("[import files]", stdout_text)
            self.assertIn("[import write]", stdout_text)

    def test_run_factor_bridge_random_mode_writes_eligible_factor_files(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_root = root / "output"
            report_path = root / "report.json"

            config = {
                "factor-source-mode": "random",
                "tushare-data-path": str(root / "unused_tushare"),
                "output-path": str(output_root),
                "report-file": str(report_path),
                "symbols": ["600000.SH", "000001.SZ"],
                "date": "20240131",
                "random-factor-seed": 7,
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
            }

            output = io.StringIO()
            with redirect_stdout(output):
                report = module.run_factor_bridge(module.load_pipeline_config(overrides=config))

            file_a = output_root / "sse" / "daily" / "600000.csv"
            file_b = output_root / "szse" / "daily" / "000001.csv"
            self.assertEqual(report["mode"], "random")
            self.assertEqual(report["random_factor_seed"], 7)
            self.assertEqual(report["written_symbol_count"], 2)
            self.assertTrue(file_a.exists())
            self.assertTrue(file_b.exists())

            frame = pd.read_csv(file_a, dtype={"trade_date": str})
            self.assertEqual(frame.loc[0, "trade_date"], "20240131")
            self.assertEqual(int(frame.loc[0, "missing_factor_count"]), 0)
            self.assertEqual(int(frame.loc[0, "is_st"]), 0)
            self.assertGreater(float(frame.loc[0, "total_mv"]), 0.0)
            self.assertGreater(float(frame.loc[0, "turnover_rate"]), 0.0)
            self.assertGreaterEqual(int(frame.loc[0, "listed_days"]), 600)
            self.assertTrue(frame[module.FACTOR_COLUMNS].notna().all(axis=None))

            second_report = module.run_factor_bridge(module.load_pipeline_config(overrides=config))
            second_frame = pd.read_csv(file_a, dtype={"trade_date": str})
            self.assertEqual(second_report["mode"], "random")
            pd.testing.assert_frame_equal(frame, second_frame, check_dtype=False)

            stdout_text = output.getvalue()
            self.assertIn("Generating random Barra CNE5 factors", stdout_text)
            self.assertIn("[random generate]", stdout_text)
            self.assertIn("[write files]", stdout_text)


if __name__ == "__main__":
    unittest.main()
