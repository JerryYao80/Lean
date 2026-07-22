import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "export_backtest_results_to_influx.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("export_backtest_results_to_influx", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BacktestResultsInfluxExportTests(unittest.TestCase):
    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_parse_numeric_value_handles_percent_currency_and_commas(self):
        module = load_module()

        self.assertEqual(module.parse_numeric_value("47.637%"), 47.637)
        self.assertEqual(module.parse_numeric_value("¥1,061,476.39"), 1061476.39)
        self.assertEqual(module.parse_numeric_value("-¥8,877.61"), -8877.61)
        self.assertIsNone(module.parse_numeric_value("Synthetic + Lean stats sync"))

    def test_build_stat_points_reads_statistics_runtime_and_portfolio(self):
        module = load_module()
        summary = {
            "statistics": {
                "Net Profit": "6.148%",
                "Sharpe Ratio": "-0.256",
                "Execution Mode": "Synthetic + Lean stats sync",
            },
            "runtimeStatistics": {
                "Equity": "¥1,061,476.39",
            },
            "totalPerformance": {
                "portfolioStatistics": {
                    "probabilisticSharpeRatio": "0.4764",
                }
            },
        }

        points = module.build_stat_points(summary, "Algo", "run-1", 123)

        by_metric = {point.tags["metric"]: point for point in points}
        self.assertEqual(by_metric["Net Profit"].fields["numeric_value"], 6.148)
        self.assertEqual(by_metric["Net Profit"].tags["unit"], "percent")
        self.assertEqual(by_metric["Equity"].fields["numeric_value"], 1061476.39)
        self.assertEqual(by_metric["Equity"].tags["unit"], "currency")
        self.assertAlmostEqual(by_metric["probabilisticSharpeRatio"].fields["numeric_value"], 47.64, places=6)
        self.assertNotIn("numeric_value", by_metric["Execution Mode"].fields)

    def test_build_monte_carlo_points_scales_fractional_probabilities(self):
        module = load_module()
        payload = {
            "generatedAtUtc": "2026-04-14T14:49:17Z",
            "scenarios": {
                "combinedStress": {
                    "trialCount": 500,
                    "meanTotalReturn": -0.0415,
                    "lossProbability": 0.928,
                    "meanSharpe": -2.75,
                }
            },
        }

        points = module.build_monte_carlo_points(payload, Path("AShareBarraCNE5Algorithm-monte-carlo.json"), "Algo", "run-1", 123)

        by_metric = {point.tags["metric"]: point for point in points}
        self.assertAlmostEqual(by_metric["lossProbability"].fields["numeric_value"], 92.8, places=6)
        self.assertEqual(by_metric["lossProbability"].tags["unit"], "percent")
        self.assertAlmostEqual(by_metric["meanTotalReturn"].fields["numeric_value"], -4.15, places=6)
        self.assertEqual(by_metric["meanSharpe"].fields["numeric_value"], -2.75)
        self.assertEqual(by_metric["meanSharpe"].tags["unit"], "number")

    def test_collect_points_supports_backtest_results_statistics_key(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_file = root / "backtest-results.json"
            monte_file = root / "monte.json"
            self.write_json(summary_file, {
                "Statistics": {
                    "Net Profit": "12.5%",
                },
                "State": {
                    "Name": "local",
                    "StartTime": "2026-04-14T14:47:40Z",
                    "EndTime": "2026-04-14T14:49:17Z",
                },
            })
            self.write_json(monte_file, {
                "base_backtest": {"start_date": "20250102"},
                "scenarios": {
                    "combined": {
                        "probability_negative_total_return": 0.0388,
                    }
                },
            })

            points = module.collect_points(summary_file, [monte_file], "Algo", run_id="explicit-run")

        self.assertEqual({point.tags["run_id"] for point in points}, {"explicit-run"})
        self.assertIn("lean_backtest_stat", {point.measurement for point in points})
        self.assertIn("lean_monte_carlo", {point.measurement for point in points})

    def test_point_to_line_protocol_escapes_tags_and_preserves_fields(self):
        module = load_module()
        point = module.InfluxPoint(
            measurement="lean_backtest_stat",
            tags={"metric": "Net Profit", "run_id": "run 1"},
            fields={"numeric_value": 6.148, "text_value": "6.148%"},
            timestamp_ns=123,
        )

        line = module.point_to_line_protocol(point)

        self.assertTrue(line.startswith("lean_backtest_stat,metric=Net\\ Profit,run_id=run\\ 1 "))
        self.assertIn("numeric_value=6.148", line)
        self.assertIn('text_value="6.148%"', line)
        self.assertTrue(line.endswith(" 123"))

    def test_influx_token_has_no_hardcoded_secret_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            module = load_module()

        self.assertIsNone(module.DEFAULT_INFLUX_TOKEN)
        with self.assertRaises(ValueError):
            module.resolve_influx_token(None)
        self.assertEqual(module.resolve_influx_token("env-token"), "env-token")


if __name__ == "__main__":
    unittest.main()
