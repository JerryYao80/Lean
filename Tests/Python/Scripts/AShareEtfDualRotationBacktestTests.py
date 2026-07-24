import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "ashare_etf_dual_rotation_backtest.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("ashare_etf_dual_rotation_backtest", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AShareEtfDualRotationBacktestTests(unittest.TestCase):
    def test_load_runtime_config_resolves_relative_pipeline_config(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-etf-dual-rotation-lean-backtest.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "parameters": {
                    "pipeline-config": "../pipeline/config-ashare-etf-dual-rotation-pipeline.json",
                }
            }), encoding="utf-8")

            _, pipeline_config_path = module.load_runtime_config(config_path)

        self.assertEqual(
            pipeline_config_path,
            (config_path.parent / "../pipeline/config-ashare-etf-dual-rotation-pipeline.json").resolve(),
        )

    def test_load_runtime_config_falls_back_to_default_pipeline_config(self):
        module = load_module()

        _, pipeline_config_path = module.load_runtime_config()

        self.assertTrue(str(pipeline_config_path).endswith("Launcher/config/config-ashare-etf-dual-rotation-pipeline.json"))

    def test_run_pipeline_stage_resolves_relative_output_paths_from_backtest_config(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-etf-dual-rotation-lean-backtest.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "parameters": {
                    "pipeline-config": "../pipeline/config-ashare-etf-dual-rotation-pipeline.json",
                    "plan-file": "../plans/dual_rotation.csv",
                    "benchmark-file": "../benchmark/000300.SH.csv",
                }
            }), encoding="utf-8")

            captured = {}
            original_load = module.dual_pipeline.load_pipeline_config
            original_run = module.dual_pipeline.run_pipeline
            module.dual_pipeline.load_pipeline_config = lambda path, overrides: captured.setdefault("load", {"path": path, "overrides": overrides}) or {"ok": True}
            module.dual_pipeline.run_pipeline = lambda config: {"received": config}
            try:
                module.run_pipeline_stage(config_path)
            finally:
                module.dual_pipeline.load_pipeline_config = original_load
                module.dual_pipeline.run_pipeline = original_run

        self.assertEqual(
            captured["load"]["overrides"]["plan-directory"],
            str((config_path.parent / "../plans").resolve()),
        )
        self.assertEqual(
            captured["load"]["overrides"]["benchmark-file"],
            str((config_path.parent / "../benchmark/000300.SH.csv").resolve()),
        )

    def test_run_export_stage_exports_unique_plan_symbols_into_lean_data_directory(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "Launcher" / "config" / "config-ashare-etf-dual-rotation-lean-backtest.json"
            plan_file = root / "Data" / "alternative" / "ashare-etf-dual-rotation" / "dual_rotation.csv"
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(
                "variant,signal_date,execution_date,plan_timestamp,symbol,target_weight\n"
                "dual_rotation,20240102,20240103,2024-01-02T15:00:00,510300.SH,0.5\n"
                "dual_rotation,20240102,20240103,2024-01-02T15:00:00,159919.SZ,0.5\n"
                "dual_rotation,20240103,20240104,2024-01-03T15:00:00,510300.SH,1.0\n",
                encoding="utf-8",
            )
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "data-folder": str(root / "LeanData"),
                "results-destination-folder": str(root / "Results"),
                "parameters": {
                    "pipeline-config": str(root / "pipeline.json"),
                    "plan-file": str(plan_file),
                    "start-date": "2020-01-01",
                    "end-date": "2020-12-31",
                }
            }), encoding="utf-8")
            (root / "pipeline.json").write_text("{}", encoding="utf-8")

            original_load_pipeline_config = module.dual_pipeline.load_pipeline_config
            original_export_symbol_universe = module.tushare_lean_export.export_symbol_universe
            module.dual_pipeline.load_pipeline_config = lambda *args, **kwargs: {
                "tushare-data-path": str(root / "tushare"),
            }
            captured = {}
            module.tushare_lean_export.export_symbol_universe = lambda **kwargs: captured.setdefault("kwargs", kwargs) or {"ok": True}
            try:
                module.run_export_stage(config_path)
            finally:
                module.dual_pipeline.load_pipeline_config = original_load_pipeline_config
                module.tushare_lean_export.export_symbol_universe = original_export_symbol_universe

        self.assertEqual(captured["kwargs"]["universe"], ["159919.SZ", "510300.SH"])
        self.assertEqual(captured["kwargs"]["lean_data_path"], str(root / "LeanData"))
        self.assertEqual(captured["kwargs"]["tushare_data_path"], str(root / "tushare"))
        self.assertEqual(captured["kwargs"]["start_date"], "20200101")
        self.assertEqual(captured["kwargs"]["end_date"], "20201231")


if __name__ == "__main__":
    unittest.main()
