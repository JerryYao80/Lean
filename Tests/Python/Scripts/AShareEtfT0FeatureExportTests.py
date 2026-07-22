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
                },
                "fund_nav": {
                    "path": "fund_nav/ts_code={symbol}/data.parquet",
                    "date_field": "nav_date",
                    "symbol_field": "ts_code",
                },
                "etf_basic": {
                    "path": "etf_basic/data.parquet",
                    "symbol_field": "ts_code",
                },
                "etf_share_size": {
                    "path": "etf_share_size/year=*/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                },
                "index_daily": {
                    "path": "index_daily/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code",
                },
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

    def make_symbol_parquet(self, root: Path, dataset: str, ts_code: str, rows: list[dict]) -> None:
        target = root / dataset / f"ts_code={ts_code}"
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(target / "data.parquet", index=False)

    def make_year_parquet(self, root: Path, dataset: str, year: int, rows: list[dict]) -> None:
        target = root / dataset / f"year={year}"
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(target / "data.parquet", index=False)

    def make_static_parquet(self, root: Path, dataset: str, rows: list[dict]) -> None:
        target = root / dataset
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(target / "data.parquet", index=False)

    def test_export_feature_universe_writes_extended_shifted_signal_columns(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            feature_root = root / "features"
            report_path = root / "feature-report.json"
            config_path = root / "config.json"

            fund_rows = []
            nav_rows = []
            share_rows = []
            index_rows = []
            for day in range(1, 27):
                date = f"202401{day:02d}"
                etf_open = 1.00 + day * 0.01
                etf_close = 1.01 + day * 0.01
                index_open = 4000 + day * 2
                index_close = 4002 + day * 2
                fund_rows.append(
                    {
                        "ts_code": "510300.SH",
                        "trade_date": date,
                        "pre_close": 0.99 + day * 0.01,
                        "open": etf_open,
                        "high": 1.02 + day * 0.01,
                        "low": 0.98 + day * 0.01,
                        "close": etf_close,
                        "pct_chg": 0.1 + day * 0.01,
                        "amount": 1000 + day * 10,
                        "vol": 100 + day,
                    }
                )
                nav_rows.append(
                    {
                        "ts_code": "510300.SH",
                        "ann_date": date,
                        "nav_date": date,
                        "unit_nav": 0.995 + day * 0.01,
                        "accum_nav": 0.995 + day * 0.01,
                        "accum_div": None,
                        "net_asset": None,
                        "total_netasset": None,
                        "adj_nav": 0.995 + day * 0.01,
                        "update_flag": "0",
                    }
                )
                share_rows.append(
                    {
                        "trade_date": date,
                        "ts_code": "510300.SH",
                        "etf_name": "沪深300ETF",
                        "total_share": 1000000 + day * 1000,
                        "total_size": 2000000 + day * 2000,
                        "exchange": "SSE",
                    }
                )
                index_rows.append(
                    {
                        "ts_code": "000300.SH",
                        "trade_date": date,
                        "close": index_close,
                        "open": index_open,
                        "high": index_close + 5,
                        "low": index_open - 5,
                        "pre_close": 3998 + day * 2,
                        "change": 4,
                        "pct_chg": 0.1,
                        "vol": 100000 + day,
                        "amount": 200000 + day,
                    }
                )

            self.make_symbol_parquet(tushare_root, "fund_daily", "510300.SH", fund_rows)
            self.make_symbol_parquet(tushare_root, "fund_nav", "510300.SH", nav_rows)
            self.make_year_parquet(tushare_root, "etf_share_size", 2024, share_rows)
            self.make_symbol_parquet(tushare_root, "index_daily", "000300.SH", index_rows)
            self.make_static_parquet(
                tushare_root,
                "etf_basic",
                [
                    {
                        "ts_code": "510300.SH",
                        "csname": "华泰柏瑞沪深300ETF",
                        "extname": "沪深300ETF华泰柏瑞",
                        "cname": "华泰柏瑞沪深300交易型开放式指数证券投资基金",
                        "index_code": "000300.SH",
                        "index_name": "沪深300指数",
                        "setup_date": "20120504",
                        "list_date": "20120528",
                        "list_status": "L",
                        "exchange": "SH",
                        "mgr_name": "华泰柏瑞基金",
                        "custod_name": "中国工商银行股份有限公司",
                        "mgt_fee": 0.15,
                        "etf_type": "纯境内",
                    }
                ],
            )

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
        self.assertIn("signal_nav_premium_1", feature_frame.columns)
        self.assertIn("signal_excess_gap", feature_frame.columns)
        self.assertIn("signal_tracking_error_10", feature_frame.columns)
        self.assertIn("signal_index_momentum_5", feature_frame.columns)

        current_row = feature_frame.loc[feature_frame["trade_date"] == "20240122"].iloc[0]
        previous_row = feature_frame.loc[feature_frame["trade_date"] == "20240121"].iloc[0]

        self.assertAlmostEqual(current_row["signal_nav_premium_1"], previous_row["nav_premium_1"], places=10)
        self.assertAlmostEqual(current_row["signal_excess_gap"], previous_row["excess_gap"], places=10)
        self.assertAlmostEqual(current_row["signal_index_momentum_5"], previous_row["index_momentum_5"], places=10)

    def test_collect_feature_coverage_report_flags_missing_symbols(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            feature_root = root / "features"
            registry = self.make_registry(root)
            catalog = self.make_catalog(root)

            self.make_symbol_parquet(
                tushare_root,
                "fund_daily",
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
