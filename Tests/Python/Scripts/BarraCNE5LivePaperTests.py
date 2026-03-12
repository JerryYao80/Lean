import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "barra_cne5_live_paper.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("barra_cne5_live_paper", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5LivePaperTests(unittest.TestCase):
    def test_load_live_paper_runtime_config_resolves_data_and_results_paths(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher_dir = root / "Launcher" / "bin" / "Debug"
            launcher_dir.mkdir(parents=True, exist_ok=True)
            config_path = root / "config-barra-cne5-live-paper.json"
            config_path.write_text(json.dumps({
                "data-folder": str(root / "Data"),
                "results-destination-folder": str(root / "Results"),
                "parameters": {
                    "factor-data-path": "alternative/barra-cne5-live-factors",
                    "live-factor-report-file": "barra-cne5-live-bridge-report.json",
                    "daily-summary-file": "barra-cne5-live-daily-summary.csv",
                },
            }), encoding="utf-8")

            runtime_config = module.load_live_paper_runtime_config(config_path)
            self.assertEqual(
                runtime_config["factor-data-path"],
                (root / "Data" / "alternative" / "barra-cne5-live-factors").resolve(),
            )
            self.assertEqual(
                runtime_config["live-factor-report-file"],
                (root / "Results" / "barra-cne5-live-bridge-report.json").resolve(),
            )
            self.assertEqual(
                runtime_config["daily-summary-file"],
                (root / "Results" / "barra-cne5-live-daily-summary.csv").resolve(),
            )

    def test_read_latest_csv_row_returns_last_data_row(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "daily.csv"
            csv_path.write_text(
                "trade_date,equity,holdings\n"
                "20260312,1000000,0\n"
                "20260313,1005000,5\n",
                encoding="utf-8",
            )

            row = module.read_latest_csv_row(csv_path)
            self.assertEqual(row["trade_date"], "20260313")
            self.assertEqual(row["equity"], "1005000")
            self.assertEqual(row["holdings"], "5")


if __name__ == "__main__":
    unittest.main()
