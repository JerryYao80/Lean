import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
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
                    "portfolio-state-file": "barra-cne5-live-state.json",
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
            self.assertEqual(runtime_config["live-price-max-requests-per-minute"], "50")
            self.assertEqual(runtime_config["initial-cash"], "100000")
            self.assertEqual(
                runtime_config["portfolio-state-file"],
                (root / "Results" / "barra-cne5-live-state.json").resolve(),
            )

    def test_prepare_session_config_creates_isolated_output_paths(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config-barra-cne5-live-paper.json"
            state_path = root / "Results" / "barra-cne5-live-state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "data-folder": str(root / "Data"),
                "results-destination-folder": str(root / "Results"),
                "parameters": {
                    "factor-data-path": "alternative/barra-cne5-live-factors",
                    "live-factor-report-file": "barra-cne5-live-bridge-report.json",
                    "live-price-snapshot-file": "barra-cne5-live-price-snapshot.json",
                    "portfolio-state-file": str(state_path),
                    "daily-quote-archive-path": "archive/barra-cne5-live-daily-quotes",
                    "daily-summary-file": "barra-cne5-live-daily-summary.csv",
                    "allocation-report-file": "barra-cne5-live-allocation.csv",
                    "factor-exposure-file": "barra-cne5-live-factor-exposure.csv",
                    "trade-report-file": "barra-cne5-live-trades.csv",
                },
            }), encoding="utf-8")

            original_builder = module.build_live_session_root
            module.build_live_session_root = lambda: root / "Sessions" / "session-1"
            try:
                session_config, runtime_config, session_root = module.prepare_session_config(config_path)
            finally:
                module.build_live_session_root = original_builder
            self.assertTrue(session_config.exists())
            self.assertTrue(str(runtime_config["daily-summary-file"]).startswith(str(session_root)))
            self.assertTrue(str(runtime_config["trade-report-file"]).startswith(str(session_root)))
            self.assertTrue(str(runtime_config["factor-data-path"]).startswith(str(session_root)))
            self.assertEqual(runtime_config["portfolio-state-file"], state_path.resolve())

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
            self.assertIn("| Symbol", preview)
            self.assertIn("000001.SZ", preview)
            self.assertIn("35.00%", preview)
            self.assertIn("600000.SH", preview)
            self.assertIn("20.00%", preview)

    def test_build_signal_preview_formats_latest_trade_rows(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "trades.csv"
            csv_path.write_text(
                "trade_date,executed_at,symbol,action,quantity,quantity_before,quantity_after,price,score\n"
                "20260312,2026-03-12 10:00:00,600000.SH,BUY,1000,0,1000,10.00,0.88\n"
                "20260313,2026-03-13 10:01:00,000001.SZ,BUY,2000,0,2000,12.34,1.25\n"
                "20260313,2026-03-13 10:01:00,600000.SH,SELL,1000,1500,500,10.56,0.45\n",
                encoding="utf-8",
            )

            preview = module.build_signal_preview(csv_path, limit=2)
            self.assertIn("trade_date=20260313", preview)
            self.assertIn("| Time", preview)
            self.assertIn("| Action", preview)
            self.assertIn("BUY", preview)
            self.assertIn("000001.SZ", preview)
            self.assertIn("2,000", preview)
            self.assertIn("12.34", preview)
            self.assertIn("1.2500", preview)
            self.assertIn("SELL", preview)
            self.assertIn("600000.SH", preview)
            self.assertIn("1,500", preview)
            self.assertIn("500", preview)
            self.assertIn("10.56", preview)
            self.assertIn("0.4500", preview)

    def test_build_account_summary_formats_core_asset_metrics(self):
        module = load_module()
        runtime_config = {"initial-cash": "100000"}
        daily_row = {
            "trade_date": "20260316",
            "equity": "100500.00",
            "cash": "20000.00",
            "invested": "80500.00",
            "holdings": "3",
            "eligible_symbols": "300",
            "selected_symbols": "30",
            "turnover": "0.1250",
        }
        snapshot_payload = {
            "quote_count": 242,
            "quotes": [
                {"fetch_timestamp": "2026-03-16T10:31:00+08:00"},
            ],
        }

        preview = module.build_account_summary(runtime_config, daily_row, snapshot_payload, live_quote_count=242)
        self.assertIn("trade_date=20260316", preview)
        self.assertIn("Initial Cash", preview)
        self.assertIn("¥100,000.00", preview)
        self.assertIn("Current Equity", preview)
        self.assertIn("¥100,500.00", preview)
        self.assertIn("PnL%", preview)
        self.assertIn("+0.50%", preview)
        self.assertIn("Live Quotes", preview)
        self.assertIn("242", preview)

    def test_normalize_process_output_line_rewrites_lean_statistics(self):
        module = load_module()
        line = module.normalize_process_output_line("lean", "20260313 10:00:00 TRACE:: Log: 2026-03-13 Execution mode: synthetic-only")
        stats = module.normalize_process_output_line("lean", "STATISTICS:: Net Profit 12.34%")
        data = module.normalize_process_output_line("lean", "20260313 10:00:01 TRACE:: TushareDataQueue.Emit(): seq=1 symbol=000001 ts_code=000001.SZ source=realtime_snapshot event_time_utc=2026-03-13T02:00:00.0000000Z close=10.1000 volume=1000 changed=1")
        refresh = module.normalize_process_output_line("lean", "20260313 10:00:01 TRACE:: TushareDataQueue.Refresh(): cycle=2 reason=timer subscribed=300 published=40 missing=0 unchanged=260")
        self.assertEqual(line, "[lean] [algo] 2026-03-13 Execution mode: synthetic-only")
        self.assertEqual(stats, "[lean] [stats] Net Profit 12.34%")
        self.assertIsNone(data)
        self.assertEqual(refresh, "[lean] [data] TushareDataQueue.Refresh(): cycle=2 reason=timer subscribed=300 published=40 missing=0 unchanged=260")

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
        self.assertIn("| Symbol", preview)

    def test_build_factor_source_line_reports_external_seed_source(self):
        module = load_module()
        line = module.build_factor_source_line({
            "trade_date": "20260317",
            "bridge_report": {
                "mode": "external-seed",
                "external_factor_path": "/tmp/factors",
                "resolved_symbol_count": 300,
                "written_symbol_count": 300,
                "carry_forward_symbol_count": 12,
                "missing_symbol_count": 0,
            },
        })
        self.assertIn("trade_date=20260317", line)
        self.assertIn("mode=external-seed", line)
        self.assertIn("path=/tmp/factors", line)
        self.assertIn("resolved=300", line)
        self.assertIn("written=300", line)

    def test_build_no_new_trade_line_summarizes_latest_execution(self):
        module = load_module()
        line = module.build_no_new_trade_line([
            {
                "executed_at": "2026-03-17 09:35:01",
                "symbol": "600061.SH",
                "action": "BUY",
            },
            {
                "executed_at": "2026-03-17 09:44:01",
                "symbol": "600958.SH",
                "action": "SELL",
            },
        ])
        self.assertIn("no new executions in this cycle", line)
        self.assertIn("total_rows=2", line)
        self.assertIn("last_execution=2026-03-17 09:44:01", line)
        self.assertIn("last_symbol=600958.SH", line)
        self.assertIn("last_action=SELL", line)

    def test_print_runtime_status_does_not_repeat_historical_trade_rows(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            factor_dir = root / "factor-data"
            factor_dir.mkdir(parents=True, exist_ok=True)
            bridge_report = root / "bridge.json"
            snapshot_file = root / "snapshot.json"
            daily_file = root / "daily.csv"
            trade_file = root / "trades.csv"
            allocation_file = root / "allocation.csv"
            exposure_file = root / "exposure.csv"

            bridge_report.write_text(json.dumps({
                "trade_date": "20260317",
                "bridge_report": {
                    "mode": "external-seed",
                    "external_factor_path": "/tmp/factors",
                    "resolved_symbol_count": 300,
                    "written_symbol_count": 300,
                    "carry_forward_symbol_count": 300,
                    "missing_symbol_count": 0,
                },
                "live_quote_report": {
                    "requested_symbol_count": 300,
                    "received_quote_count": 300,
                    "refreshed_quote_count": 300,
                    "carried_forward_quote_count": 0,
                    "estimated_full_refresh_minutes": 3,
                },
            }), encoding="utf-8")
            snapshot_file.write_text(json.dumps({
                "quote_count": 300,
                "quotes": [
                    {
                        "ts_code": "600061.SH",
                        "close": 7.39,
                        "pre_close": 7.31,
                        "fetch_timestamp": "2026-03-17T11:13:01.941002+08:00",
                    },
                ],
            }), encoding="utf-8")
            daily_file.write_text(
                "trade_date,equity,cash,invested,holdings,eligible_symbols,selected_symbols,turnover,rebalanced\n"
                "20260317,100046.30,52735.30,47311.00,18,300,30,0.1267,1\n",
                encoding="utf-8",
            )
            trade_file.write_text(
                "trade_date,executed_at,symbol,action,quantity,quantity_before,quantity_after,price,trade_value,fee,score\n"
                "20260317,2026-03-17 09:35:01,600061.SH,BUY,400,0,400,7.31,2924.00,5.06,0.5683\n",
                encoding="utf-8",
            )
            allocation_file.write_text(
                "trade_date,symbol,weight,quantity,price,market_price,market_value,score\n"
                "20260317,600061.SH,0.0295,400,7.31,7.39,2956.00,0.5683\n",
                encoding="utf-8",
            )
            exposure_file.write_text(
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize\n"
                "20260317,0.302,0.799,-0.312,0.544,-0.524,0.429,0.179,-0.118,0.568,-0.369\n",
                encoding="utf-8",
            )

            runtime_config = {
                "initial-cash": "100000",
                "factor-data-path": factor_dir,
                "live-factor-report-file": bridge_report,
                "live-price-snapshot-file": snapshot_file,
                "daily-summary-file": daily_file,
                "trade-report-file": trade_file,
                "allocation-report-file": allocation_file,
                "factor-exposure-file": exposure_file,
            }
            display_state = {}

            first_stdout = io.StringIO()
            with contextlib.redirect_stdout(first_stdout):
                module.print_runtime_status(runtime_config, display_state=display_state)
            first_output = first_stdout.getvalue()
            self.assertIn("[trade executions] trade_date=20260317", first_output)
            self.assertIn("600061.SH", first_output)
            self.assertIn("|      0 |   400 |", first_output)

            snapshot_file.write_text(json.dumps({
                "quote_count": 300,
                "quotes": [
                    {
                        "ts_code": "600061.SH",
                        "close": 7.40,
                        "pre_close": 7.31,
                        "fetch_timestamp": "2026-03-17T11:14:01.941134+08:00",
                    },
                ],
            }), encoding="utf-8")

            second_stdout = io.StringIO()
            with contextlib.redirect_stdout(second_stdout):
                module.print_runtime_status(runtime_config, display_state=display_state)
            second_output = second_stdout.getvalue()
            self.assertIn("no new executions in this cycle", second_output)
            self.assertNotIn("[trade executions] trade_date=20260317", second_output)
            self.assertNotIn("| 2026-03-17 09:35:01 | BUY    | 600061.SH", second_output)

    def test_wait_for_bridge_ready_rejects_stale_partial_bridge_outputs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config-barra-cne5-live-paper.json"
            data_dir = root / "Data"
            results_dir = root / "Results"
            factor_dir = data_dir / "alternative" / "barra-cne5-live-factors" / "szse" / "daily"
            factor_dir.mkdir(parents=True, exist_ok=True)
            (factor_dir / "000001.csv").write_text(
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n"
                "20260316,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,100,1,1000,0,0\n",
                encoding="utf-8",
            )
            report_path = results_dir / "barra-cne5-live-bridge-report.json"
            snapshot_path = results_dir / "barra-cne5-live-price-snapshot.json"
            results_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "trade_date": "20260316",
                "bridge_report": {"resolved_symbol_count": 300, "written_symbol_count": 2},
                "live_quote_report": {"requested_symbol_count": 300, "received_quote_count": 2},
            }), encoding="utf-8")
            snapshot_path.write_text(json.dumps({"trade_date": "20260316", "quotes": []}), encoding="utf-8")
            stale_time = time.time() - 60
            os.utime(report_path, (stale_time, stale_time))
            os.utime(snapshot_path, (stale_time, stale_time))
            config_path.write_text(json.dumps({
                "data-folder": str(data_dir),
                "results-destination-folder": str(results_dir),
                "parameters": {
                    "factor-data-path": "alternative/barra-cne5-live-factors",
                    "live-factor-report-file": "barra-cne5-live-bridge-report.json",
                    "live-price-snapshot-file": "barra-cne5-live-price-snapshot.json",
                    "daily-quote-archive-path": "archive/barra-cne5-live-daily-quotes",
                },
            }), encoding="utf-8")

            class AliveProcess:
                def poll(self):
                    return None

            ready = module.wait_for_bridge_ready(
                config_path,
                AliveProcess(),
                bridge_started_at=time.time(),
                timeout_seconds=0.2,
                poll_interval_seconds=0.05,
            )
            self.assertFalse(ready)

    def test_wait_for_bridge_ready_accepts_fresh_full_bridge_outputs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config-barra-cne5-live-paper.json"
            data_dir = root / "Data"
            results_dir = root / "Results"
            factor_dir = data_dir / "alternative" / "barra-cne5-live-factors" / "szse" / "daily"
            factor_dir.mkdir(parents=True, exist_ok=True)
            for code in ("000001", "000002", "000063"):
                (factor_dir / f"{code}.csv").write_text(
                    "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n"
                    "20260316,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,100,1,1000,0,0\n",
                    encoding="utf-8",
                )
            report_path = results_dir / "barra-cne5-live-bridge-report.json"
            snapshot_path = results_dir / "barra-cne5-live-price-snapshot.json"
            results_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "data-folder": str(data_dir),
                "results-destination-folder": str(results_dir),
                "parameters": {
                    "factor-data-path": "alternative/barra-cne5-live-factors",
                    "live-factor-report-file": "barra-cne5-live-bridge-report.json",
                    "live-price-snapshot-file": "barra-cne5-live-price-snapshot.json",
                    "daily-quote-archive-path": "archive/barra-cne5-live-daily-quotes",
                },
            }), encoding="utf-8")
            bridge_started_at = time.time() - 1.0
            report_path.write_text(json.dumps({
                "trade_date": "20260316",
                "bridge_report": {"resolved_symbol_count": 3, "written_symbol_count": 3},
                "live_quote_report": {
                    "requested_symbol_count": 3,
                    "received_quote_count": 3,
                    "refreshed_quote_count": 3,
                    "missing_quote_count": 0,
                },
            }), encoding="utf-8")
            snapshot_path.write_text(json.dumps({"trade_date": "20260316", "quotes": [{"ts_code": "000001.SZ", "close": 10.0}]}), encoding="utf-8")

            class AliveProcess:
                def poll(self):
                    return None

            ready = module.wait_for_bridge_ready(
                config_path,
                AliveProcess(),
                bridge_started_at=bridge_started_at,
                timeout_seconds=0.2,
                poll_interval_seconds=0.05,
            )
            self.assertTrue(ready)


if __name__ == "__main__":
    unittest.main()
