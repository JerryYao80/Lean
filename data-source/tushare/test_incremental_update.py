import unittest
from unittest.mock import patch

import pandas as pd

from api_registry import APIConfig, ChunkStrategy
from incremental_update import IncrementalUpdater, SkipIncrementalAPI, choose_start_date, dedupe_api_configs, merge_frames


class IncrementalUpdateTests(unittest.TestCase):
    def test_dedupe_api_configs_keeps_first_definition(self):
        first = APIConfig(api_name="trade_cal", description="SSE", category="stock_basic")
        second = APIConfig(api_name="trade_cal", description="DCE", category="futures")

        deduped = dedupe_api_configs([first, second])

        self.assertEqual(1, len(deduped))
        self.assertEqual("stock_basic", deduped[0].category)

    def test_choose_start_date_keeps_recent_lookback_refresh(self):
        open_dates = ["20260302", "20260303", "20260304", "20260305", "20260306"]

        start_date = choose_start_date(
            open_dates=open_dates,
            latest_completed_date="20260304",
            target_date="20260306",
            bootstrap_trade_days=40,
            lookback_trade_days=3,
        )

        self.assertEqual("20260304", start_date)

    def test_merge_frames_dedupes_by_ts_code_and_trade_date(self):
        api_config = APIConfig(
            api_name="daily",
            description="日线行情",
            chunk_strategy=ChunkStrategy.STOCK,
            code_field="ts_code",
            date_field="trade_date",
        )
        existing_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260305", "close": 10.0},
                {"ts_code": "000001.SZ", "trade_date": "20260306", "close": 10.2},
            ]
        )
        new_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260306", "close": 10.3},
                {"ts_code": "000001.SZ", "trade_date": "20260307", "close": 10.4},
            ]
        )

        merged_df = merge_frames(existing_df, new_df, api_config)

        self.assertEqual(3, len(merged_df))
        latest_row = merged_df.loc[merged_df["trade_date"] == "20260306"].iloc[0]
        self.assertEqual(10.3, latest_row["close"])

    def test_run_marks_permission_denied_as_skipped(self):
        api_config = APIConfig(
            api_name="stk_premarket",
            description="每日股本（盘前）",
            chunk_strategy=ChunkStrategy.DATE,
            date_field="trade_date",
            category="stock_basic",
        )
        updater = IncrementalUpdater(dry_run=True)

        with patch.object(updater, "_resolve_target_trade_date", return_value="20260309"), \
             patch.object(updater, "_select_api_configs", return_value=[api_config]), \
             patch.object(updater, "_run_single_api", side_effect=SkipIncrementalAPI("stk_premarket skipped (date=20260309): no permission")):
            summary = updater.run()

        self.assertEqual(0, summary["failed"])
        self.assertEqual(1, summary["skipped"])
        self.assertEqual("skipped", summary["details"]["stk_premarket"]["status"])


if __name__ == "__main__":
    unittest.main()
