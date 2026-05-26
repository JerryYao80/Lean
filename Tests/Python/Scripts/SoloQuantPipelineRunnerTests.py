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
    def __init__(self, fail_compile=False, market_open=False):
        self.fail_compile = fail_compile
        self.market_open = market_open
        self.calls = []

    def ingest_local_strategies(self, config, run_date=None):
        self.calls.append("ingest_local_strategies")
        return {"status": "ok", "ingested": 0, "skipped": 0, "errors": 0}

    def crawl_research(self, config, run_date=None):
        self.calls.append("crawl_research")
        return {"status": "ok", "mode": "background", "action": "launched", "pid": 10001}

    def prepare_reproduction(self, config, run_date=None):
        self.calls.append("prepare_reproduction")
        return {"status": "ok", "mode": "background", "launched": [
            {"stage_key": "prepare_reproduction", "action": "launched", "pid": 10002},
            {"stage_key": "analyze_finance_intelligence", "action": "launched", "pid": 10003},
        ]}

    def prepare_iv_data(self, config, run_date=None):
        self.calls.append("prepare_iv_data")
        return {"status": "ok", "returncode": 0}

    def build_event_graph(self, config, run_date=None):
        self.calls.append("build_event_graph")
        return {"status": "ok"}

    def build_event_signals(self, config, run_date=None):
        self.calls.append("build_event_signals")
        return {"status": "ok"}

    def reproduce_one(self, config, run_date=None):
        self.calls.append("reproduce_one")
        return {"status": "ok", "mode": "background", "action": "launched", "pid": 10004}

    def compile_strategies(self, config):
        self.calls.append("compile_strategies")
        return {"status": "error" if self.fail_compile else "ok", "returncode": 1 if self.fail_compile else 0}

    def smoke_test_strategies(self, config):
        self.calls.append("smoke_test_strategies")
        return {"status": "ok", "passed_count": 0, "failed_count": 0, "skipped_count": 0}

    def materialize_variants(self, config, run_date=None):
        self.calls.append("materialize_variants")
        return {"status": "ok", "materialized_count": 1}

    def optimize_backtests(self, config, run_date=None):
        self.calls.append("optimize_backtests")
        return {"status": "ok", "mode": "background", "action": "launched", "pid": 10005}

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
                "ingest_local_strategies",
                "crawl_research",
                "prepare_reproduction",
                "prepare_iv_data",
                "build_event_graph",
                "build_event_signals",
                "reproduce_one",
                "materialize_variants",
                "optimize_backtests",
                "prepare_live_market_data",
                "update_lifecycle",
                "run_live_paper",
                "export_influx",
            ],
        )

    def test_pipeline_tick_degraded_stage_continues(self):
        """A non-LLM degradable stage failure should degrade the pipeline, not stop it."""
        module = load_module()

        class FailIvDataServices(FakeServices):
            def prepare_iv_data(self, config, run_date=None):
                self.calls.append("prepare_iv_data")
                return {"status": "error", "error": "iv data failed"}

        services = FailIvDataServices()
        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        self.assertIn("prepare_iv_data", services.calls)
        self.assertIn("prepare_iv_data", report.get("degraded_stages", []))
        self.assertIn("reproduce_one", services.calls)
        self.assertNotEqual(report["status"], "error")

    def test_pipeline_continues_with_existing_artifacts_when_crawl_is_degraded(self):
        """Background LLM stages always return ok; test that a non-LLM degradable stage
        (e.g. build_event_graph) doesn't stop the pipeline."""
        module = load_module()

        class FailEventGraphServices(FakeServices):
            def build_event_graph(self, config, run_date=None):
                self.calls.append("build_event_graph")
                return {"status": "error", "error": "graph build failed"}

        services = FailEventGraphServices()
        report = module.run_pipeline_tick({"workflow-root": "/tmp/soloquant"}, services=services)

        self.assertIn("build_event_graph", services.calls)
        self.assertIn("build_event_graph", report.get("degraded_stages", []))
        self.assertIn("reproduce_one", services.calls)
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
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {
                "workflow-root": str(root),
                "pipeline": {
                    "crawl-max-queries-per-task": 2,
                    "crawl-max-results-per-query": 1,
                },
            }

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            launched_processes = []

            def fake_popen(command, cwd, stdout, stderr, start_new_session=True, env=None):
                launched_processes.append({"command": list(command), "cwd": cwd})
                return FakeProcess(9000 + len(launched_processes))

            services = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: False,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                crawl_report = services.crawl_research(config)

            self.assertEqual(crawl_report["status"], "ok")
            self.assertEqual(crawl_report["mode"], "background")
            self.assertTrue(len(launched_processes) > 0)
            # The command is [python, soloquant_crawl_scheduler.py, ...]
            cmd_parts = " ".join(launched_processes[0]["command"])
            self.assertIn("soloquant_crawl_scheduler", cmd_parts)

            compile_report = services.compile_strategies({"strategy-policy": {"languages": ["CSharp"]}, "lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})
            live_report = services.run_live_paper({"registry-file": "/tmp/registry.json"})

            self.assertEqual(live_report["started_count"], 0)

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
        """prepare_reproduction stage launches --prepare-reproduction and
        --analyze-finance-intelligence as background processes."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "pipeline": {}, "glm": {}, "lean": {}, "data": {}}

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            launched_commands = []

            def fake_popen(command, cwd, stdout, stderr, start_new_session=True, env=None):
                launched_commands.append(list(command))
                return FakeProcess(8000 + len(launched_commands))

            services = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: False,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                report = services.prepare_reproduction(config)

            all_flags = [arg for cmd in launched_commands for arg in cmd if arg.startswith("--")]
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
        self.assertIn("reproduce_one", services.calls)
        self.assertNotEqual(report["status"], "error")


    def test_default_services_compile_strategies_python_skips_dotnet(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Algorithm.Python" / "SoloQuantGenerated").mkdir(parents=True)
            with mock.patch.object(module.orchestrator, "repo_root", return_value=root):
                report = services.compile_strategies({"strategy-policy": {"language": "Python"}, "lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})

        self.assertEqual(report["status"], "ok")
        py_result = [l for l in report["languages"] if l["language"] == "Python"][0]
        self.assertEqual(py_result["language"], "Python")
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
            # Multi-language format: check the Python entry in languages list
            py_result = [l for l in report["languages"] if l["language"] == "Python"][0]
            self.assertEqual(py_result["language"], "Python")
            self.assertTrue(len(py_result["syntax_errors"]) > 0)

    def test_default_services_reproduce_one_passes_max_items_one(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "strategy-policy": {"language": "Python"}, "pipeline": {}}

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            launched_commands = []

            def fake_popen(command, cwd, stdout, stderr, start_new_session=True, env=None):
                launched_commands.append(list(command))
                return FakeProcess(7000 + len(launched_commands))

            services = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: False,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                report = services.reproduce_one(config)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["mode"], "background")
            all_args = [arg for cmd in launched_commands for arg in cmd]
            self.assertIn("--generate-strategy-implementations", all_args)
            self.assertIn("--max-items", all_args)
            self.assertIn("--smoke-test-strategies", all_args)
            idx = all_args.index("--max-items")
            self.assertEqual(all_args[idx + 1], "1")

    def test_default_services_materialize_variants_passes_language(self):
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None):
            commands.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
            return 0

        services = module.DefaultPipelineServices(command_runner=fake_runner)
        services.materialize_variants({"strategy-policy": {"language": "Python"}})

        self.assertTrue(any("--language" in cmd["command"] and "Python" in cmd["command"] for cmd in commands))

    def test_compile_strategies_renames_broken_cs_files_not_deletes(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gen_root = root / "Algorithm.CSharp" / "SoloQuantGenerated"
            strategy_dir = gen_root / "bad-strat"
            strategy_dir.mkdir(parents=True)
            (strategy_dir / "SoloQuantGeneratedBadAlgorithm.cs").write_text("bad code", encoding="utf-8")

            def fake_runner(command, cwd, timeout_seconds=None, env=None):
                return 1

            services = module.DefaultPipelineServices(command_runner=fake_runner)

            with mock.patch.object(services, "_last_build_output", return_value=f"{strategy_dir / 'SoloQuantGeneratedBadAlgorithm.cs'}(1,1): error CS0103: The name 'bad' does not exist"):
                with mock.patch.object(module.orchestrator, "repo_root", return_value=root):
                    with mock.patch.object(module.orchestrator, "parse_broken_cs_files", return_value=[strategy_dir / "SoloQuantGeneratedBadAlgorithm.cs"]):
                        report = services.compile_strategies({"strategy-policy": {"languages": ["CSharp"]}, "lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}})

            self.assertEqual(report["status"], "error")
            # New multi-language format: languages list
            csharp_result = [l for l in report["languages"] if l["language"] == "CSharp"][0]
            self.assertIn(str(strategy_dir / "SoloQuantGeneratedBadAlgorithm.cs"), csharp_result["broken_files"])
            # .cs should be renamed to .cs.broken, not deleted
            self.assertFalse((strategy_dir / "SoloQuantGeneratedBadAlgorithm.cs").exists(), ".cs should be renamed")
            self.assertTrue((strategy_dir / "SoloQuantGeneratedBadAlgorithm.cs.broken").exists(), ".cs.broken should exist")

    def test_ingest_local_strategies_skipped_when_interval_not_elapsed(self):
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.payload["stage_timestamps"] = {
                "ingest_local_strategies": datetime.now(timezone.utc).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {"local-ingest-interval-seconds": 3600}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
            )

        self.assertNotIn("ingest_local_strategies", services.calls)
        self.assertIn("crawl_research", services.calls)

    def test_ingest_local_strategies_runs_when_interval_elapsed(self):
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.payload["stage_timestamps"] = {
                "ingest_local_strategies": (datetime.now(timezone.utc) - timedelta(seconds=3700)).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {"local-ingest-interval-seconds": 3600}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
            )

        self.assertIn("ingest_local_strategies", services.calls)

    def test_crawl_research_skipped_when_interval_not_elapsed(self):
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.payload["stage_timestamps"] = {
                "crawl_research": datetime.now(timezone.utc).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {"crawl-interval-seconds": 28800}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
            )

        self.assertIn("ingest_local_strategies", services.calls)
        self.assertNotIn("crawl_research", services.calls)
        self.assertIn("prepare_reproduction", services.calls)

    def test_crawl_research_runs_when_interval_elapsed(self):
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.payload["stage_timestamps"] = {
                "crawl_research": (datetime.now(timezone.utc) - timedelta(seconds=28900)).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {"crawl-interval-seconds": 28800}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
            )

        self.assertIn("crawl_research", services.calls)

    def test_background_llm_stage_skips_when_process_running(self):
        """If a background LLM process is still running, the stage should skip."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "pipeline": {}}

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            launched = []

            def fake_popen(command, cwd, stdout, stderr, start_new_session=True, env=None):
                launched.append(list(command))
                return FakeProcess(9100)

            # First call: process not running → launch
            services = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: False,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                report1 = services.crawl_research(config)
            self.assertEqual(report1["mode"], "background")
            self.assertEqual(report1["action"], "launched")

            # Write PID file to simulate a running process
            pid_dir = root / "llm-background"
            pid_dir.mkdir(parents=True, exist_ok=True)
            (pid_dir / "crawl_research.pid.json").write_text(
                json.dumps({"pid": 9100, "stage": "crawl_research"}), encoding="utf-8"
            )

            # Second call: process running → skip
            services2 = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: True,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                report2 = services2.crawl_research(config)
            self.assertEqual(report2["mode"], "background")
            self.assertEqual(report2["action"], "already_running")
            # Only 1 launch happened
            self.assertEqual(len(launched), 1)

    def test_background_pid_cleanup_when_process_finished(self):
        """PID file is cleaned up when the background process has finished."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "pipeline": {}}

            pid_dir = root / "llm-background"
            pid_dir.mkdir(parents=True, exist_ok=True)
            pid_file = pid_dir / "crawl_research.pid.json"
            pid_file.write_text(
                json.dumps({"pid": 9200, "stage": "crawl_research"}), encoding="utf-8"
            )

            # Process is not alive → cleanup should remove the file
            module._cleanup_finished_background("crawl_research", config)
            self.assertFalse(pid_file.exists())

    def test_background_prepare_reproduction_launches_two_processes(self):
        """prepare_reproduction launches two background processes:
        --prepare-reproduction and --analyze-finance-intelligence."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "pipeline": {}}

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid

            launched = []

            def fake_popen(command, cwd, stdout, stderr, start_new_session=True, env=None):
                launched.append(list(command))
                return FakeProcess(9300 + len(launched))

            services = module.DefaultPipelineServices(
                live_popen=fake_popen,
                live_process_alive=lambda pid: False,
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                report = services.prepare_reproduction(config)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(len(launched), 2)
            all_args = [arg for cmd in launched for arg in cmd]
            self.assertIn("--prepare-reproduction", all_args)
            self.assertIn("--analyze-finance-intelligence", all_args)

    # ── Debug mode vs Work mode ──────────────────────────────────────────────

    def test_debug_mode_runs_all_stages_without_interval_skips(self):
        """In debug mode, interval-based stage skips are bypassed — all stages run."""
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            # Set recent timestamps that would normally skip stages
            state.payload["stage_timestamps"] = {
                "ingest_local_strategies": datetime.now(timezone.utc).isoformat(),
                "crawl_research": datetime.now(timezone.utc).isoformat(),
                "reproduce_one": datetime.now(timezone.utc).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {
                    "local-ingest-interval-seconds": 3600,
                    "crawl-interval-seconds": 28800,
                    "reproduce-interval-seconds": 1800,
                }},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
                mode="debug",
            )

        self.assertEqual(report["status"], "ok")
        self.assertIn("ingest_local_strategies", services.calls)
        self.assertIn("crawl_research", services.calls)
        self.assertIn("reproduce_one", services.calls)

    def test_debug_mode_does_not_record_stage_timestamps(self):
        """In debug mode, stage timestamps are not recorded in state."""
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
                mode="debug",
            )

        # Reload state from disk
        state2 = module.PipelineState(state_path)
        self.assertEqual(state2.payload.get("stage_timestamps"), None)

    def test_work_mode_skips_stages_when_interval_not_elapsed(self):
        """In work mode, stages with recent timestamps are skipped."""
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.payload["stage_timestamps"] = {
                "ingest_local_strategies": datetime.now(timezone.utc).isoformat(),
                "crawl_research": datetime.now(timezone.utc).isoformat(),
                "reproduce_one": datetime.now(timezone.utc).isoformat(),
            }
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {
                    "local-ingest-interval-seconds": 3600,
                    "crawl-interval-seconds": 28800,
                    "reproduce-interval-seconds": 1800,
                }},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
                mode="work",
            )

        self.assertNotIn("ingest_local_strategies", services.calls)
        self.assertNotIn("crawl_research", services.calls)
        self.assertNotIn("reproduce_one", services.calls)

    def test_work_mode_records_stage_timestamps(self):
        """In work mode, stage timestamps are recorded in state."""
        module = load_module()
        services = FakeServices()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.PipelineState(state_path)
            state.save()

            report = module.run_pipeline_tick(
                {"workflow-root": "/tmp/soloquant", "pipeline": {}},
                services=services,
                now=datetime.now(timezone.utc),
                state=state,
                mode="work",
            )

            # state is mutated in-place, check directly
            timestamps = state.payload.get("stage_timestamps") or {}
            self.assertIn("ingest_local_strategies", timestamps)
            self.assertIn("crawl_research", timestamps)

    def test_debug_mode_uses_synchronous_crawl(self):
        """In debug mode, crawl_research calls _crawl_research_sync instead of Popen."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {"workflow-root": str(root), "pipeline": {"crawl-max-queries-per-task": 1, "crawl-max-results-per-query": 1}}

            ran_commands = []

            def fake_runner(command, cwd, timeout_seconds=None, env=None):
                ran_commands.append(list(command))
                return 0

            services = module.DefaultPipelineServices(
                command_runner=fake_runner,
                live_popen=lambda *a, **kw: None,
                live_process_alive=lambda pid: False,
                mode="debug",
            )
            with mock.patch.object(module.orchestrator, "repo_root", return_value=Path(temp_dir)):
                with mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                    report = services.crawl_research(config)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["mode"], "debug")
            mock_run.assert_called_once()

    def test_debug_mode_uses_synchronous_reproduce_one(self):
        """In debug mode, reproduce_one calls _reproduce_one_sync instead of Popen."""
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None, env=None):
            commands.append(list(command))
            return 0

        services = module.DefaultPipelineServices(
            command_runner=fake_runner,
            mode="debug",
        )
        config = {"workflow-root": "/tmp/soloquant", "pipeline": {}}
        with mock.patch.object(module.orchestrator, "repo_root", return_value=Path("/tmp")):
            report = services.reproduce_one(config)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["mode"], "debug")
        all_args = [arg for cmd in commands for arg in cmd]
        self.assertIn("--generate-strategy-implementations", all_args)
        self.assertIn("--smoke-test-strategies", all_args)

    def test_debug_mode_uses_synchronous_prepare_reproduction(self):
        """In debug mode, prepare_reproduction calls _prepare_reproduction_sync instead of Popen."""
        module = load_module()
        commands = []

        def fake_runner(command, cwd, timeout_seconds=None, env=None):
            commands.append(list(command))
            return 0

        services = module.DefaultPipelineServices(
            command_runner=fake_runner,
            mode="debug",
        )
        config = {"workflow-root": "/tmp/soloquant", "pipeline": {}}
        with mock.patch.object(module.orchestrator, "repo_root", return_value=Path("/tmp")):
            report = services.prepare_reproduction(config)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["mode"], "debug")
        all_args = [arg for cmd in commands for arg in cmd]
        self.assertIn("--prepare-reproduction", all_args)
        self.assertIn("--analyze-finance-intelligence", all_args)
