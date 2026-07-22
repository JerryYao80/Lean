import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "barra_cne5_monte_carlo.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("barra_cne5_monte_carlo", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5MonteCarloTests(unittest.TestCase):
    def test_block_bootstrap_returns_respects_shape(self):
        module = load_module()
        paths = module.block_bootstrap_returns(
            [0.01, -0.02, 0.03],
            trial_count=4,
            horizon_days=6,
            block_size=2,
            seed=7,
        )

        self.assertEqual(len(paths), 4)
        self.assertTrue(all(len(path) == 6 for path in paths))
        self.assertTrue(all(value in [0.01, -0.02, 0.03] for path in paths for value in path))

    def test_factor_perturbation_returns_uses_exposures(self):
        module = load_module()
        daily = pd.DataFrame([
            {"trade_date": "20240101", "net_return": 0.01},
            {"trade_date": "20240102", "net_return": -0.02},
            {"trade_date": "20240103", "net_return": 0.03},
        ])
        exposures = pd.DataFrame([
            {"trade_date": "20240101", "beta": -0.2, "momentum": 0.4},
            {"trade_date": "20240102", "beta": 0.5, "momentum": -0.1},
            {"trade_date": "20240103", "beta": -0.1, "momentum": 0.3},
        ])

        paths = module.factor_perturbation_returns(
            daily=daily,
            exposures=exposures,
            trial_count=5,
            horizon_days=4,
            seed=11,
            perturbation_scale=0.1,
        )

        self.assertEqual(len(paths), 5)
        self.assertTrue(all(len(path) == 4 for path in paths))

    def test_run_monte_carlo_writes_reports(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            daily_path = root / "daily.csv"
            exposure_path = root / "exposure.csv"
            report_path = root / "report.txt"
            json_report_path = root / "report.json"

            pd.DataFrame([
                {"trade_date": "20240101", "net_return": 0.01},
                {"trade_date": "20240102", "net_return": -0.02},
                {"trade_date": "20240103", "net_return": 0.03},
                {"trade_date": "20240104", "net_return": 0.00},
                {"trade_date": "20240105", "net_return": 0.01},
            ]).to_csv(daily_path, index=False)
            pd.DataFrame([
                {"trade_date": "20240101", "beta": -0.2, "momentum": 0.4, "size": -0.1},
                {"trade_date": "20240102", "beta": 0.5, "momentum": -0.1, "size": 0.2},
                {"trade_date": "20240103", "beta": -0.1, "momentum": 0.3, "size": -0.3},
                {"trade_date": "20240104", "beta": 0.0, "momentum": 0.2, "size": 0.1},
                {"trade_date": "20240105", "beta": -0.3, "momentum": 0.1, "size": -0.2},
            ]).to_csv(exposure_path, index=False)

            config = {
                "daily-summary-file": str(daily_path),
                "factor-exposure-file": str(exposure_path),
                "trial-count": 32,
                "horizon-days": 10,
                "block-size": 3,
                "seed": 23,
                "factor-perturbation-scale": 0.1,
                "report-file": str(report_path),
                "json-report-file": str(json_report_path),
                "progress-interval-trials": 8,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                report = module.run_monte_carlo(module.load_pipeline_config(config_path))

            self.assertTrue(report_path.exists())
            self.assertTrue(json_report_path.exists())
            self.assertIn("block_bootstrap", report["scenarios"])
            self.assertIn("factor_perturbation", report["scenarios"])
            self.assertIn("combined", report["scenarios"])
            stdout_text = output.getvalue()
            self.assertIn("Barra CNE5 Monte Carlo", stdout_text)
            self.assertIn("[scenario block_bootstrap]", stdout_text)
            self.assertIn("[block bootstrap] trials", stdout_text)
            self.assertIn("[factor perturbation] trials", stdout_text)
            self.assertIn("[write report] json_report=", stdout_text)


if __name__ == "__main__":
    unittest.main()
