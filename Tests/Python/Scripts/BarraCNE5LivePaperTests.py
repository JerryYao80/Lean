import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "barra_cne5_live_paper.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("barra_cne5_live_paper", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BarraCNE5LivePaperTests(unittest.TestCase):
    def test_load_live_paper_runtime_config_resolves_data_and_results_paths(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher_dir = root / "Launcher" / "bin" / "Debug"
            launcher_dir.mkdir(parents=True, exist_ok=True)
            config_path = root / "config-barra-cne5-live-paper.json"
            config_path.write_text(json.dumps({
                "data-folder": str(root / "Data"),
                "results-destination-folder": str(root / "Results"),
                "parameters": {
                    "factor-data-path": "alternative/barra-cne5-live-factors",
                    "live-factor-report-file": "barra-cne5-live-bridge-report.json",
                    "live-price-snapshot-file": "barra-cne5-live-price-snapshot.json",
                    "daily-quote-archive-path": "archive/barra-cne5-live-daily-quotes",
                    "bridge-ready-timeout-seconds": "600",
                    "daily-summary-file": "barra-cne5-live-daily-summary.csv",
                },
            }), encoding="utf-8")

            runtime_config = module.load_live_paper_runtime_config(config_path)
            self.assertEqual(
                runtime_config["factor-data-path"],
                (root / "Data" / "alternative" / "barra-cne5-live-factors").resolve(),
            )
            self.assertEqual(
                runtime_config["live-factor-report-file"],
                (root / "Results" / "barra-cne5-live-bridge-report.json").resolve(),
            )
            self.assertEqual(
                runtime_config["live-price-snapshot-file"],
                (root / "Results" / "barra-cne5-live-price-snapshot.json").resolve(),
            )
            self.assertEqual(
                runtime_config["daily-quote-archive-path"],
                (root / "Data" / "archive" / "barra-cne5-live-daily-quotes").resolve(),
            )
            self.assertEqual(
                runtime_config["daily-summary-file"],
                (root / "Results" / "barra-cne5-live-daily-summary.csv").resolve(),
            )
            self.assertEqual(runtime_config["bridge-ready-timeout-seconds"], 600)

    def test_read_latest_csv_row_returns_last_data_row(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "daily.csv"
            csv_path.write_text(
                "trade_date,equity,holdings\n"
                "20260312,1000000,0\n"
                "20260313,1005000,5\n",
                encoding="utf-8",
            )

            row = module.read_latest_csv_row(csv_path)
            self.assertEqual(row["trade_date"], "20260313")
            self.assertEqual(row["equity"], "1005000")
            self.assertEqual(row["holdings"], "5")

    def test_build_allocation_preview_formats_latest_weights(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "allocation.csv"
            csv_path.write_text(
                "trade_date,symbol,weight\n"
                "20260312,600000.SH,0.25\n"
                "20260313,000001.SZ,0.35\n"
                "20260313,600000.SH,0.20\n",
                encoding="utf-8",
            )

            preview = module.build_allocation_preview(csv_path, limit=2)
            self.assertIn("trade_date=20260313", preview)
            self.assertIn("000001.SZ=35.00%", preview)
            self.assertIn("600000.SH=20.00%", preview)

    def test_build_signal_preview_formats_latest_trade_rows(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "trades.csv"
            csv_path.write_text(
                "trade_date,symbol,action,quantity,price,score\n"
                "20260312,600000.SH,BUY,1000,10.00,0.88\n"
                "20260313,000001.SZ,BUY,2000,12.34,1.25\n"
                "20260313,600000.SH,SELL,1000,10.56,0.45\n",
                encoding="utf-8",
            )

            preview = module.build_signal_preview(csv_path, limit=2)
            self.assertIn("trade_date=20260313", preview)
            self.assertIn("BUY 000001.SZ qty=2000 px=12.34 score=1.2500", preview)
            self.assertIn("SELL 600000.SH qty=1000 px=10.56 score=0.4500", preview)

    def test_normalize_process_output_line_rewrites_lean_statistics(self):
        module = load_module()
        line = module.normalize_process_output_line("lean", "20260313 10:00:00 TRACE:: Log: 2026-03-13 Execution mode: synthetic-only")
        stats = module.normalize_process_output_line("lean", "STATISTICS:: Net Profit 12.34%")
        data = module.normalize_process_output_line("lean", "20260313 10:00:01 TRACE:: TushareDataQueue.Emit(): seq=1 symbol=000001 ts_code=000001.SZ source=realtime_snapshot event_time_utc=2026-03-13T02:00:00.0000000Z close=10.1000 volume=1000 changed=1")
        self.assertEqual(line, "[lean] [algo] 2026-03-13 Execution mode: synthetic-only")
        self.assertEqual(stats, "[lean] [stats] Net Profit 12.34%")
        self.assertEqual(data, "[lean] [data] TushareDataQueue.Emit(): seq=1 symbol=000001 ts_code=000001.SZ source=realtime_snapshot event_time_utc=2026-03-13T02:00:00.0000000Z close=10.1000 volume=1000 changed=1")

    def test_build_market_preview_line_includes_fetch_progress(self):
        module = load_module()
        preview = module.build_market_preview_line({
            "market_preview": {
                "trade_date": "20260313",
                "rows": [
                    {"symbol": "000001.SZ", "close": 12.34, "pct_chg": 0.49},
                    {"symbol": "600000.SH", "close": 10.25, "pct_chg": 1.08},
                ],
            },
            "live_quote_report": {
                "requested_symbol_count": 300,
                "received_quote_count": 298,
                "generated_at": "2026-03-13T10:15:00+08:00",
            },
        })
        self.assertIn("trade_date=20260313", preview)
        self.assertIn("requested=300", preview)
        self.assertIn("received=298", preview)
        self.assertIn("fetched_at=2026-03-13T10:15:00+08:00", preview)


if __name__ == "__main__":
    unittest.main()
