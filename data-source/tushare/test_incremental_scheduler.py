import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from incremental_scheduler import CORE_SCHEDULED_APIS, IncrementalScheduler


class IncrementalSchedulerTests(unittest.TestCase):
    def test_defaults_to_core_scheduled_apis(self):
        scheduler = IncrementalScheduler()
        self.assertEqual(CORE_SCHEDULED_APIS, scheduler.api_names)

    def test_partial_failures_mark_finished_target(self):
        scheduler = IncrementalScheduler(api_names=["daily"])
        with TemporaryDirectory() as temp_dir:
            scheduler.state.file_path = Path(temp_dir) / "scheduler_state.json"
            scheduler.state.state = {}
            with patch.object(scheduler, "_build_updater") as mock_build:
                updater = mock_build.return_value
                updater._resolve_target_trade_date.return_value = "20260309"
                updater.run.return_value = {"failed": 1, "completed": 1, "selected": 1}
                result = scheduler.tick()

            self.assertTrue(result)
            self.assertEqual("20260309", scheduler.state.get("last_finished_target_date"))
            self.assertEqual("partial_failed", scheduler.state.get("last_status"))


if __name__ == "__main__":
    unittest.main()
