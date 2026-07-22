import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "soloquant_job_runner.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("soloquant_job_runner", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SoloQuantJobRunnerTests(unittest.TestCase):
    def test_cron_due_matches_every_five_minutes_once_per_minute(self):
        module = load_module()
        now = datetime(2026, 5, 10, 12, 15, tzinfo=timezone.utc)

        self.assertTrue(module.is_cron_due("*/5 * * * *", now, last_finished_at=None))
        self.assertFalse(module.is_cron_due("*/5 * * * *", now, last_finished_at="2026-05-10T12:15:30+00:00"))
        self.assertFalse(module.is_cron_due("*/5 * * * *", datetime(2026, 5, 10, 12, 16, tzinfo=timezone.utc), None))

    def test_run_due_jobs_executes_selected_job_and_updates_state(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = module.JobRunnerState(root / "job-state.json")
            calls = []
            specs = [
                {
                    "name": "soloquant-export-strategy-results-influx",
                    "cron": "20 */2 * * *",
                    "cwd": str(root),
                    "command": ["/bin/echo", "export"],
                }
            ]

            def fake_runner(command, cwd, timeout_seconds=None):
                calls.append({"command": list(command), "cwd": str(cwd), "timeout_seconds": timeout_seconds})
                return 0

            reports = module.run_due_jobs(
                specs,
                state,
                runner=fake_runner,
                now=datetime(2026, 5, 10, 12, 20, tzinfo=timezone.utc),
            )

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["status"], "success")
            self.assertEqual(calls[0]["command"], ["/bin/echo", "export"])
            saved = json.loads((root / "job-state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["jobs"]["soloquant-export-strategy-results-influx"]["last_status"], "success")

    def test_run_due_jobs_skips_long_running_jobs_by_default(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state = module.JobRunnerState(Path(temp_dir) / "job-state.json")
            specs = [
                {
                    "name": "soloquant-live-paper",
                    "cron": "*/5 * * * *",
                    "cwd": temp_dir,
                    "command": ["/bin/sleep", "60"],
                    "long-running": True,
                }
            ]

            reports = module.run_due_jobs(
                specs,
                state,
                runner=lambda command, cwd, timeout_seconds=None: 0,
                now=datetime(2026, 5, 10, 12, 15, tzinfo=timezone.utc),
            )

            self.assertEqual(reports[0]["status"], "skipped")
            self.assertEqual(reports[0]["reason"], "long_running_requires_explicit_include")

    def test_run_due_jobs_records_failure_without_running_later_jobs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            state = module.JobRunnerState(Path(temp_dir) / "job-state.json")
            specs = [
                {"name": "first", "cron": "* * * * *", "cwd": temp_dir, "command": ["/bin/false"]},
                {"name": "second", "cron": "* * * * *", "cwd": temp_dir, "command": ["/bin/echo", "second"]},
            ]
            calls = []

            def fake_runner(command, cwd, timeout_seconds=None):
                calls.append(list(command))
                return 7

            reports = module.run_due_jobs(
                specs,
                state,
                runner=fake_runner,
                now=datetime(2026, 5, 10, 12, 15, tzinfo=timezone.utc),
                stop_on_error=True,
            )

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["status"], "error")
            self.assertEqual(reports[0]["returncode"], 7)
            self.assertEqual(calls, [["/bin/false"]])


if __name__ == "__main__":
    unittest.main()
