import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_t0_monte_carlo.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_etf_t0_monte_carlo", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareEtfT0MonteCarloTests(unittest.TestCase):
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
                }
            }
        }
        catalog_path = root / "catalog.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        return catalog_path

    def make_registry(self, root: Path) -> Path:
        registry = root / "AShareETFMetadata.cs"
        registry.write_text(
            '\n'.join([
                '{ "510300", new AShareETFMetadata { Ticker = "510300", Name = "沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
                '{ "159919", new AShareETFMetadata { Ticker = "159919", Name = "嘉实沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },',
                '{ "511880", new AShareETFMetadata { Ticker = "511880", Name = "银华日利", TradingMode = ETFTradingMode.T0, Market = "SSE" } },',
            ]),
            encoding="utf-8"
        )
        return registry

    def test_block_bootstrap_returns_respects_shape_and_source_values(self):
        module = load_module()
        returns = [0.01, -0.02, 0.03]

        paths = module.block_bootstrap_returns(
            returns,
            trial_count=4,
            horizon_days=5,
            block_size=2,
            seed=7,
        )

        self.assertEqual(len(paths), 4)
        self.assertTrue(all(len(path) == 5 for path in paths))
        self.assertTrue(all(value in returns for path in paths for value in path))

    def test_stress_functions_apply_fee_and_market_shock(self):
        module = load_module()
        paths = [[0.01, 0.02], [0.0, -0.01]]

        fee_stressed = module.apply_fee_stress(paths, extra_fee_rate=0.0015)
        shock_stressed = module.apply_market_shock_stress(
            paths,
            shock_probability=1.0,
            shock_mean=0.02,
            shock_std=0.0,
            seed=11,
        )

        self.assertEqual(fee_stressed, [[0.0085, 0.0185], [-0.0015, -0.0115]])
        self.assertEqual(shock_stressed, [[-0.01, 0.0], [-0.02, -0.03]])

    def test_summarize_paths_reports_loss_probability_and_percentiles(self):
        module = load_module()
        paths = [
            [0.10, -0.05],
            [-0.20, -0.10],
            [0.0, 0.0],
        ]

        summary = module.summarize_paths(paths)

        self.assertEqual(summary["trial_count"], 3)
        self.assertEqual(summary["horizon_days"], 2)
        self.assertAlmostEqual(summary["loss_probability"], 1 / 3, places=10)
        self.assertAlmostEqual(summary["median_final_equity"], 1.0, places=10)
        self.assertLess(summary["p95_max_drawdown"], 0)

    def test_run_monte_carlo_writes_report(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            registry = self.make_registry(root)
            report_file = root / "mc.log"

            rows_a = []
            rows_b = []
            for day in range(1, 41):
                month = "01" if day <= 31 else "02"
                day_value = day if day <= 31 else day - 31
                date = f"2024{month}{day_value:02d}"
                rows_a.append({
                    "ts_code": "510300.SH",
                    "trade_date": date,
                    "pre_close": 100 + day - 1,
                    "open": 100 + day,
                    "high": 101 + day,
                    "low": 99 + day,
                    "close": 101 + day,
                    "pct_chg": 0.8,
                    "amount": 100000 + day * 1000,
                    "vol": 1000 + day,
                })
                rows_b.append({
                    "ts_code": "159919.SZ",
                    "trade_date": date,
                    "pre_close": 100 + day - 1,
                    "open": 100 + day,
                    "high": 100 + day,
                    "low": 99 + day,
                    "close": 95 + day * 0.1,
                    "pct_chg": -1.2,
                    "amount": 10000 + day * 100,
                    "vol": 900 + day,
                })

            self.write_parquet(root / "fund_daily" / "ts_code=510300.SH" / "data.parquet", rows_a)
            self.write_parquet(root / "fund_daily" / "ts_code=159919.SZ" / "data.parquet", rows_b)

            config = {
                "registry-file": str(registry),
                "tushare-data-path": str(root),
                "dataset-catalog": str(catalog),
                "start-date": "20240101",
                "end-date": "20240209",
                "exclude-money-market-etfs": True,
                "top-n": 1,
                "fee-rate": 0.0005,
                "trial-count": 32,
                "horizon-days": 15,
                "block-size": 4,
                "extra-fee-rate": 0.0008,
                "shock-probability": 0.10,
                "shock-mean": 0.015,
                "shock-std": 0.0,
                "seed": 23,
                "report-file": str(report_file),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            report = module.run_monte_carlo(module.load_pipeline_config(config_path))
            report_text = report_file.read_text(encoding="utf-8")

        self.assertGreater(report["base_backtest"]["trade_days"], 0)
        self.assertIn("baseline", report["scenarios"])
        self.assertIn("combined_stress", report["scenarios"])
        self.assertEqual(report["scenarios"]["baseline"]["trial_count"], 32)
        self.assertIn("AShare ETF T+0 Monte Carlo", report_text)
        self.assertIn("combined_stress", report_text)


if __name__ == "__main__":
    unittest.main()
