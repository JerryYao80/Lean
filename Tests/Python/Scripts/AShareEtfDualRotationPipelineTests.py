import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_dual_rotation_pipeline.py"
    spec = importlib.util.spec_from_file_location("ashare_etf_dual_rotation_pipeline_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareEtfDualRotationPipelineTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def build_trade_dates(self, start: str, periods: int) -> list[str]:
        dates = pd.date_range(start=start, periods=periods, freq="B")
        return [date.strftime("%Y%m%d") for date in dates]

    def build_fund_rows(self, dates: list[str], start_price: float, step: float, amount_base: float) -> list[dict]:
        rows = []
        previous_close = start_price
        for index, trade_date in enumerate(dates):
            close = start_price + step * index
            open_price = previous_close * (1.0 + 0.001)
            high = max(open_price, close) * 1.002
            low = min(open_price, close) * 0.998
            pct_chg = (close / previous_close - 1.0) * 100.0 if previous_close else 0.0
            rows.append({
                "ts_code": "",
                "trade_date": trade_date,
                "pre_close": previous_close,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "pct_chg": pct_chg,
                "amount": amount_base * (1.0 + 0.01 * index),
                "vol": amount_base / max(close, 0.1),
            })
            previous_close = close
        return rows

    def build_nav_rows(self, symbol: str, dates: list[str], start_price: float, step: float) -> list[dict]:
        rows = []
        for index, trade_date in enumerate(dates):
            nav = start_price + step * index
            rows.append({
                "ts_code": symbol,
                "nav_date": trade_date,
                "unit_nav": nav,
                "adj_nav": nav,
            })
        return rows

    def build_share_rows(self, symbol: str, dates: list[str], base_share: float) -> list[dict]:
        rows = []
        for index, trade_date in enumerate(dates):
            total_share = base_share * (1.0 + 0.001 * index)
            rows.append({
                "ts_code": symbol,
                "trade_date": trade_date,
                "total_share": total_share,
                "total_size": total_share * 10.0,
            })
        return rows

    def test_run_pipeline_writes_plan_files(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            results_root = root / "results"
            data_root = root / "data"
            registry_file = root / "AShareETFMetadata.cs"
            dataset_catalog = root / "catalog.json"
            trade_dates = self.build_trade_dates("2024-01-02", 45)

            registry_file.write_text(
                "\n".join([
                    '{ "510300", new AShareETFMetadata { Ticker = "510300", Name = "沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
                    '{ "510500", new AShareETFMetadata { Ticker = "510500", Name = "中证500ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
                    '{ "510180", new AShareETFMetadata { Ticker = "510180", Name = "180ETF", TradingMode = ETFTradingMode.T1, Market = "SSE" } },',
                ]),
                encoding="utf-8",
            )

            self.write_json(dataset_catalog, {
                "datasets": {
                    "fund_daily": {"path": "fund_daily/ts_code={symbol}/data.parquet", "date_field": "trade_date", "symbol_field": "ts_code"},
                    "fund_nav": {"path": "fund_nav/ts_code={symbol}/data.parquet", "date_field": "nav_date", "symbol_field": "ts_code"},
                    "etf_share_size": {"path": "etf_share_size/ts_code={symbol}/data.parquet", "date_field": "trade_date", "symbol_field": "ts_code"},
                    "etf_basic": {"path": "etf_basic/data.parquet", "symbol_field": "ts_code"},
                    "index_daily": {"path": "index_daily/ts_code={symbol}/data.parquet", "date_field": "trade_date", "symbol_field": "ts_code"},
                }
            })

            etf_basic_rows = [
                {"ts_code": "510300.SH", "csname": "沪深300ETF", "index_code": "000300.SH", "index_name": "沪深300", "list_date": "20120101", "list_status": "L", "exchange": "SH", "mgt_fee": 0.5, "etf_type": "纯境内"},
                {"ts_code": "510500.SH", "csname": "中证500ETF", "index_code": "000300.SH", "index_name": "沪深300", "list_date": "20120101", "list_status": "L", "exchange": "SH", "mgt_fee": 0.5, "etf_type": "纯境内"},
                {"ts_code": "510180.SH", "csname": "180ETF", "index_code": "000300.SH", "index_name": "沪深300", "list_date": "20120101", "list_status": "L", "exchange": "SH", "mgt_fee": 0.5, "etf_type": "纯境内"},
            ]
            self.write_parquet(tushare_root / "etf_basic" / "data.parquet", etf_basic_rows)

            benchmark_rows = self.build_fund_rows(trade_dates, 4000.0, 8.0, 5_000_000.0)
            for row in benchmark_rows:
                row["ts_code"] = "000300.SH"
            self.write_parquet(tushare_root / "index_daily" / "ts_code=000300.SH" / "data.parquet", benchmark_rows)

            symbol_specs = {
                "510300.SH": (4.0, 0.025, 3_000_000.0),
                "510500.SH": (5.0, 0.010, 2_000_000.0),
                "510180.SH": (6.0, 0.030, 2_500_000.0),
            }
            for symbol, (start_price, step, amount_base) in symbol_specs.items():
                fund_rows = self.build_fund_rows(trade_dates, start_price, step, amount_base)
                for row in fund_rows:
                    row["ts_code"] = symbol
                self.write_parquet(tushare_root / "fund_daily" / f"ts_code={symbol}" / "data.parquet", fund_rows)
                self.write_parquet(tushare_root / "fund_nav" / f"ts_code={symbol}" / "data.parquet", self.build_nav_rows(symbol, trade_dates, start_price, step))
                self.write_parquet(tushare_root / "etf_share_size" / f"ts_code={symbol}" / "data.parquet", self.build_share_rows(symbol, trade_dates, 1_000_000.0))

            plan_root = data_root / "alternative" / "ashare-etf-dual-rotation"
            summary_file = results_root / "summary.json"
            config = module.load_pipeline_config(overrides={
                "registry-file": str(registry_file),
                "tushare-data-path": str(tushare_root),
                "dataset-catalog": str(dataset_catalog),
                "start-date": trade_dates[0],
                "end-date": trade_dates[-1],
                "variant-name": "dual_rotation",
                "liquidity-quantile": 0.0,
                "max-symbols-per-day": 10,
                "min-sleeve-symbols": 1,
                "t0-top-k": 1,
                "t1-top-k": 1,
                "symbol-min-momentum20": -1.0,
                "fallback-single-sleeve-score": -10.0,
                "plan-directory": str(plan_root),
                "benchmark-file": str(plan_root / "benchmark" / "000300.SH.csv"),
                "daily-nav-file": str(results_root / "daily.csv"),
                "rebalance-file": str(results_root / "rebalances.csv"),
                "summary-file": str(summary_file),
            })

            report = module.run_pipeline(config)
            plan_file = Path(report["plan_files"]["dual_rotation"])
            benchmark_file = Path(report["plan_files"]["benchmark"])

            self.assertTrue(plan_file.exists())
            self.assertTrue(benchmark_file.exists())
            self.assertTrue(summary_file.exists())
            self.assertIn("dual_rotation", report["summary_by_variant"])
            self.assertGreater(len(pd.read_csv(plan_file)), 0)


if __name__ == "__main__":
    unittest.main()
