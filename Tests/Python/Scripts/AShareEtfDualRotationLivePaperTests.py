import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_dual_rotation_live_paper.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_etf_dual_rotation_live_paper", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareEtfDualRotationLivePaperTests(unittest.TestCase):
    def test_build_bridge_command_targets_dual_rotation_live_bridge(self):
        module = load_module()

        command = module.build_bridge_command(python_executable="/usr/bin/python3")

        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertTrue(command[1].endswith("Scripts/ashare_etf_dual_rotation_live_bridge.py"))
        self.assertEqual(command[2], "--config")
        self.assertTrue(command[3].endswith("Launcher/config/config-ashare-etf-dual-rotation-live-paper.json"))

    def test_build_launcher_command_targets_dual_rotation_live_config(self):
        module = load_module()

        command, workdir = module.build_launcher_command()

        self.assertEqual(command[0], "/usr/local/dotnet/dotnet")
        self.assertTrue(command[1].endswith("Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"))
        self.assertEqual(command[2], "--config")
        self.assertTrue(command[3].endswith("Launcher/config/config-ashare-etf-dual-rotation-live-paper.json"))
        self.assertTrue(str(workdir).endswith("Launcher/bin/Debug"))

    def test_prepare_session_config_redirects_plan_outputs_into_session_root(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-etf-dual-rotation-live-paper.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "data-folder": str(root / "Data"),
                "results-destination-folder": str(root / "Results"),
                "parameters": {
                    "variant-name": "dual_rotation",
                    "pipeline-config": "../../../Launcher/config/config-ashare-etf-dual-rotation-pipeline.json",
                    "plan-directory": "alternative/ashare-etf-dual-rotation-live",
                    "plan-file": "alternative/ashare-etf-dual-rotation-live/dual_rotation.csv",
                    "benchmark-file": "alternative/ashare-etf-dual-rotation-live/benchmark/000300.SH.csv",
                    "live-bridge-report-file": "ashare-etf-dual-rotation-live-bridge-report.json",
                }
            }), encoding="utf-8")

            original_builder = module.build_live_session_root
            module.build_live_session_root = lambda: root / "Sessions" / "session-1"
            try:
                session_config, runtime_config, session_root = module.prepare_session_config(config_path)
                self.assertTrue(session_config.exists())
                self.assertTrue(str(runtime_config["plan-file"]).startswith(str(session_root)))
                self.assertTrue(str(runtime_config["benchmark-file"]).startswith(str(session_root)))
                self.assertTrue(str(runtime_config["trade-report-file"]).startswith(str(session_root)))
                self.assertTrue(str(runtime_config["live-bridge-report-file"]).startswith(str(session_root)))
                self.assertTrue(str(runtime_config["pipeline-config"]).endswith("Launcher/config/config-ashare-etf-dual-rotation-pipeline.json"))
                self.assertNotIn("/Results/", str(runtime_config["pipeline-config"]))
            finally:
                module.build_live_session_root = original_builder


if __name__ == "__main__":
    unittest.main()
