import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "soloquant_pipeline_runner.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("soloquant_pipeline_runner", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeServices:
    def __init__(self, fail_compile=False, market_open=False, fail_crawl=False, fail_prepare=False):
        self.fail_compile = fail_compile
        self.market_open = market_open
        self.fail_crawl = fail_crawl
        self.fail_prepare = fail_prepare
        self.calls = []

    def crawl_research(self, config, run_date=None):
        self.calls.append("crawl_research")
        if self.fail_crawl:
            return {"status": "error", "error": "crawl timeout"}
        return {"status": "ok", "run_date": run_date, "written_count": 1}

    def prepare_reproduction(self, config, run_date=None):
        self.calls.append("prepare_reproduction")
        if self.fail_prepare:
            return {"status": "error", "error": "glm timeout"}
        return {"status": "ok", "written_count": 1}

    def prepare_iv_data(self, config, run_date=None):
        self.calls.append("prepare_iv_data")
        return {"status": "ok", "returncode": 0}

    def build_event_graph(self, config, run_date=None):
        self.calls.append("build_event_graph")
        return {"status": "ok"}

    def build_event_signals(self, config, run_date=None):
        self.calls.append("build_event_signals")
        return {"status": "ok"}

    def generate_strategy_code(self, config, run_date=None):
        self.calls.append("generate_strategy_code")
        return {"status": "ok", "generated_count": 1}

    def compile_strategies(self, config):
        self.calls.append("compile_strategies")
        return {"status": "error" if self.fail_compile else "ok", "returncode": 1 if self.fail_compile else 0}

    def materialize_variants(self, config, run_date=None):
        self.calls.append("materialize_variants")
        return {"status": "ok", "materialized_count": 1}

    def optimize_backtests(self, config):
        self.calls.append("optimize_backtests")
        return {"status": "ok", "optimized_count": 1}

    def prepare_live_market_data(self, config, now=None):
        self.calls.append("prepare_live_market_data")
        return {
            "status": "ok",
            "source_mode": "tushare-realtime" if self.market_open else "gbm-simulated",
            "market_open": self.market_open,
        }

    def run_live_paper(self, config):
        self.calls.append("run_live_paper")
        return {"status": "ok", "run_count": 1}

    def update_lifecycle(self, config):
        self.calls.append("update_lifecycle")
        return {"status": "ok", "serving_count": 1, "retired_count": 0}

    def export_influx(self, config, run_date=None):
        self.calls.append("export_influx")
        return {"status": "ok", "written": 3}


