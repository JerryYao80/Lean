import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "tushare_lean_export.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("tushare_lean_export", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TushareLeanExportTests(unittest.TestCase):
    def make_registry_file(self, root: Path) -> Path:
        content = '''
{ "510300", new AShareETFMetadata { Ticker = "510300", Name = "沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
{ "511880", new AShareETFMetadata { Ticker = "511880", Name = "银华日利", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
{ "159001", new AShareETFMetadata { Ticker = "159001", Name = "保证金货币", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
{ "159919", new AShareETFMetadata { Ticker = "159919", Name = "嘉实沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
{ "513020", new AShareETFMetadata { Ticker = "513020", Name = "港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
{ "510180", new AShareETFMetadata { Ticker = "510180", Name = "180ETF", TradingMode = ETFTradingMode.T1, Market = "SSE" } },
'''
        path = root / "AShareETFMetadata.cs"
        path.write_text(content, encoding="utf-8")
        return path

    def make_parquet(self, tushare_root: Path, ts_code: str, rows: list[dict]) -> None:
        target = tushare_root / "fund_daily" / f"ts_code={ts_code}"
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(target / "data.parquet", index=False)

    def read_zip_rows(self, zip_path: Path) -> list[str]:
        with zipfile.ZipFile(zip_path, "r") as archive:
            entry = archive.namelist()[0]
            with archive.open(entry) as handle:
                return handle.read().decode("utf-8").strip().splitlines()

    def test_load_registry_universe_honors_money_market_filter(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_registry_file(Path(temp_dir))

            all_symbols = module.load_registry_universe(registry, exclude_money_market=False)
            filtered = module.load_registry_universe(registry, exclude_money_market=True)

        self.assertEqual(all_symbols, ["159001.SZ", "159919.SZ", "510300.SH", "511880.SH", "513020.SH"])
        self.assertEqual(filtered, ["159919.SZ", "510300.SH", "513020.SH"])

    def test_build_export_rows_scales_prices_filters_dates_and_sorts(self):
        module = load_module()
        frame = pd.DataFrame([
            {"trade_date": "20240103", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 10.5},
            {"trade_date": "20240101", "open": 0.9, "high": 1.0, "low": 0.8, "close": 0.95, "vol": 8.0},
            {"trade_date": "20240102", "open": 1.01, "high": 1.03, "low": 0.99, "close": 1.02, "vol": 9.25},
        ])

        rows = module.build_export_rows(frame, start_date="20240102", end_date="20240103")

        self.assertEqual(rows, [
            "20240102 00:00,10100,10300,9900,10200,925",
            "20240103 00:00,11000,12000,10000,11500,1050",
        ])

    def test_collect_coverage_report_flags_missing_parquet_and_missing_exports(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = self.make_registry_file(root)
            tushare_root = root / "tushare"
            lean_root = root / "lean"

            self.make_parquet(tushare_root, "510300.SH", [{"trade_date": "20240102", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}])
            self.make_parquet(tushare_root, "159919.SZ", [{"trade_date": "20240102", "open": 2, "high": 2, "low": 2, "close": 2, "vol": 2}])

            (lean_root / "equity" / "sse" / "daily").mkdir(parents=True, exist_ok=True)
            (lean_root / "equity" / "sse" / "daily" / "510300.zip").write_bytes(b"placeholder")

            report = module.collect_coverage_report(
                registry_file=registry,
                tushare_data_path=tushare_root,
                lean_data_path=lean_root,
                start_date="20240101",
                end_date="20240131",
                exclude_money_market=True,
            )

        self.assertEqual(report["registry_symbol_count"], 3)
        self.assertEqual(report["parquet_available_count"], 2)
        self.assertEqual(report["lean_export_count"], 1)
        self.assertEqual(report["missing_parquet_symbols"], ["513020.SH"])
        self.assertEqual(report["missing_lean_export_symbols"], ["159919.SZ"])

    def test_export_registry_universe_writes_zip_files_and_json_report(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = self.make_registry_file(root)
            tushare_root = root / "tushare"
            lean_root = root / "lean"
            report_path = root / "report.json"

            self.make_parquet(tushare_root, "510300.SH", [
                {"trade_date": "20240102", "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "vol": 3.5},
                {"trade_date": "20240103", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "vol": 4.5},
            ])
            self.make_parquet(tushare_root, "159919.SZ", [
                {"trade_date": "20240102", "open": 2.0, "high": 2.2, "low": 1.9, "close": 2.1, "vol": 6.0},
            ])
            self.make_parquet(tushare_root, "513020.SH", [
                {"trade_date": "20240102", "open": 3.0, "high": 3.3, "low": 2.9, "close": 3.1, "vol": 8.0},
            ])

            config = {
                "registry-file": str(registry),
                "tushare-data-path": str(tushare_root),
                "lean-data-path": str(lean_root),
                "start-date": "20240101",
                "end-date": "20240131",
                "exclude-money-market-etfs": True,
                "report-file": str(report_path),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            report = module.export_registry_universe(module.load_pipeline_config(config_path))

            zip_rows = self.read_zip_rows(lean_root / "equity" / "sse" / "daily" / "510300.zip")
            report_from_disk = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(zip_rows, [
            "20240102 00:00,10000,11000,9000,10500,350",
            "20240103 00:00,11000,12000,10000,11500,450",
        ])
        self.assertEqual(report["missing_parquet_symbols"], [])
        self.assertEqual(report["missing_lean_export_symbols"], [])
        self.assertEqual(report_from_disk["lean_export_count"], 3)


if __name__ == "__main__":
    unittest.main()
