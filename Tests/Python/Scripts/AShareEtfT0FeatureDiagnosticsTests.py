import importlib.util
import unittest
from pathlib import Path

import pandas as pd


BASE_SIGNAL_FIELDS = [
    "signal_momentum_20",
    "signal_momentum_5",
    "signal_liquidity_5",
    "signal_close_location",
    "signal_volatility_10",
    "signal_gap_abs",
]


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_t0_feature_diagnostics.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_etf_t0_feature_diagnostics", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareEtfT0FeatureDiagnosticsTests(unittest.TestCase):
    def make_panel(self) -> pd.DataFrame:
        dates = [f"202401{day:02d}" for day in range(1, 19)]
        symbols = ["A", "B", "C", "D", "E", "F"]
        rows = []
        for date_index, trade_date in enumerate(dates, start=1):
            for rank, symbol in enumerate(symbols, start=1):
                dense_value = float(rank)
                trade_return = -0.01 * dense_value + date_index * 0.0001
                row = {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "trade_return": trade_return,
                    "signal_dense_edge": dense_value,
                    "signal_sparse_bad": dense_value if date_index <= 2 and rank <= 3 else None,
                    "signal_momentum_20": dense_value * 0.1,
                    "signal_momentum_5": dense_value * 0.08,
                    "signal_liquidity_5": 1000 + rank * 100,
                    "signal_close_location": 0.2 + rank * 0.1,
                    "signal_volatility_10": 0.05 + rank * 0.02,
                    "signal_gap_abs": 0.01 + rank * 0.003,
                }
                rows.append(row)
        return pd.DataFrame(rows)

    def test_analyze_feature_panel_whitelists_dense_feature_and_rejects_sparse_one(self):
        module = load_module()
        panel = self.make_panel()
        config = module.default_config()
        config.update(
            {
                "min-cross-section-size": 4,
                "walk-forward-folds": 3,
                "walk-forward-min-train-days": 6,
                "walk-forward-min-test-days": 3,
                "whitelist-min-coverage-rate": 0.50,
                "whitelist-min-avg-cs-count": 4,
                "whitelist-min-ic-days": 6,
                "whitelist-min-abs-mean-ic": 0.20,
                "whitelist-min-wf-sign-match": 0.50,
                "default-min-coverage-rate": 0.50,
                "default-min-avg-cs-count": 4,
                "default-min-ic-days": 6,
                "default-min-abs-mean-ic": 0.20,
                "default-min-wf-sign-match": 0.50,
                "default-max-base-score-corr": 1.0,
                "candidate-features": ["signal_dense_edge", "signal_sparse_bad"],
                "current-default-features": [],
            }
        )

        report = module.analyze_feature_panel(
            panel,
            config,
            candidate_features=["signal_dense_edge", "signal_sparse_bad"],
            current_default_features=[],
        )

        self.assertIn("signal_dense_edge", report["summary"]["new_research_whitelist"])
        self.assertNotIn("signal_sparse_bad", report["summary"]["new_research_whitelist"])
        self.assertIn("signal_sparse_bad", report["summary"]["rejected_features"])

        dense_report = report["features"]["signal_dense_edge"]
        self.assertEqual(dense_report["suggested_direction"], "negative")
        self.assertLess(dense_report["suggested_weight_range"]["max"], 0.0)
        self.assertTrue(dense_report["passes_whitelist"])

    def test_build_report_markdown_includes_feature_diagnostics(self):
        module = load_module()
        panel = self.make_panel()
        config = module.default_config()
        config.update(
            {
                "min-cross-section-size": 4,
                "walk-forward-folds": 2,
                "walk-forward-min-train-days": 6,
                "walk-forward-min-test-days": 3,
                "candidate-features": ["signal_dense_edge"],
                "current-default-features": [],
            }
        )
        report = module.analyze_feature_panel(panel, config, candidate_features=["signal_dense_edge"], current_default_features=[])
        markdown = module.build_report_markdown(
            report,
            config,
            panel_summary={"registry_universe": 6, "loaded_symbols": 6, "panel_rows": len(panel), "trade_dates": panel["trade_date"].nunique()},
        )

        self.assertIn("AShare ETF T+0 Feature Diagnostics", markdown)
        self.assertIn("signal_dense_edge", markdown)
        self.assertIn("Suggested Weight Range", markdown)


if __name__ == "__main__":
    unittest.main()