class SoloQuantPipelineRunnerTests(unittest.TestCase):
    def test_pipeline_tick_runs_full_stage_order(self):
        module = load_module()
        services = FakeServices()

        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services, run_date="20260510")

        self.assertEqual(report["status"], "ok")
        self.assertEqual(
            services.calls,
            [
                "crawl_research",
                "prepare_reproduction",
                "prepare_iv_data",
                "build_event_graph",
                "build_event_signals",
                "generate_strategy_code",
                "compile_strategies",
                "materialize_variants",
                "optimize_backtests",
                "prepare_live_market_data",
                "update_lifecycle",
                "run_live_paper",
                "export_influx",
            ],
        )

    def test_pipeline_tick_stops_before_backtest_when_compile_fails(self):
        module = load_module()
        services = FakeServices(fail_compile=True)

        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        self.assertEqual(report["status"], "error")
        self.assertEqual(report["failed_stage"], "compile_strategies")
        self.assertNotIn("optimize_backtests", services.calls)
        self.assertNotIn("run_live_paper", services.calls)

    def test_pipeline_continues_with_existing_artifacts_when_crawl_is_degraded(self):
        module = load_module()
        services = FakeServices(fail_crawl=True, fail_prepare=True)

        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["degraded_stages"], ["crawl_research", "prepare_reproduction"])
        self.assertIn("build_event_graph", services.calls)
        self.assertIn("compile_strategies", services.calls)
        self.assertIn("run_live_paper", services.calls)
        self.assertIn("export_influx", services.calls)

    def test_pipeline_is_due_every_five_minutes(self):
        module = load_module()
        now = datetime(2026, 5, 10, 12, 10, tzinfo=timezone.utc)

        self.assertTrue(module.is_pipeline_due(now=now, last_finished_at=None, interval_seconds=300))
        self.assertFalse(
            module.is_pipeline_due(
                now=now,
                last_finished_at=(now - timedelta(seconds=299)).isoformat(),
                interval_seconds=300,
            )
        )
        self.assertTrue(
            module.is_pipeline_due(
                now=now,
                last_finished_at=(now - timedelta(seconds=300)).isoformat(),
                interval_seconds=300,
            )
        )

    def test_non_trading_session_uses_gbm_live_market_data(self):
        module = load_module()
        services = FakeServices(market_open=False)

        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        market_stage = next(stage for stage in report["stages"] if stage["stage"] == "prepare_live_market_data")
        self.assertEqual(market_stage["report"]["source_mode"], "gbm-simulated")

    def test_update_strategy_lifecycle_marks_serving_and_retired(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {"strategy_id": "winner", "best_score": 0.34, "live_score": 0.31},
                            {"strategy_id": "decayed", "best_score": 0.28, "live_score": -0.05},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = module.update_strategy_lifecycle(
                registry_path,
                serving_score_threshold=0.1,
                degradation_threshold=0.2,
                now=datetime(2026, 5, 10, 12, 10, tzinfo=timezone.utc),
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            by_id = {item["strategy_id"]: item for item in registry["strategies"]}

            self.assertEqual(report["serving_count"], 1)
            self.assertEqual(report["retired_count"], 1)
            self.assertEqual(by_id["winner"]["status"], "serving")
            self.assertEqual(by_id["decayed"]["status"], "retired")

    def test_default_services_build_expected_real_commands(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        crawl_report = services.crawl_research(
            {
                "pipeline": {
                    "crawl-max-queries-per-task": 2,
                    "crawl-max-results-per-query": 1,
                }
            }
        )
        compile_report = services.compile_strategies({"lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})
        live_report = services.run_live_paper({"registry-file": "/tmp/registry.json"})

        self.assertEqual(crawl_report["returncode"], 0)
        self.assertEqual(compile_report["returncode"], 0)
        self.assertEqual(live_report["started_count"], 0)
        self.assertIn("--max-queries-per-task", commands[0]["command"])
        self.assertIn("2", commands[0]["command"])
        self.assertIn("--max-results-per-query", commands[0]["command"])
        self.assertEqual(commands[1]["command"][:3], ["/usr/local/dotnet/dotnet", "build", "Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj"])

    def test_start_registered_live_paper_strategies_starts_all_non_retired_without_duplicates(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_a = root / "live-a.json"
            live_b = root / "live-b.json"
            live_a.write_text(json.dumps({"environment": "live-paper"}), encoding="utf-8")
            live_b.write_text(json.dumps({"environment": "live-paper"}), encoding="utf-8")
            registry_path = root / "registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {"strategy_id": "alpha-a", "status": "serving", "live-paper-config": str(live_a), "best_score": 0.2},
                            {"strategy_id": "alpha-b", "status": "candidate", "live-paper-config": str(live_b), "best_score": 0.1},
                            {"strategy_id": "old", "status": "retired", "live-paper-config": str(live_b), "best_score": 0.5},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            started = []

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            def fake_popen(command, cwd, stdout, stderr, start_new_session, env=None):
                started.append({"command": list(command), "cwd": str(cwd), "stdout": stdout.name, "stderr": stderr.name, "start_new_session": start_new_session})
                return FakeProcess(9000 + len(started))

            report = module.start_registered_live_paper_strategies(
                registry_path,
                process_root=root / "processes",
                popen=fake_popen,
            )
            second = module.start_registered_live_paper_strategies(
                registry_path,
                process_root=root / "processes",
                popen=fake_popen,
                process_alive=lambda pid: True,
            )

            self.assertEqual(report["started_count"], 2)
            self.assertEqual(report["skipped_count"], 1)
            self.assertEqual(second["started_count"], 0)
            self.assertEqual(len(started), 2)
            self.assertTrue(all(item["start_new_session"] for item in started))
            self.assertTrue(all(command["command"][0] == "/usr/local/dotnet/dotnet" for command in started))

    def test_prepare_registered_live_configs_points_to_gbm_snapshot(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_config = root / "live.json"
            snapshot = root / "gbm-snapshot.json"
            live_config.write_text(json.dumps({"parameters": {"universe": "000001.SZ"}}), encoding="utf-8")
            registry = root / "registry.json"
            registry.write_text(
                json.dumps({"strategies": [{"strategy_id": "alpha", "status": "serving", "live-paper-config": str(live_config)}]}),
                encoding="utf-8",
            )

            report = module.prepare_registered_live_configs_for_market_data(registry, snapshot)
            payload = json.loads(live_config.read_text(encoding="utf-8"))

            self.assertEqual(report["updated_count"], 1)
            self.assertEqual(payload["parameters"]["live-price-snapshot-file"], str(snapshot))
            self.assertEqual(payload["parameters"]["live-price-source-mode"], "auto")


    # ── 变更三：pipeline 解耦 ──────────────────────────────────────────────

    def test_prepare_reproduction_does_not_call_build_finance_event_graph(self):
        """prepare_reproduction stage should only call --prepare-reproduction and
        --analyze-finance-intelligence; --build-finance-event-graph must be its own stage."""
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append(list(command))
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        services.prepare_reproduction({"pipeline": {}, "glm": {}, "lean": {}, "data": {}})

        # Collect all flag-style args (starting with --) from all commands
        all_flags = [arg for cmd in commands for arg in cmd if arg.startswith("--")]
        self.assertIn("--prepare-reproduction", all_flags)
        self.assertIn("--analyze-finance-intelligence", all_flags)
        self.assertNotIn("--build-finance-event-graph", all_flags)

    def test_pipeline_stages_include_build_event_graph(self):
        module = load_module()
        self.assertIn("build_event_graph", module.PIPELINE_STAGES)
        self.assertIn("build_event_signals", module.PIPELINE_STAGES)
        # build_event_signals must come after build_event_graph
        stages = list(module.PIPELINE_STAGES)
        self.assertLess(stages.index("build_event_graph"), stages.index("build_event_signals"))

    def test_build_event_graph_stage_is_degraded_continue(self):
        """build_event_graph failure should degrade the pipeline, not stop it."""
        module = load_module()

        class FailEventGraphServices(FakeServices):
            def build_event_graph(self, config, run_date=None):
                self.calls.append("build_event_graph")
                return {"status": "error", "error": "graph build failed"}

        services = FailEventGraphServices()
        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        self.assertIn("build_event_graph", services.calls)
        self.assertIn("build_event_graph", report.get("degraded_stages", []))
        self.assertIn("compile_strategies", services.calls)
        self.assertNotEqual(report["status"], "error")


    def test_default_services_compile_strategies_python_skips_dotnet(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        report = services.compile_strategies({"strategy-policy": {"language": "Python"}, "lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["language"], "Python")
        self.assertEqual(len(commands), 0)

    def test_default_services_compile_strategies_python_detects_syntax_errors(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            py_dir = root / "Algorithm.Python" / "SoloQuantGenerated" / "bad-strat"
            py_dir.mkdir(parents=True)
            (py_dir / "SoloQuantGeneratedBadAlgorithm.py").write_text(
                "class SoloQuantGeneratedBadAlgorithm(QCAlgorithm):\n    def Initialize(self\n        pass\n",
                encoding="utf-8",
            )

            services = module.DefaultPipelineServices(command_runner=lambda *a, **kw: 0)
            with mock.patch.object(module.orchestrator, "repo_root", return_value=root):
                report = services.compile_strategies({"strategy-policy": {"language": "Python"}, "lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})

            self.assertEqual(report["status"], "error")
            self.assertEqual(report["language"], "Python")
            self.assertTrue(len(report["syntax_errors"]) > 0)

    def test_default_services_generate_strategy_code_passes_language(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        services.generate_strategy_code({"strategy-policy": {"language": "Python"}, "pipeline": {}})

        self.assertTrue(any("--language" in cmd["command"] and "Python" in cmd["command"] for cmd in commands))

    def test_default_services_materialize_variants_passes_language(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        services.materialize_variants({"strategy-policy": {"language": "Python"}})

        self.assertTrue(any("--language" in cmd["command"] and "Python" in cmd["command"] for cmd in commands))


if __name__ == "__main__":
    unittest.main()
