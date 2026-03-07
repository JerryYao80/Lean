import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_t0_feature_backtest.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_etf_t0_feature_backtest", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareETFT0FeatureBacktestTests(unittest.TestCase):
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

    def test_prepare_symbol_frame_builds_t0_feature_columns(self):
        module = load_module()
        frame = pd.DataFrame([
            {
                "trade_date": f"202401{day:02d}",
                "pre_close": 100 + day - 1,
                "open": 100 + day,
                "high": 101 + day,
                "low": 99 + day,
                "close": 100.5 + day,
                "pct_chg": 0.5 + day * 0.1,
                "amount": 1000 + day * 10,
                "vol": 100 + day,
            }
            for day in range(1, 26)
        ])

        prepared = module.prepare_symbol_frame("510300.SH", frame)
        row = prepared.loc[prepared["trade_date"] == "20240122"].iloc[0]

        self.assertIn("momentum_5", prepared.columns)
        self.assertIn("volatility_10", prepared.columns)
        self.assertIn("liquidity_5", prepared.columns)
        self.assertIn("close_location", prepared.columns)
        self.assertAlmostEqual(row["trade_return"], row["close"] / row["open"] - 1, places=10)
        self.assertAlmostEqual(row["gap_return"], row["open"] / row["pre_close"] - 1, places=10)
        self.assertGreaterEqual(row["close_location"], 0)
        self.assertLessEqual(row["close_location"], 1)
        self.assertIsNotNone(row["signal_momentum_20"])

    def test_compute_cross_section_scores_ranks_symbols(self):
        module = load_module()
        panel = pd.DataFrame([
            {
                "trade_date": "20240110",
                "symbol": "A",
                "signal_momentum_20": 0.10,
                "signal_momentum_5": 0.06,
                "signal_liquidity_5": 100,
                "signal_close_location": 0.90,
                "signal_volatility_10": 0.05,
                "signal_gap_abs": 0.01,
                "trade_return": 0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "B",
                "signal_momentum_20": 0.03,
                "signal_momentum_5": 0.01,
                "signal_liquidity_5": 80,
                "signal_close_location": 0.60,
                "signal_volatility_10": 0.10,
                "signal_gap_abs": 0.03,
                "trade_return": -0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "C",
                "signal_momentum_20": -0.01,
                "signal_momentum_5": -0.03,
                "signal_liquidity_5": 70,
                "signal_close_location": 0.20,
                "signal_volatility_10": 0.15,
                "signal_gap_abs": 0.05,
                "trade_return": -0.02,
            },
        ])

        scored = module.compute_cross_section_scores(panel)
        ranked = scored.sort_values("score", ascending=False)["symbol"].tolist()

        self.assertEqual(ranked, ["C", "B", "A"])

    def test_backtest_from_scores_applies_fee_and_compounds_equity(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 3.0, "trade_return": 0.020},
            {"trade_date": "20240110", "symbol": "B", "score": 1.0, "trade_return": -0.010},
            {"trade_date": "20240111", "symbol": "A", "score": 0.5, "trade_return": 0.010},
            {"trade_date": "20240111", "symbol": "B", "score": 2.0, "trade_return": 0.015},
        ])

        daily, summary = module.backtest_from_scores(scored, top_n=1, fee_rate=0.001)
        expected_equity = (1 + 0.020 - 0.001) * (1 + 0.015 - 0.001)

        self.assertAlmostEqual(daily.iloc[-1]["equity"], expected_equity, places=10)
        self.assertEqual(summary["trade_days"], 2)
        self.assertEqual(summary["top_symbols"][0][0], "A")

    def test_run_backtest_loads_registry_universe_and_writes_log(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            registry = self.make_registry(root)
            report_file = root / "bt.log"

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
                    "high": 100.0 + day,
                    "low": 99.0 + day,
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
                "report-file": str(report_file),
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            summary = module.run_backtest(module.load_pipeline_config(config_path))
            log_text = report_file.read_text(encoding="utf-8")

        self.assertGreater(summary["trade_days"], 0)
        self.assertIn("final_equity", summary)
        self.assertIn("AShare ETF T+0 Feature Strategy", log_text)
        self.assertIn("Final Equity", log_text)


if __name__ == "__main__":
    unittest.main()
