import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "export_ashare_etf_t0_feature_data.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("export_ashare_etf_t0_feature_data", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareEtfT0FeatureExportTests(unittest.TestCase):
    def make_catalog(self, root: Path) -> Path:
        catalog = {
            "datasets": {
                "fund_daily": {
                    "path": "fund_daily/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                }
            }
        }
        path = root / "catalog.json"
        path.write_text(json.dumps(catalog), encoding="utf-8")
        return path

    def make_registry(self, root: Path) -> Path:
        registry = root / "AShareETFMetadata.cs"
        registry.write_text(
            "\n".join(
                [
                    '{ "510300", new AShareETFMetadata { Ticker = "510300", Name = "沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
                    '{ "159919", new AShareETFMetadata { Ticker = "159919", Name = "嘉实沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },',
                    '{ "511880", new AShareETFMetadata { Ticker = "511880", Name = "银华日利", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
                ]
            ),
            encoding="utf-8",
        )
        return registry

    def make_parquet(self, root: Path, ts_code: str, rows: list[dict]) -> None:
        target = root / "fund_daily" / f"ts_code={ts_code}"
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(target / "data.parquet", index=False)

    def test_export_feature_universe_writes_shifted_signal_columns(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            feature_root = root / "features"
            report_path = root / "feature-report.json"
            config_path = root / "config.json"

            rows = []
            for day in range(1, 27):
                rows.append(
                    {
                        "ts_code": "510300.SH",
                        "trade_date": f"202401{day:02d}",
                        "pre_close": 0.99 + day * 0.01,
                        "open": 1.00 + day * 0.01,
                        "high": 1.02 + day * 0.01,
                        "low": 0.98 + day * 0.01,
                        "close": 1.01 + day * 0.01,
                        "pct_chg": 0.1 + day * 0.01,
                        "amount": 1000 + day * 10,
                        "vol": 100 + day,
                    }
                )

            self.make_parquet(tushare_root, "510300.SH", rows)

            config = {
                "registry-file": str(self.make_registry(root)),
                "tushare-data-path": str(tushare_root),
                "dataset-catalog": str(self.make_catalog(root)),
                "feature-data-path": str(feature_root),
                "start-date": "20240101",
                "end-date": "20240131",
                "exclude-money-market-etfs": True,
                "report-file": str(report_path),
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            report = module.export_feature_universe(module.load_pipeline_config(config_path))
            feature_frame = pd.read_csv(feature_root / "sse" / "daily" / "510300.csv", dtype={"trade_date": str})

        self.assertEqual(report["exported_count"], 1)
        self.assertIn("signal_momentum_20", feature_frame.columns)
        self.assertIn("signal_liquidity_5", feature_frame.columns)
        self.assertIn("momentum_5", feature_frame.columns)
        self.assertIn("trade_return", feature_frame.columns)

        current_row = feature_frame.loc[feature_frame["trade_date"] == "20240122"].iloc[0]
        previous_row = feature_frame.loc[feature_frame["trade_date"] == "20240121"].iloc[0]

        self.assertAlmostEqual(current_row["signal_momentum_5"], previous_row["momentum_5"], places=10)
        self.assertAlmostEqual(current_row["signal_gap_abs"], previous_row["gap_abs"], places=10)
        self.assertAlmostEqual(current_row["signal_close_location"], previous_row["close_location"], places=10)

    def test_collect_feature_coverage_report_flags_missing_symbols(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            feature_root = root / "features"
            registry = self.make_registry(root)
            catalog = self.make_catalog(root)

            self.make_parquet(
                tushare_root,
                "510300.SH",
                [
                    {
                        "ts_code": "510300.SH",
                        "trade_date": "20240102",
                        "pre_close": 1.0,
                        "open": 1.01,
                        "high": 1.03,
                        "low": 0.99,
                        "close": 1.02,
                        "pct_chg": 0.5,
                        "amount": 2000,
                        "vol": 200,
                    }
                ],
            )
            (feature_root / "sse" / "daily").mkdir(parents=True, exist_ok=True)
            (feature_root / "sse" / "daily" / "510300.csv").write_text("trade_date,close\n20240102,1.02\n", encoding="utf-8")

            report = module.collect_feature_coverage_report(
                registry_file=registry,
                tushare_data_path=tushare_root,
                dataset_catalog=catalog,
                feature_data_path=feature_root,
                start_date="20240101",
                end_date="20240131",
                exclude_money_market=True,
            )

        self.assertEqual(report["registry_symbol_count"], 2)
        self.assertEqual(report["parquet_available_count"], 1)
        self.assertEqual(report["feature_export_count"], 1)
        self.assertEqual(report["missing_parquet_symbols"], ["159919.SZ"])
        self.assertEqual(report["missing_feature_export_symbols"], [])


if __name__ == "__main__":
    unittest.main()
