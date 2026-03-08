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

    def test_backtest_from_scores_skips_low_conviction_and_gap_risk_days(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 0.40, "trade_return": 0.020, "signal_gap_abs": 0.010},
            {"trade_date": "20240110", "symbol": "B", "score": 0.20, "trade_return": 0.010, "signal_gap_abs": 0.010},
            {"trade_date": "20240111", "symbol": "A", "score": 1.40, "trade_return": 0.020, "signal_gap_abs": 0.020},
            {"trade_date": "20240111", "symbol": "B", "score": 0.20, "trade_return": 0.010, "signal_gap_abs": 0.020},
            {"trade_date": "20240112", "symbol": "A", "score": 1.30, "trade_return": 0.020, "signal_gap_abs": 0.010},
            {"trade_date": "20240112", "symbol": "B", "score": 0.20, "trade_return": 0.010, "signal_gap_abs": 0.010},
        ])

        daily, summary = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.001,
            min_score_spread=0.5,
            max_average_gap_abs=0.015,
        )

        self.assertEqual(summary["scored_trade_days"], 3)
        self.assertEqual(summary["skipped_low_conviction_days"], 1)
        self.assertEqual(summary["skipped_gap_risk_days"], 1)
        self.assertEqual(summary["selected_trade_days"], 1)
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily.iloc[0]["trade_date"], "20240112")

    def test_backtest_from_scores_scales_risk_regime_exposure(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 1.2, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": -0.010, "signal_volatility_10": 1.50},
            {"trade_date": "20240110", "symbol": "B", "score": 0.2, "trade_return": 0.010, "signal_gap_abs": 0.010, "signal_momentum_5": -0.020, "signal_volatility_10": 1.60},
            {"trade_date": "20240111", "symbol": "A", "score": 1.1, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": -0.001, "signal_volatility_10": 1.30},
            {"trade_date": "20240111", "symbol": "B", "score": 0.2, "trade_return": 0.010, "signal_gap_abs": 0.010, "signal_momentum_5": -0.002, "signal_volatility_10": 1.35},
            {"trade_date": "20240112", "symbol": "A", "score": 1.0, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": 0.010, "signal_volatility_10": 1.10},
            {"trade_date": "20240112", "symbol": "B", "score": 0.2, "trade_return": 0.010, "signal_gap_abs": 0.010, "signal_momentum_5": 0.000, "signal_volatility_10": 1.00},
        ])

        daily, summary = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.001,
            risk_regime_filter_enabled=True,
            risk_regime_medium_momentum_threshold=0.0,
            risk_regime_medium_volatility_threshold=1.25,
            risk_regime_medium_exposure_scale=0.9,
            risk_regime_momentum_threshold=-0.005,
            risk_regime_volatility_threshold=1.4,
            risk_regime_high_exposure_scale=0.1,
        )

        expected_equity = (1 + (0.020 - 0.001) * 0.1) * (1 + (0.020 - 0.001) * 0.9) * (1 + (0.020 - 0.001))

        self.assertEqual(summary["selected_trade_days"], 3)
        self.assertEqual(summary["medium_risk_regime_days"], 1)
        self.assertEqual(summary["high_risk_regime_days"], 1)
        self.assertAlmostEqual(summary["average_exposure_scale"], (0.1 + 0.9 + 1.0) / 3, places=10)
        self.assertEqual(daily.iloc[0]["risk_regime_bucket"], "high")
        self.assertEqual(daily.iloc[1]["risk_regime_bucket"], "medium")
        self.assertEqual(daily.iloc[2]["risk_regime_bucket"], "normal")
        self.assertAlmostEqual(daily.iloc[-1]["equity"], expected_equity, places=10)


    def test_backtest_from_scores_applies_signal_shrinkage_and_liquidity_whitelist(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 1.5, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": -0.010, "signal_volatility_10": 1.50, "signal_liquidity_5": 10},
            {"trade_date": "20240110", "symbol": "B", "score": 1.2, "trade_return": 0.015, "signal_gap_abs": 0.010, "signal_momentum_5": -0.010, "signal_volatility_10": 1.60, "signal_liquidity_5": 100},
            {"trade_date": "20240110", "symbol": "C", "score": 0.8, "trade_return": 0.010, "signal_gap_abs": 0.010, "signal_momentum_5": -0.010, "signal_volatility_10": 1.55, "signal_liquidity_5": 90},
            {"trade_date": "20240111", "symbol": "A", "score": 1.4, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": -0.001, "signal_volatility_10": 1.30, "signal_liquidity_5": 50},
            {"trade_date": "20240111", "symbol": "B", "score": 1.1, "trade_return": 0.015, "signal_gap_abs": 0.010, "signal_momentum_5": -0.001, "signal_volatility_10": 1.35, "signal_liquidity_5": 80},
            {"trade_date": "20240111", "symbol": "C", "score": 0.7, "trade_return": 0.005, "signal_gap_abs": 0.010, "signal_momentum_5": -0.001, "signal_volatility_10": 1.28, "signal_liquidity_5": 60},
            {"trade_date": "20240112", "symbol": "A", "score": 1.3, "trade_return": 0.020, "signal_gap_abs": 0.010, "signal_momentum_5": 0.010, "signal_volatility_10": 1.10, "signal_liquidity_5": 40},
            {"trade_date": "20240112", "symbol": "B", "score": 1.1, "trade_return": 0.015, "signal_gap_abs": 0.010, "signal_momentum_5": 0.000, "signal_volatility_10": 1.00, "signal_liquidity_5": 70},
            {"trade_date": "20240112", "symbol": "C", "score": 0.6, "trade_return": 0.005, "signal_gap_abs": 0.010, "signal_momentum_5": 0.000, "signal_volatility_10": 1.00, "signal_liquidity_5": 65},
        ])

        daily, summary = module.backtest_from_scores(
            scored,
            top_n=2,
            fee_rate=0.001,
            risk_regime_filter_enabled=True,
            risk_regime_medium_momentum_threshold=0.0,
            risk_regime_medium_volatility_threshold=1.25,
            risk_regime_medium_exposure_scale=1.0,
            risk_regime_medium_top_n=1,
            risk_regime_momentum_threshold=-0.005,
            risk_regime_volatility_threshold=1.4,
            risk_regime_high_exposure_scale=0.5,
            risk_regime_high_top_n=1,
            risk_regime_high_liquidity_quantile=0.5,
        )

        self.assertEqual(daily.iloc[0]["selected_symbols"], "B")
        self.assertEqual(daily.iloc[1]["selected_symbols"], "A")
        self.assertEqual(daily.iloc[2]["selected_symbols"], "A,B")
        self.assertAlmostEqual(summary["average_selected_count"], 4 / 3, places=10)

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
                "min-score-spread": 0.1,
                "max-average-gap-abs": 0.05,
                "risk-regime-filter-enabled": True,
                "risk-regime-medium-momentum-threshold": 0.0,
                "risk-regime-medium-volatility-threshold": 1.25,
                "risk-regime-medium-exposure-scale": 0.9,
                "risk-regime-medium-top-n": 1,
                "risk-regime-medium-score-spread-add": 0.0,
                "risk-regime-medium-liquidity-quantile": 0.0,
                "risk-regime-momentum-threshold": -0.5,
                "risk-regime-volatility-threshold": 10.0,
                "risk-regime-high-exposure-scale": 0.1,
                "risk-regime-high-top-n": 1,
                "risk-regime-high-score-spread-add": 0.0,
                "risk-regime-high-liquidity-quantile": 0.0,
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
        self.assertIn("Selection Rate", log_text)
        self.assertIn("Risk Regime Scaling", log_text)
        self.assertIn("Risk Regime Signal Shrinkage", log_text)
        self.assertIn("Portfolio Risk Overlay", log_text)


    def test_backtest_from_scores_applies_quarter_kelly_floor_after_losses(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 2.0, "trade_return": -0.020},
            {"trade_date": "20240110", "symbol": "B", "score": 1.0, "trade_return": 0.000},
            {"trade_date": "20240111", "symbol": "A", "score": 2.0, "trade_return": 0.020},
            {"trade_date": "20240111", "symbol": "B", "score": 1.0, "trade_return": 0.000},
        ])

        daily, summary = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.0,
            portfolio_quarter_kelly_enabled=True,
            portfolio_quarter_kelly_lookback=5,
            portfolio_quarter_kelly_min_observations=1,
            portfolio_quarter_kelly_floor_scale=0.25,
            portfolio_quarter_kelly_cap_scale=1.0,
            portfolio_quarter_kelly_medium_regime_multiplier=0.75,
            portfolio_quarter_kelly_high_regime_multiplier=0.5,
        )

        self.assertAlmostEqual(daily.iloc[0]["portfolio_quarter_kelly_scale"], 1.0, places=10)
        self.assertAlmostEqual(daily.iloc[1]["portfolio_quarter_kelly_scale"], 0.25, places=10)
        self.assertAlmostEqual(daily.iloc[1]["portfolio_risk_overlay_scale"], 0.25, places=10)
        self.assertAlmostEqual(summary["average_portfolio_quarter_kelly_scale"], 0.625, places=10)

    def test_backtest_from_scores_higher_kelly_fraction_scales_less_aggressively(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 2.0, "trade_return": 0.10},
            {"trade_date": "20240110", "symbol": "B", "score": 1.0, "trade_return": 0.0},
            {"trade_date": "20240111", "symbol": "A", "score": 2.0, "trade_return": -0.09},
            {"trade_date": "20240111", "symbol": "B", "score": 1.0, "trade_return": 0.0},
            {"trade_date": "20240112", "symbol": "A", "score": 2.0, "trade_return": 0.02},
            {"trade_date": "20240112", "symbol": "B", "score": 1.0, "trade_return": 0.0},
        ])

        quarter_daily, _ = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.0,
            portfolio_quarter_kelly_enabled=True,
            portfolio_quarter_kelly_lookback=5,
            portfolio_quarter_kelly_min_observations=2,
            portfolio_quarter_kelly_floor_scale=0.0,
            portfolio_quarter_kelly_cap_scale=1.0,
            portfolio_quarter_kelly_fraction=0.25,
            portfolio_quarter_kelly_medium_regime_multiplier=0.75,
            portfolio_quarter_kelly_high_regime_multiplier=0.5,
        )
        half_daily, _ = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.0,
            portfolio_quarter_kelly_enabled=True,
            portfolio_quarter_kelly_lookback=5,
            portfolio_quarter_kelly_min_observations=2,
            portfolio_quarter_kelly_floor_scale=0.0,
            portfolio_quarter_kelly_cap_scale=1.0,
            portfolio_quarter_kelly_fraction=0.5,
            portfolio_quarter_kelly_medium_regime_multiplier=0.75,
            portfolio_quarter_kelly_high_regime_multiplier=0.5,
        )

        self.assertLess(quarter_daily.iloc[2]["portfolio_quarter_kelly_scale"], half_daily.iloc[2]["portfolio_quarter_kelly_scale"])
        self.assertAlmostEqual(quarter_daily.iloc[2]["portfolio_quarter_kelly_scale"], 0.1385, places=3)
        self.assertAlmostEqual(half_daily.iloc[2]["portfolio_quarter_kelly_scale"], 0.2770, places=3)

    def test_backtest_from_scores_applies_vol_target_scale_after_high_volatility(self):
        module = load_module()
        scored = pd.DataFrame([
            {"trade_date": "20240110", "symbol": "A", "score": 2.0, "trade_return": 0.040},
            {"trade_date": "20240110", "symbol": "B", "score": 1.0, "trade_return": 0.000},
            {"trade_date": "20240111", "symbol": "A", "score": 2.0, "trade_return": -0.020},
            {"trade_date": "20240111", "symbol": "B", "score": 1.0, "trade_return": 0.000},
            {"trade_date": "20240112", "symbol": "A", "score": 2.0, "trade_return": 0.020},
            {"trade_date": "20240112", "symbol": "B", "score": 1.0, "trade_return": 0.000},
        ])

        daily, summary = module.backtest_from_scores(
            scored,
            top_n=1,
            fee_rate=0.0,
            portfolio_vol_target_enabled=True,
            portfolio_vol_target_daily_vol=0.01,
            portfolio_vol_target_lookback=5,
            portfolio_vol_target_min_observations=2,
            portfolio_vol_target_floor_scale=0.5,
            portfolio_vol_target_cap_scale=1.0,
        )

        self.assertAlmostEqual(daily.iloc[0]["portfolio_vol_target_scale"], 1.0, places=10)
        self.assertAlmostEqual(daily.iloc[1]["portfolio_vol_target_scale"], 1.0, places=10)
        self.assertLess(daily.iloc[2]["portfolio_vol_target_scale"], 1.0)
        self.assertLess(summary["average_portfolio_vol_target_scale"], 1.0)


    def test_compute_cross_section_scores_disables_conditional_z20_overlay_in_high_risk_regime(self):
        module = load_module()
        panel = pd.DataFrame([
            {
                "trade_date": "20240110",
                "symbol": "A",
                "signal_momentum_20": 0.01,
                "signal_momentum_5": -0.02,
                "signal_liquidity_5": 100,
                "signal_close_location": 0.50,
                "signal_volatility_10": 1.60,
                "signal_gap_abs": 0.01,
                "signal_nav_premium_z20": -1.0,
                "trade_return": 0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "B",
                "signal_momentum_20": 0.01,
                "signal_momentum_5": -0.02,
                "signal_liquidity_5": 100,
                "signal_close_location": 0.50,
                "signal_volatility_10": 1.60,
                "signal_gap_abs": 0.01,
                "signal_nav_premium_z20": 0.0,
                "trade_return": 0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "C",
                "signal_momentum_20": 0.01,
                "signal_momentum_5": -0.02,
                "signal_liquidity_5": 100,
                "signal_close_location": 0.50,
                "signal_volatility_10": 1.60,
                "signal_gap_abs": 0.01,
                "signal_nav_premium_z20": 1.0,
                "trade_return": 0.01,
            },
        ])

        base = module.compute_cross_section_scores(panel)
        scored = module.compute_cross_section_scores(
            panel,
            config={
                "conditional-signal-nav-premium-z20-weight": -0.2,
                "conditional-signal-nav-premium-z20-normal-scale": 1.0,
                "conditional-signal-nav-premium-z20-medium-scale": 0.5,
                "conditional-signal-nav-premium-z20-high-scale": 0.0,
                "risk-regime-medium-momentum-threshold": 0.0,
                "risk-regime-medium-volatility-threshold": 1.25,
                "risk-regime-momentum-threshold": -0.005,
                "risk-regime-volatility-threshold": 1.4,
            },
        )

        merged = base[["symbol", "score"]].merge(scored[["symbol", "score", "score_overlay_nav_premium_z20"]], on="symbol", suffixes=("_base", "_overlay"))
        self.assertTrue((scored["score_risk_regime_bucket"] == "high").all())
        self.assertTrue((pd.to_numeric(scored["score_overlay_nav_premium_z20_scale"]) == 0.0).all())
        self.assertTrue((pd.to_numeric(scored["score_overlay_nav_premium_z20"]).abs() < 1e-12).all())
        self.assertTrue(((pd.to_numeric(merged["score_base"]) - pd.to_numeric(merged["score_overlay"])) .abs() < 1e-12).all())

    def test_compute_cross_section_scores_orthogonalizes_conditional_z20_overlay(self):
        module = load_module()
        panel = pd.DataFrame([
            {
                "trade_date": "20240110",
                "symbol": "A",
                "signal_momentum_20": 3.0,
                "signal_momentum_5": 3.0,
                "signal_liquidity_5": 3.0,
                "signal_close_location": 3.0,
                "signal_volatility_10": 3.0,
                "signal_gap_abs": 3.0,
                "signal_nav_premium_z20": 3.0,
                "trade_return": 0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "B",
                "signal_momentum_20": 2.0,
                "signal_momentum_5": 2.0,
                "signal_liquidity_5": 2.0,
                "signal_close_location": 2.0,
                "signal_volatility_10": 2.0,
                "signal_gap_abs": 2.0,
                "signal_nav_premium_z20": 2.0,
                "trade_return": -0.01,
            },
            {
                "trade_date": "20240110",
                "symbol": "C",
                "signal_momentum_20": 1.0,
                "signal_momentum_5": 1.0,
                "signal_liquidity_5": 1.0,
                "signal_close_location": 1.0,
                "signal_volatility_10": 1.0,
                "signal_gap_abs": 1.0,
                "signal_nav_premium_z20": 1.0,
                "trade_return": -0.02,
            },
        ])

        base = module.compute_cross_section_scores(panel)
        orth = module.compute_cross_section_scores(
            panel,
            config={
                "conditional-signal-nav-premium-z20-weight": -0.2,
                "conditional-signal-nav-premium-z20-orthogonalize": True,
                "conditional-signal-nav-premium-z20-normal-scale": 1.0,
                "conditional-signal-nav-premium-z20-medium-scale": 1.0,
                "conditional-signal-nav-premium-z20-high-scale": 1.0,
                "risk-regime-medium-momentum-threshold": -999.0,
                "risk-regime-medium-volatility-threshold": 999.0,
                "risk-regime-momentum-threshold": -999.0,
                "risk-regime-volatility-threshold": 999.0,
            },
        )

        merged = base[["symbol", "score"]].merge(
            orth[["symbol", "score", "score_overlay_nav_premium_z20"]],
            on="symbol",
            suffixes=("_base", "_orth"),
        )

        self.assertTrue((pd.to_numeric(merged["score_overlay_nav_premium_z20"]).abs() < 1e-10).all())
        self.assertTrue(((pd.to_numeric(merged["score_base"]) - pd.to_numeric(merged["score_orth"])) .abs() < 1e-10).all())


if __name__ == "__main__":
    unittest.main()
