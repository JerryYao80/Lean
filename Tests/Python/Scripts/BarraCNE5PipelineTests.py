import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "barra_cne5_pipeline.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("barra_cne5_pipeline", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5PipelineTests(unittest.TestCase):
    def test_pipeline_reuses_existing_factors_and_runs_remaining_stages(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            factor_root = root / "factors" / "sse" / "daily"
            factor_root.mkdir(parents=True, exist_ok=True)
            (factor_root / "600000.csv").write_text(
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n"
                "20240131,0,0,0,0,0,0,0,0,0,0,1,1,1,0,0\n",
                encoding="utf-8",
            )
            launcher = root / "QuantConnect.Lean.Launcher"
            launcher.write_text("", encoding="utf-8")
            backtest_config = root / "config.json"
            backtest_config.write_text("{}", encoding="utf-8")

            config = module.load_pipeline_config(overrides={
                "factor-output-path": str(root / "factors"),
                "launcher-binary": str(launcher),
                "backtest-config": str(backtest_config),
                "pipeline-report-file": str(root / "pipeline-report.json"),
            })

            with mock.patch.object(module, "run_subprocess_with_heartbeat", return_value=(0, 0.0)) as run_mock, \
                 mock.patch.object(module.barra_cne5_monte_carlo, "run_monte_carlo", return_value={"scenarios": {}}):
                report = module.run_pipeline(config)

            self.assertEqual(report["stages"]["factor"]["mode"], "reuse")
            self.assertEqual(report["stages"]["backtest"]["returncode"], 0)
            run_mock.assert_called_once()
            self.assertTrue(Path(config["pipeline-report-file"]).exists())

    def test_pipeline_uses_external_factor_import_when_requested(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher = root / "QuantConnect.Lean.Launcher"
            launcher.write_text("", encoding="utf-8")
            backtest_config = root / "config.json"
            backtest_config.write_text("{}", encoding="utf-8")

            config = module.load_pipeline_config(overrides={
                "factor-source-mode": "import",
                "external-factor-path": str(root / "incoming"),
                "factor-output-path": str(root / "factors"),
                "launcher-binary": str(launcher),
                "backtest-config": str(backtest_config),
                "pipeline-report-file": str(root / "pipeline-report.json"),
            })

            with mock.patch.object(module.barra_cne5_factor_bridge, "run_factor_bridge", return_value={"status": "ok", "mode": "import"}), \
                 mock.patch.object(module, "run_subprocess_with_heartbeat", return_value=(0, 0.0)), \
                 mock.patch.object(module.barra_cne5_monte_carlo, "run_monte_carlo", return_value={"scenarios": {}}):
                report = module.run_pipeline(config)

            self.assertEqual(report["stages"]["factor"]["mode"], "import")

    def test_pipeline_uses_random_factor_generation_when_requested(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher = root / "QuantConnect.Lean.Launcher"
            launcher.write_text("", encoding="utf-8")
            backtest_config = root / "config.json"
            backtest_config.write_text("{}", encoding="utf-8")

            config = module.load_pipeline_config(overrides={
                "factor-source-mode": "random",
                "random-factor-seed": 11,
                "factor-output-path": str(root / "factors"),
                "launcher-binary": str(launcher),
                "backtest-config": str(backtest_config),
                "pipeline-report-file": str(root / "pipeline-report.json"),
            })

            with mock.patch.object(module.barra_cne5_factor_bridge, "run_factor_bridge", return_value={"status": "ok", "mode": "random", "random_factor_seed": 11}) as factor_mock, \
                 mock.patch.object(module, "run_subprocess_with_heartbeat", return_value=(0, 0.0)), \
                 mock.patch.object(module.barra_cne5_monte_carlo, "run_monte_carlo", return_value={"scenarios": {}}):
                report = module.run_pipeline(config)

            self.assertEqual(report["stages"]["factor"]["mode"], "random")
            bridge_config = factor_mock.call_args.args[0]
            self.assertEqual(bridge_config["factor-source-mode"], "random")
            self.assertEqual(bridge_config["random-factor-seed"], 11)

    def test_pipeline_uses_environment_variable_for_external_factor_path(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            incoming = root / "incoming"
            incoming.mkdir(parents=True, exist_ok=True)
            (incoming / "barra_snapshot.csv").write_text("trade_date,ticker,beta\n20240131,600000,-0.1\n", encoding="utf-8")
            launcher = root / "QuantConnect.Lean.Launcher"
            launcher.write_text("", encoding="utf-8")
            backtest_config = root / "config.json"
            backtest_config.write_text("{}", encoding="utf-8")

            config = module.load_pipeline_config(overrides={
                "external-factor-path": None,
                "factor-output-path": str(root / "factors"),
                "launcher-binary": str(launcher),
                "backtest-config": str(backtest_config),
                "pipeline-report-file": str(root / "pipeline-report.json"),
            })

            with mock.patch.dict(module.os.environ, {"BARRA_CNE5_EXTERNAL_FACTOR_PATH": str(incoming)}, clear=False), \
                 mock.patch.object(module.barra_cne5_factor_bridge, "run_factor_bridge", return_value={"status": "ok", "mode": "import"}), \
                 mock.patch.object(module, "run_subprocess_with_heartbeat", return_value=(0, 0.0)), \
                 mock.patch.object(module.barra_cne5_monte_carlo, "run_monte_carlo", return_value={"scenarios": {}}):
                report = module.run_pipeline(config)

            self.assertEqual(report["config"]["external-factor-path"], str(incoming.resolve()))
            self.assertEqual(report["stages"]["factor"]["mode"], "import")

    def test_pipeline_prints_progress_and_stage_context(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            factor_root = root / "factors" / "sse" / "daily"
            factor_root.mkdir(parents=True, exist_ok=True)
            (factor_root / "600000.csv").write_text(
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n"
                "20240131,0,0,0,0,0,0,0,0,0,0,1,1,1,0,0\n",
                encoding="utf-8",
            )
            launcher = root / "QuantConnect.Lean.Launcher"
            launcher.write_text("", encoding="utf-8")
            backtest_config = root / "config.json"
            backtest_config.write_text("{}", encoding="utf-8")

            config = module.load_pipeline_config(overrides={
                "factor-output-path": str(root / "factors"),
                "launcher-binary": str(launcher),
                "backtest-config": str(backtest_config),
                "pipeline-report-file": str(root / "pipeline-report.json"),
            })

            output = io.StringIO()
            with redirect_stdout(output), \
                 mock.patch.object(module, "run_subprocess_with_heartbeat", return_value=(0, 0.0)), \
                 mock.patch.object(module.barra_cne5_monte_carlo, "run_monte_carlo", return_value={"scenarios": {}}):
                module.run_pipeline(config)

            text = output.getvalue()
            self.assertIn("Barra CNE5 Pipeline", text)
            self.assertIn("[1/3 | 0% -> 33%] Factor Preparation", text)
            self.assertIn("[2/3 | 33% -> 66%] LEAN Backtest", text)
            self.assertIn("[3/3 | 66% -> 100%] Monte Carlo Analysis", text)
            self.assertIn("Heartbeat", text)
            self.assertIn("Pipeline Completed", text)

    def test_run_subprocess_with_heartbeat_prints_running_status(self):
        module = load_module()

        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self._poll_count = 0

            def poll(self):
                self._poll_count += 1
                if self._poll_count == 1:
                    return None
                self.returncode = 0
                return 0

        output = io.StringIO()
        with redirect_stdout(output), \
             mock.patch.object(module.subprocess, "Popen", return_value=FakeProcess()), \
             mock.patch.object(module.time, "monotonic", side_effect=[0.0, 1.1, 2.0]), \
             mock.patch.object(module.time, "sleep", return_value=None):
            returncode, elapsed = module.run_subprocess_with_heartbeat(["fake-launcher"], Path("/tmp"), heartbeat_seconds=1)

        text = output.getvalue()
        self.assertEqual(returncode, 0)
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertIn("[backtest launch] command=fake-launcher", text)
        self.assertIn("[backtest heartbeat] status=running", text)
        self.assertIn("[backtest complete] returncode=0", text)


if __name__ == "__main__":
    unittest.main()
