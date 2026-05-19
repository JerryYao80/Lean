import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "soloquant_crawl_scheduler.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("soloquant_crawl_scheduler", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SoloQuantCrawlSchedulerTests(unittest.TestCase):
    def test_default_tasks_include_strategy_and_finance_intelligence(self):
        module = load_module()

        tasks = module.default_crawl_tasks()
        by_name = {task["name"]: task for task in tasks}

        self.assertIn("strategy", by_name)
        self.assertIn("finance_intelligence", by_name)
        self.assertEqual(by_name["strategy"]["category"], "strategy")
        self.assertEqual(by_name["finance_intelligence"]["category"], "finance_intelligence")
        self.assertEqual(by_name["strategy"]["interval-minutes"], 5)
        self.assertEqual(by_name["finance_intelligence"]["interval-minutes"], 5)
        self.assertGreaterEqual(len(by_name["strategy"]["keywords"]), 6)
        self.assertGreaterEqual(len(by_name["finance_intelligence"]["keywords"]), 6)
        self.assertTrue(any("公开市场业务交易公告" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))
        self.assertTrue(any("site:sse.com.cn" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))
        self.assertTrue(any("site:federalreserve.gov" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))
        self.assertTrue(any("site:ecb.europa.eu" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))
        self.assertTrue(any("site:boj.or.jp" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))
        self.assertTrue(any("site:cmegroup.com" in keyword for keyword in by_name["finance_intelligence"]["keywords"]))

    def test_scheduler_state_skips_task_until_interval_elapses(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state = module.SchedulerState(state_path)
            now = datetime(2026, 5, 10, 9, 30)

            self.assertTrue(state.should_run("strategy", interval_minutes=60, now=now))
            state.mark_finished("strategy", now=now, report={"status": "ok"})
            self.assertFalse(state.should_run("strategy", interval_minutes=60, now=now + timedelta(minutes=30)))
            self.assertTrue(state.should_run("strategy", interval_minutes=60, now=now + timedelta(minutes=61)))

    def test_load_crawl_tasks_prefers_configured_keywords_and_intervals(self):
        module = load_module()

        tasks = module.load_crawl_tasks(
            {
                "crawl-tasks": [
                    {
                        "name": "strategy",
                        "category": "strategy",
                        "keywords": ["configured alpha"],
                        "interval-minutes": 15,
                        "max-queries": 4,
                        "max-results-per-query": 2,
                    }
                ]
            }
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["keywords"], ["configured alpha"])
        self.assertEqual(tasks[0]["interval-minutes"], 15)
        self.assertEqual(tasks[0]["max-queries"], 4)
        self.assertEqual(tasks[0]["max-results-per-query"], 2)


    def test_run_due_tasks_invokes_crawl_pipeline_for_due_tasks_and_persists_state(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            config = {
                "artifact-root": str(Path(temp_dir) / "artifacts"),
                "missing-data-log": str(Path(temp_dir) / "missing.jsonl"),
                "data": {
                    "field-mapping-path": str(Path(temp_dir) / "mapping.json"),
                },
            }
            Path(config["data"]["field-mapping-path"]).write_text(json.dumps({"datasets": {}}), encoding="utf-8")
            calls = []

            def fake_pipeline(config, keywords, categories, search_client, crawl_client, glm_screen_client, run_date, max_queries, max_results_per_query, tick_offset=0):
                calls.append(
                    {
                        "keywords": keywords,
                        "categories": categories,
                        "run_date": run_date,
                        "max_queries": max_queries,
                        "max_results_per_query": max_results_per_query,
                        "screen": glm_screen_client is not None,
                        "tick_offset": tick_offset,
                    }
                )
                return {"status": "ok", "crawled": {"written_count": 1}, "screened": {"written_count": 1}}

            tasks = [
                {
                    "name": "strategy",
                    "category": "strategy",
                    "keywords": ["alpha"],
                    "interval-minutes": 60,
                    "max-queries": 3,
                }
            ]

            with mock.patch.object(module.orchestrator, "run_crawl_pipeline", side_effect=fake_pipeline):
                reports = module.run_due_tasks(
                    config=config,
                    tasks=tasks,
                    state=module.SchedulerState(state_path),
                    search_client=lambda query: [],
                    crawl_client=lambda result: {},
                    glm_screen_client=lambda payload: {},
                    now=datetime(2026, 5, 10, 9, 30),
                    run_date="20260510",
                )

            self.assertEqual(len(reports), 1)
            self.assertEqual(calls[0]["categories"], ["strategy"])
            self.assertEqual(calls[0]["keywords"], ["alpha"])
            self.assertEqual(calls[0]["max_results_per_query"], 3)
            self.assertTrue(calls[0]["screen"])
            self.assertTrue(state_path.exists())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["tasks"]["strategy"]["last_status"], "ok")

    def test_run_due_tasks_records_errors_without_raising(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            config = {
                "artifact-root": str(Path(temp_dir) / "artifacts"),
                "missing-data-log": str(Path(temp_dir) / "missing.jsonl"),
                "data": {
                    "field-mapping-path": str(Path(temp_dir) / "mapping.json"),
                },
            }
            Path(config["data"]["field-mapping-path"]).write_text(json.dumps({"datasets": {}}), encoding="utf-8")

            def failing_pipeline(*args, **kwargs):
                raise RuntimeError("searxng unavailable")

            tasks = [
                {
                    "name": "strategy",
                    "category": "strategy",
                    "keywords": ["alpha"],
                    "interval-minutes": 60,
                    "max-queries": 3,
                }
            ]

            with mock.patch.object(module.orchestrator, "run_crawl_pipeline", side_effect=failing_pipeline):
                reports = module.run_due_tasks(
                    config=config,
                    tasks=tasks,
                    state=module.SchedulerState(state_path),
                    search_client=lambda query: [],
                    crawl_client=lambda result: {},
                    glm_screen_client=lambda payload: {},
                    now=datetime(2026, 5, 10, 9, 30),
                    run_date="20260510",
                )

            self.assertEqual(reports[0]["status"], "error")
            self.assertEqual(reports[0]["task"], "strategy")
            self.assertIn("searxng unavailable", reports[0]["error"])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["tasks"]["strategy"]["last_status"], "error")

    def test_run_due_tasks_logs_start_success_and_skip(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            config = {
                "artifact-root": str(Path(temp_dir) / "artifacts"),
                "missing-data-log": str(Path(temp_dir) / "missing.jsonl"),
                "data": {
                    "field-mapping-path": str(Path(temp_dir) / "mapping.json"),
                },
            }
            Path(config["data"]["field-mapping-path"]).write_text(json.dumps({"datasets": {}}), encoding="utf-8")
            state = module.SchedulerState(state_path)
            tasks = [
                {
                    "name": "strategy",
                    "category": "strategy",
                    "keywords": ["alpha"],
                    "interval-minutes": 60,
                    "max-queries": 3,
                }
            ]

            def fake_pipeline(*args, **kwargs):
                return {
                    "status": "ok",
                    "crawled": {"written_count": 2},
                    "screened": {"written_count": 1},
                    "data_requirements": {"available": [{"field": "close"}], "missing": [{"field": "sentiment"}]},
                }

            output = io.StringIO()
            with redirect_stdout(output), mock.patch.object(module.orchestrator, "run_crawl_pipeline", side_effect=fake_pipeline):
                module.run_due_tasks(
                    config=config,
                    tasks=tasks,
                    state=state,
                    search_client=lambda query: [],
                    crawl_client=lambda result: {},
                    glm_screen_client=lambda payload: {},
                    now=datetime(2026, 5, 10, 9, 30),
                    run_date="20260510",
                    logger=module.SchedulerLogger(),
                )
                module.run_due_tasks(
                    config=config,
                    tasks=tasks,
                    state=state,
                    search_client=lambda query: [],
                    crawl_client=lambda result: {},
                    glm_screen_client=lambda payload: {},
                    now=datetime(2026, 5, 10, 9, 45),
                    run_date="20260510",
                    logger=module.SchedulerLogger(),
                )

            text = output.getvalue()
            self.assertIn("task_start name=strategy", text)
            self.assertIn("task_success name=strategy", text)
            self.assertIn("crawled=2", text)
            self.assertIn("screened=1", text)
            self.assertIn("task_skip name=strategy", text)

    def test_scheduler_logger_writes_to_log_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "scheduler.log"
            logger = module.SchedulerLogger(log_path)

            logger.info("hello", task="strategy")

            text = log_path.read_text(encoding="utf-8")
            self.assertIn("hello", text)
            self.assertIn("task=strategy", text)


if __name__ == "__main__":
    unittest.main()
