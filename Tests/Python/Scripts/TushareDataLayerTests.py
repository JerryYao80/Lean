import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "tushare_data_layer.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("tushare_data_layer", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TushareDataLayerTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def make_catalog(self, root: Path) -> Path:
        catalog = {
            "datasets": {
                "fund_daily": {
                    "path": "fund_daily/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code"
                },
                "fund_nav": {
                    "path": "fund_nav/ts_code={symbol}/data.parquet",
                    "date_field": "nav_date",
                    "symbol_field": "ts_code"
                },
                "fund_adj": {
                    "path": "fund_adj/year=*/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code"
                },
                "etf_basic": {
                    "path": "etf_basic/data.parquet",
                    "symbol_field": "ts_code"
                },
                "daily_basic": {
                    "path": "daily_basic/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code"
                },
                "stk_limit": {
                    "path": "stk_limit/ts_code={symbol}/data.parquet",
                    "date_field": "trade_date",
                    "symbol_field": "ts_code"
                }
            }
        }
        catalog_path = root / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        return catalog_path

    def test_list_fields_supports_symbol_and_glob_datasets(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = self.make_catalog(root)
            self.write_parquet(root / "fund_daily" / "ts_code=510300.SH" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240102", "close": 1.0, "amount": 100.0}
            ])
            self.write_parquet(root / "fund_adj" / "year=2024" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240102", "adj_factor": 1.1}
            ])

            layer = module.TushareDataLayer(root, catalog_path)

            fund_daily_fields = layer.list_fields("fund_daily")
            fund_adj_fields = layer.list_fields("fund_adj")

        self.assertEqual(fund_daily_fields, ["ts_code", "trade_date", "close", "amount"])
        self.assertEqual(fund_adj_fields, ["ts_code", "trade_date", "adj_factor"])

    def test_load_dataset_filters_symbol_fields_and_date_range(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = self.make_catalog(root)
            self.write_parquet(root / "fund_adj" / "year=2024" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240101", "adj_factor": 1.0},
                {"ts_code": "510300.SH", "trade_date": "20240103", "adj_factor": 1.2},
                {"ts_code": "159919.SZ", "trade_date": "20240103", "adj_factor": 2.2},
            ])

            layer = module.TushareDataLayer(root, catalog_path)
            frame = layer.load_dataset(
                "fund_adj",
                symbol="510300.SH",
                start_date="20240102",
                end_date="20240131",
                fields=["adj_factor"],
            )

        self.assertEqual(frame.to_dict(orient="records"), [
            {"trade_date": "20240103", "adj_factor": 1.2}
        ])

    def test_build_feature_frame_merges_multiple_time_series_on_common_date(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = self.make_catalog(root)
            self.write_parquet(root / "fund_daily" / "ts_code=510300.SH" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240102", "close": 1.0, "amount": 100.0},
                {"ts_code": "510300.SH", "trade_date": "20240103", "close": 1.1, "amount": 110.0},
            ])
            self.write_parquet(root / "fund_adj" / "year=2024" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240103", "adj_factor": 1.2}
            ])
            self.write_parquet(root / "fund_nav" / "ts_code=510300.SH" / "data.parquet", [
                {"ts_code": "510300.SH", "nav_date": "20240103", "unit_nav": 1.08}
            ])

            layer = module.TushareDataLayer(root, catalog_path)
            frame = layer.build_feature_frame(
                symbol="510300.SH",
                time_series=[
                    {"dataset": "fund_daily", "fields": ["close", "amount"]},
                    {"dataset": "fund_adj", "fields": ["adj_factor"]},
                    {"dataset": "fund_nav", "fields": ["unit_nav"]},
                ],
                start_date="20240102",
                end_date="20240131",
            )

        self.assertEqual(frame.to_dict(orient="records"), [
            {"date": "20240102", "close": 1.0, "amount": 100.0, "adj_factor": None, "unit_nav": None},
            {"date": "20240103", "close": 1.1, "amount": 110.0, "adj_factor": 1.2, "unit_nav": 1.08},
        ])

    def test_build_feature_frame_broadcasts_static_metadata(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = self.make_catalog(root)
            self.write_parquet(root / "fund_daily" / "ts_code=510300.SH" / "data.parquet", [
                {"ts_code": "510300.SH", "trade_date": "20240102", "close": 1.0, "amount": 100.0},
                {"ts_code": "510300.SH", "trade_date": "20240103", "close": 1.1, "amount": 110.0},
            ])
            self.write_parquet(root / "etf_basic" / "data.parquet", [
                {"ts_code": "510300.SH", "csname": "沪深300ETF", "etf_type": "纯境内"}
            ])

            layer = module.TushareDataLayer(root, catalog_path)
            frame = layer.build_feature_frame(
                symbol="510300.SH",
                time_series=[
                    {"dataset": "fund_daily", "fields": ["close"]},
                ],
                static=[
                    {"dataset": "etf_basic", "fields": ["csname", "etf_type"]},
                ],
                start_date="20240102",
                end_date="20240131",
            )

        self.assertEqual(frame.to_dict(orient="records"), [
            {"date": "20240102", "close": 1.0, "csname": "沪深300ETF", "etf_type": "纯境内"},
            {"date": "20240103", "close": 1.1, "csname": "沪深300ETF", "etf_type": "纯境内"},
        ])


if __name__ == "__main__":
    unittest.main()
