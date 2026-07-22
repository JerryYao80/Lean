import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_industry_rotation_backtest.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_industry_rotation_backtest", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareIndustryRotationBacktestTests(unittest.TestCase):
    def test_load_runtime_config_resolves_relative_pipeline_config(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-industry-rotation-lean-backtest.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "parameters": {
                    "pipeline-config": "../pipeline/config-ashare-industry-rotation-pipeline.json",
                }
            }), encoding="utf-8")

            _, pipeline_config_path = module.load_runtime_config(config_path)

        self.assertEqual(
            pipeline_config_path,
            (config_path.parent / "../pipeline/config-ashare-industry-rotation-pipeline.json").resolve(),
        )

    def test_load_runtime_config_falls_back_to_default_pipeline_config(self):
        module = load_module()

        _, pipeline_config_path = module.load_runtime_config()

        self.assertTrue(str(pipeline_config_path).endswith("Launcher/config/config-ashare-industry-rotation-pipeline.json"))

    def test_run_pipeline_stage_resolves_relative_output_paths_from_backtest_config(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-industry-rotation-lean-backtest.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "parameters": {
                    "pipeline-config": "../pipeline/config-ashare-industry-rotation-pipeline.json",
                    "plan-file": "../plans/rotation_resonance.csv",
                    "benchmark-file": "../benchmark/000300.SH.csv",
                }
            }), encoding="utf-8")

            captured = {}
            original_load = module.rotation_pipeline.load_pipeline_config
            original_run = module.rotation_pipeline.run_pipeline
            module.rotation_pipeline.load_pipeline_config = lambda path, overrides: captured.setdefault("load", {"path": path, "overrides": overrides}) or {"ok": True}
            module.rotation_pipeline.run_pipeline = lambda config: {"received": config}
            try:
                module.run_pipeline_stage(config_path)
            finally:
                module.rotation_pipeline.load_pipeline_config = original_load
                module.rotation_pipeline.run_pipeline = original_run

        self.assertEqual(
            captured["load"]["overrides"]["plan-directory"],
            str((config_path.parent / "../plans").resolve()),
        )
        self.assertEqual(
            captured["load"]["overrides"]["benchmark-file"],
            str((config_path.parent / "../benchmark/000300.SH.csv").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
