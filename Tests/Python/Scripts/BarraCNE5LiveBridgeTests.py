import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo
from datetime import datetime

import pandas as pd


def load_module(module_name: str, relative_path: str):
    module_path = Path(__file__).resolve().parents[3] / relative_path
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5LiveBridgeTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def test_live_bridge_once_generates_current_snapshot_for_csi300_universe(self):
        live_bridge = load_module("barra_cne5_live_bridge", "Scripts/barra_cne5_live_bridge.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tushare_root = root / "tushare"
            output_root = root / "live_factors"
            report_path = root / "live_bridge_report.json"

            self.write_parquet(tushare_root / "stock_basic" / "data.parquet", [
                {"ts_code": "600000.SH", "name": "浦发银行", "list_status": "L", "market": "主板", "list_date": "19991110"},
                {"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "market": "主板", "list_date": "19910403"},
                {"ts_code": "300750.SZ", "name": "宁德时代", "list_status": "L", "market": "创业板", "list_date": "20180611"},
            ])
            self.write_parquet(tushare_root / "trade_cal" / "data.parquet", [
                {"cal_date": "20260312", "is_open": 1},
            ])
            self.write_parquet(tushare_root / "index_weight" / "date=20260312" / "data.parquet", [
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "600000.SH", "weight": 1.0},
                {"index_code": "000300.SH", "trade_date": "20260312", "con_code": "000001.SZ", "weight": 1.0},
            ])

            config = live_bridge.load_live_bridge_config(overrides={
                "tushare-data-path": str(tushare_root),
                "factor-data-path": str(output_root),
                "live-factor-report-file": str(report_path),
                "factor-source-mode": "random",
                "random-factor-seed": 17,
                "universe": "csi300",
                "index-code": "000300.SH",
                "market-symbol": "000300.SH",
                "progress-interval-symbols": 1,
                "progress-interval-files": 1,
                "live-factor-poll-interval-seconds": 1,
            })

            trade_date = live_bridge.resolve_live_trade_date(
                config,
                now=datetime(2026, 3, 12, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertEqual(trade_date, "20260312")

            with mock.patch.object(live_bridge, "resolve_live_trade_date", return_value="20260312"):
                returncode = live_bridge.run_live_bridge(config, once=True)

            self.assertEqual(returncode, 0)
            self.assertTrue((output_root / "sse" / "daily" / "600000.csv").exists())
            self.assertTrue((output_root / "szse" / "daily" / "000001.csv").exists())
            self.assertFalse((output_root / "szse" / "daily" / "300750.csv").exists())

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["trade_date"], "20260312")
            self.assertEqual(report["bridge_report"]["mode"], "random")
            self.assertEqual(report["bridge_report"]["written_symbol_count"], 2)


if __name__ == "__main__":
    unittest.main()
