import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_industry_rotation_pipeline.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_industry_rotation_pipeline", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareIndustryRotationPipelineTests(unittest.TestCase):
    def test_compute_resonance_score_uses_weighted_industry_returns_of_strongest_days(self):
        module = load_module()

        history = pd.DataFrame([
            {"trade_date": "20240102", "industry": "电子", "return": 0.01, "vol": 100.0},
            {"trade_date": "20240103", "industry": "电子", "return": 0.03, "vol": 300.0},
            {"trade_date": "20240104", "industry": "电子", "return": 0.02, "vol": 250.0},
            {"trade_date": "20240105", "industry": "电子", "return": -0.01, "vol": 500.0},
        ])
        industry_returns = pd.Series(
            {
                ("20240103", "电子"): 0.04,
                ("20240104", "电子"): 0.02,
                ("20240102", "电子"): 0.01,
            }
        )

        score, used_dates = module.compute_resonance_score(
            history,
            industry_returns,
            lookback=20,
            top_days=3,
            positive_only=True,
        )

        expected = 0.04 + 0.5 * 0.02 + 0.25 * 0.01
        self.assertAlmostEqual(score, expected)
        self.assertEqual(used_dates, ["20240103", "20240104", "20240102"])

    def test_group_trade_dates_by_month_uses_last_trading_day(self):
        module = load_module()

        grouped = module.group_trade_dates_by_month(
            ["20240102", "20240131", "20240201", "20240229", "20240301"]
        )

        self.assertEqual(grouped, ["20240131", "20240229", "20240301"])

    def test_extend_benchmark_with_live_trade_date_appends_flat_row(self):
        module = load_module()
        benchmark = pd.DataFrame([
            {"trade_date": "20240328", "close": 3900.0, "pct_chg": 0.60, "benchmark_return": 0.006},
            {"trade_date": "20240329", "close": 3925.0, "pct_chg": 0.64, "benchmark_return": 0.0064},
        ])

        extended = module.extend_benchmark_with_live_trade_date(benchmark, "20240401")

        self.assertEqual(extended["trade_date"].tolist()[-1], "20240401")
        self.assertEqual(float(extended.iloc[-1]["close"]), 3925.0)
        self.assertEqual(float(extended.iloc[-1]["pct_chg"]), 0.0)
        self.assertEqual(float(extended.iloc[-1]["benchmark_return"]), 0.0)

    def test_append_live_snapshot_to_panel_overrides_current_trade_date_rows(self):
        module = load_module()
        raw_panel = pd.DataFrame([
            {
                "ts_code": "600000.SH",
                "trade_date": "20240329",
                "name": "浦发银行",
                "pct_change": 1.0,
                "close": 10.0,
                "pre_close": 9.9,
                "vol": 1000,
                "amount": 10000,
                "turn_over": 1.2,
                "industry": "银行",
                "float_mv": 200000.0,
            }
        ])
        stock_basic = pd.DataFrame([
            {"ts_code": "600000.SH", "market": "主板", "list_date": "19991110", "name": "浦发银行"},
        ])
        live_snapshot = pd.DataFrame([
            {
                "ts_code": "600000.SH",
                "trade_date": "20240401",
                "close": 10.5,
                "price": 10.5,
                "pre_close": 10.0,
                "pct_chg": 5.0,
                "vol": 1200,
                "amount": 12600,
            }
        ])

        merged = module.append_live_snapshot_to_panel(raw_panel, stock_basic, live_snapshot, "20240401")

        self.assertEqual(sorted(merged["trade_date"].tolist()), ["20240329", "20240401"])
        current = merged[merged["trade_date"] == "20240401"].iloc[0]
        self.assertEqual(current["ts_code"], "600000.SH")
        self.assertEqual(float(current["close"]), 10.5)
        self.assertEqual(float(current["pct_change"]), 5.0)
        self.assertEqual(current["industry"], "银行")
        self.assertGreater(float(current["float_mv"]), 200000.0)

    def test_resolve_foreign_ratio_handles_duplicate_symbol_rows(self):
        module = load_module()
        snapshots = [
            (
                "20240331",
                pd.Series(
                    [1.1, 1.3, 0.8],
                    index=["600000.SH", "600000.SH", "000001.SZ"],
                    dtype=float,
                ),
            )
        ]

        ratio = module.resolve_foreign_ratio(
            snapshots=snapshots,
            signal_date="20240401",
            ts_code="600000.SH",
            max_age_days=30,
        )

        self.assertAlmostEqual(ratio, 1.3)


if __name__ == "__main__":
    unittest.main()
