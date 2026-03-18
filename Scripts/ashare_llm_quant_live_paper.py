#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import live_paper_console as console


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-llm-quant-live-paper.json"


def launcher_binary_path() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"


def default_bridge_python_executable() -> str:
    preferred = Path("/root/miniconda3/envs/quant311/bin/python")
    return str(preferred) if preferred.exists() else sys.executable


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


def format_currency(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"¥{numeric:,.2f}"


def format_fraction_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric * 100.0:+.2f}%"


def load_runtime_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    parameters = payload.get("parameters") if isinstance(payload, dict) else {}
    if not isinstance(parameters, dict):
        parameters = {}

    launcher_base = repo_root() / "Launcher" / "bin" / "Debug"
    data_base = launcher_base
    results_base = launcher_base

    data_folder = payload.get("data-folder")
    if data_folder:
        path = Path(str(data_folder))
        data_base = path if path.is_absolute() else (launcher_base / path).resolve()

    results_folder = payload.get("results-destination-folder")
    if results_folder:
        path = Path(str(results_folder))
        results_base = path if path.is_absolute() else (launcher_base / path).resolve()

    def resolve_path(value, default: Path, base: Path) -> Path:
        path = Path(str(value)) if value else default
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    root = repo_root()
    return {
        "feature-data-path": resolve_path(parameters.get("feature-data-path"), root / "Data" / "alternative" / "ashare-llm-quant-live-features", data_base),
        "benchmark-file": resolve_path(parameters.get("benchmark-file"), root / "Data" / "alternative" / "ashare-llm-quant-live-features" / "benchmark" / "000300.SH.csv", data_base),
        "live-bridge-report-file": resolve_path(parameters.get("live-bridge-report-file"), root / "Results" / "ashare-llm-quant-live-bridge-report.json", results_base),
        "live-price-snapshot-file": resolve_path(parameters.get("live-price-snapshot-file"), root / "Results" / "ashare-llm-quant-live-price-snapshot.json", results_base),
        "trade-report-file": resolve_path(parameters.get("trade-report-file"), root / "Results" / "ashare-llm-quant-live-trades.csv", results_base),
        "daily-summary-file": resolve_path(parameters.get("daily-summary-file"), root / "Results" / "ashare-llm-quant-live-daily.csv", results_base),
        "rebalance-report-file": resolve_path(parameters.get("rebalance-report-file"), root / "Results" / "ashare-llm-quant-live-rebalances.csv", results_base),
        "signal-file": resolve_path(parameters.get("signal-file"), root / "Results" / "ashare-llm-quant-live-signals.json", results_base),
        "portfolio-snapshot-file": resolve_path(parameters.get("portfolio-snapshot-file"), root / "Results" / "ashare-llm-quant-live-portfolio.json", results_base),
        "comparison-summary-file": resolve_path(parameters.get("comparison-summary-file"), root / "Results" / "ashare-llm-quant-live-comparison.json", results_base),
        "historical-feature-path": resolve_path(parameters.get("historical-feature-path"), root / "Data" / "alternative" / "ashare-llm-quant-features", data_base),
        "historical-benchmark-file": resolve_path(parameters.get("historical-benchmark-file"), root / "Data" / "alternative" / "ashare-llm-quant-features" / "benchmark" / "000300.SH.csv", data_base),
        "bridge-ready-timeout-seconds": max(60, safe_int(parameters.get("bridge-ready-timeout-seconds"), 600)),
        "live-factor-poll-interval-seconds": max(1, safe_int(parameters.get("live-factor-poll-interval-seconds"), 60)),
        "live-signal-interval-minutes": max(1, safe_int(parameters.get("live-signal-interval-minutes"), 5)),
        "rebalance-frequency": str(parameters.get("rebalance-frequency") or "interval"),
        "initial-capital": str(parameters.get("initial-capital") or "1000000"),
        "live-price-source-mode": str(parameters.get("live-price-source-mode") or "auto"),
        "simulated-live-price-volatility-scale": str(parameters.get("simulated-live-price-volatility-scale") or "8.0"),
    }


def build_live_session_root() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root() / "Results" / "live-paper" / "ashare-llm-quant" / f"session-{timestamp}-{os.getpid()}"


def prepare_session_config(config_path: str | Path | None = None) -> tuple[Path, dict, Path]:
    source_config_path = resolve_live_config_path(config_path)
    runtime_config = load_runtime_config(source_config_path)
    session_root = build_live_session_root()
    session_root.mkdir(parents=True, exist_ok=True)

    feature_root = session_root / "feature-data"
    config_payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    parameters = config_payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
        config_payload["parameters"] = parameters

    parameters.update(
        {
            "feature-data-path": str(feature_root),
            "benchmark-file": str(feature_root / "benchmark" / "000300.SH.csv"),
            "live-bridge-report-file": str(session_root / "ashare-llm-quant-live-bridge-report.json"),
            "live-price-snapshot-file": str(session_root / "ashare-llm-quant-live-price-snapshot.json"),
            "trade-report-file": str(session_root / "ashare-llm-quant-live-trades.csv"),
            "daily-summary-file": str(session_root / "ashare-llm-quant-live-daily.csv"),
            "rebalance-report-file": str(session_root / "ashare-llm-quant-live-rebalances.csv"),
            "signal-file": str(session_root / "ashare-llm-quant-live-signals.json"),
            "portfolio-snapshot-file": str(session_root / "ashare-llm-quant-live-portfolio.json"),
            "comparison-summary-file": str(session_root / "ashare-llm-quant-live-comparison.json"),
            "daily-quote-archive-path": str(session_root / "quote-archive"),
        }
    )

    session_config_path = session_root / "config-ashare-llm-quant-live-paper.session.json"
    session_config_path.write_text(json.dumps(config_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_config_path, load_runtime_config(session_config_path), session_root


def count_feature_files(feature_root: Path) -> int:
    return sum(1 for _ in feature_root.glob("*/*/*.csv")) if feature_root.exists() else 0


def load_bridge_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or default_bridge_python_executable()
    return [executable, str(repo_root() / "Scripts" / "ashare_llm_quant_live_bridge.py"), "--config", str(resolve_live_config_path(config_path))]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher = launcher_binary_path().resolve()
    return [str(launcher), "--config", str(config_path)], launcher.parent


def normalize_lean_output_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    if "STATISTICS::" in text:
        return f"[stats] {text.split('STATISTICS::', 1)[1].strip()}"
    if "TushareDataQueue." in text or "TushareDataConverter." in text:
        return None
    if "TRACE:: Log:" in text:
        return f"[algo] {text.split('TRACE:: Log:', 1)[1].strip()}"
    if "TRACE:: Debug:" in text:
        message = text.split("TRACE:: Debug:", 1)[1].strip()
        interesting = ("initialized", "rebalance", "Saved LEAN", "Runtime Error", "completed in")
        return f"[algo] {message}" if any(token in message for token in interesting) else None
    if "ERROR::" in text or "Unhandled exception" in text or "RuntimeError" in text:
        return text
    return None


def normalize_process_output_line(label: str, line: str) -> str | None:
    text = line.rstrip("\n")
    if label == "lean":
        normalized = normalize_lean_output_line(text)
        return f"[lean] {normalized}" if normalized else None
    text = text.strip()
    return f"[{label}] {text}" if text else None


def stream_process_output(process: subprocess.Popen, label: str) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def consume_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            normalized = normalize_process_output_line(label, line)
            if normalized:
                print(normalized, flush=True)

    thread = threading.Thread(target=consume_output, name=f"{label}-output", daemon=True)
    thread.start()
    return thread


def start_logged_process(command: list[str], cwd: Path, label: str) -> tuple[subprocess.Popen, threading.Thread | None]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    print(f"[process start] name={label} pid={process.pid} cwd={cwd}", flush=True)
    print(f"[process cmd] {label}: {' '.join(command)}", flush=True)
    return process, stream_process_output(process, label)


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def join_output_thread(thread: threading.Thread | None) -> None:
    if thread is not None:
        thread.join(timeout=5)


def wait_for_bridge_ready(config_path: str | Path | None, bridge_process: subprocess.Popen, bridge_started_at: float, timeout_seconds: float) -> bool:
    runtime_config = load_runtime_config(config_path)
    feature_root = Path(runtime_config["feature-data-path"])
    report_path = Path(runtime_config["live-bridge-report-file"])
    snapshot_path = Path(runtime_config["live-price-snapshot-file"])
    deadline = time.time() + max(1.0, timeout_seconds)

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        report = load_bridge_report(report_path)
        if report_path.exists() and snapshot_path.exists() and report is not None:
            if report_path.stat().st_mtime + 1e-6 >= bridge_started_at and snapshot_path.stat().st_mtime + 1e-6 >= bridge_started_at:
                bridge_payload = report.get("bridge_report") or {}
                resolved = safe_int(bridge_payload.get("resolved_symbol_count"), 0)
                written = safe_int(bridge_payload.get("written_symbol_count"), 0)
                if written > 0 and count_feature_files(feature_root) >= written and (resolved == 0 or written >= resolved):
                    return True
        print(
            f"[bridge wait] feature_files={count_feature_files(feature_root)} "
            f"report_exists={int(report_path.exists())} snapshot_exists={int(snapshot_path.exists())}",
            flush=True,
        )
        time.sleep(2.0)
    return False


def print_runtime_status(
    runtime_config: dict,
    output_fresh_since: float | None = None,
    display_state: dict | None = None,
) -> None:
    bridge_report = load_bridge_report(Path(runtime_config["live-bridge-report-file"]))
    daily_row = console.read_latest_csv_row(Path(runtime_config["daily-summary-file"]), modified_after=output_fresh_since)
    trade_rows = console.read_csv_rows(Path(runtime_config["trade-report-file"]), modified_after=output_fresh_since)
    portfolio_snapshot = console.load_json_payload(Path(runtime_config["portfolio-snapshot-file"]), modified_after=output_fresh_since)
    if not isinstance(portfolio_snapshot, dict):
        portfolio_snapshot = {}
    signal_payload = console.load_json_payload(Path(runtime_config["signal-file"]), modified_after=output_fresh_since)
    if not isinstance(signal_payload, dict):
        signal_payload = {}
    snapshot_payload, snapshot_quotes = console.build_snapshot_quote_lookup(
        Path(runtime_config["live-price-snapshot-file"]),
    )

    trade_count = len(trade_rows)
    live_quote_report = bridge_report.get("live_quote_report") if isinstance(bridge_report, dict) else {}
    if not isinstance(live_quote_report, dict):
        live_quote_report = {}
    received_quotes = safe_int(live_quote_report.get("received_quote_count"), 0)
    bridge_mode = str((bridge_report.get("bridge_report") or {}).get("mode") or "-") if isinstance(bridge_report, dict) else "-"

    equity = safe_float((daily_row or {}).get("equity"))
    if equity is None:
        equity = safe_float(portfolio_snapshot.get("total_value"))
    initial = safe_float(runtime_config.get("initial-capital"))
    pnl_ratio = ((equity / initial) - 1.0) if equity is not None and initial not in (None, 0.0) else None
    cash = safe_float((daily_row or {}).get("cash"))
    if cash is None:
        cash = safe_float(portfolio_snapshot.get("cash"))
    holdings_value = safe_float((daily_row or {}).get("holdings_value"))
    if holdings_value is None:
        holdings_value = safe_float(portfolio_snapshot.get("holdings_value"))
    position_count = (daily_row or {}).get("position_count")
    if position_count in (None, "") and isinstance(portfolio_snapshot.get("holdings"), list):
        position_count = str(len(portfolio_snapshot.get("holdings") or []))

    if daily_row is None and not portfolio_snapshot:
        print(
            f"[live status] no strategy outputs yet feature_files={count_feature_files(Path(runtime_config['feature-data-path']))} "
            f"bridge_mode={bridge_mode} live_quotes={received_quotes} trades={trade_count}",
            flush=True,
        )
    else:
        print(
            f"[live status] trade_date={(daily_row or {}).get('trade_date', (bridge_report or {}).get('trade_date', '-'))} "
            f"equity={format_currency(equity)} pnl={format_fraction_percent(pnl_ratio)} "
            f"cash={format_currency(cash)} holdings_value={format_currency(holdings_value)} "
            f"positions={position_count or '-'} trades={trade_count} bridge_mode={bridge_mode} live_quotes={received_quotes}",
            flush=True,
        )

    last_rebalance = signal_payload.get("last_rebalance") if isinstance(signal_payload, dict) else None
    if not isinstance(last_rebalance, dict):
        last_rebalance = {}
    extra_rows: list[tuple[str, str]] = []
    if last_rebalance.get("risk_state") not in (None, ""):
        extra_rows.append(("Risk State", str(last_rebalance.get("risk_state"))))
    target_exposure = safe_float(last_rebalance.get("target_exposure"))
    if target_exposure is not None:
        extra_rows.append(("Target Exp", f"{target_exposure * 100.0:.2f}%"))
    if last_rebalance.get("selected_count") not in (None, ""):
        extra_rows.append(("Selected", str(last_rebalance.get("selected_count"))))
    if last_rebalance.get("signal_date") not in (None, ""):
        extra_rows.append(("Signal Date", str(last_rebalance.get("signal_date"))))

    account_summary = console.build_account_summary_from_sources(
        initial_cash=runtime_config.get("initial-capital"),
        daily_row=daily_row,
        portfolio_snapshot=portfolio_snapshot,
        snapshot_payload=snapshot_payload,
        bridge_report=bridge_report,
        live_quote_count=received_quotes,
        extra_rows=extra_rows,
    )
    if console.should_print_section(display_state, "account_summary", account_summary):
        print(account_summary, flush=True)

    previous_trade_count = safe_int(display_state.get("_trade_row_count"), 0) if isinstance(display_state, dict) else 0
    if isinstance(display_state, dict):
        display_state["_trade_row_count"] = str(trade_count)

    if trade_rows:
        new_trade_rows = trade_rows[previous_trade_count:] if previous_trade_count < trade_count else []
        if new_trade_rows:
            trade_preview = console.build_trade_executions_preview(
                new_trade_rows,
                snapshot_quotes=snapshot_quotes,
                limit=12,
                total_rows=trade_count,
            )
            if console.should_print_section(display_state, "trade_executions", trade_preview):
                print(trade_preview, flush=True)
        else:
            no_trade_line = console.build_no_new_trade_line(trade_rows)
            if console.should_print_section(display_state, "trade_executions", no_trade_line):
                print(no_trade_line, flush=True)
    else:
        no_trade_line = console.build_no_new_trade_line([])
        if console.should_print_section(display_state, "trade_executions", no_trade_line):
            print(no_trade_line, flush=True)

    allocation_trade_date = (daily_row or {}).get("trade_date") or (bridge_report.get("trade_date") if isinstance(bridge_report, dict) else None)
    allocation_preview = console.build_portfolio_allocation_from_snapshot(
        portfolio_snapshot,
        snapshot_quotes=snapshot_quotes,
        limit=20,
        trade_date=allocation_trade_date,
    )
    if allocation_preview is None and portfolio_snapshot.get("holdings") == []:
        allocation_preview = "[portfolio allocation] no holdings yet"
    if allocation_preview is None and not portfolio_snapshot:
        allocation_preview = "[portfolio allocation] no holdings yet"
    if console.should_print_section(display_state, "portfolio_allocation", allocation_preview):
        print(allocation_preview, flush=True)


def wait_for_process_with_status(
    process: subprocess.Popen,
    runtime_config: dict,
    output_fresh_since: float | None = None,
) -> int:
    interval_seconds = max(5.0, float(runtime_config["live-factor-poll-interval-seconds"]))
    display_state: dict[str, str] = {}
    print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
    next_status = time.time()
    while True:
        returncode = process.poll()
        if returncode is not None:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
            return returncode
        if time.time() >= next_status:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
            next_status = time.time() + interval_seconds
        time.sleep(1.0)


def print_live_plan(config_path: Path, runtime_config: dict, session_root: Path | None = None) -> None:
    print("=" * 100)
    print("A-Share LLM Quant Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    if session_root is not None:
        print(f"Session root        : {session_root}", flush=True)
    print("Market data         : Tushare realtime in-session; GBM-simulated daily bars off-session", flush=True)
    print(f"Historical features : {runtime_config['historical-feature-path']}", flush=True)
    print(f"Live feature path   : {runtime_config['feature-data-path']}", flush=True)
    print(f"Benchmark file      : {runtime_config['benchmark-file']}", flush=True)
    print(f"Snapshot file       : {runtime_config['live-price-snapshot-file']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-bridge-report-file']}", flush=True)
    print(f"Rebalance           : {runtime_config['rebalance-frequency']} every {runtime_config['live-signal-interval-minutes']} minute(s)", flush=True)
    print(f"Initial capital     : {runtime_config['initial-capital']}", flush=True)
    print(f"Price mode          : {runtime_config['live-price-source-mode']} (sim vol scale={runtime_config['simulated-live-price-volatility-scale']})", flush=True)
    print("=" * 100)


def run_live_paper(config_path: str | Path | None = None, start_bridge: bool = True, start_launcher: bool = True, python_executable: str | None = None) -> int:
    session_config_path, runtime_config, session_root = prepare_session_config(config_path)
    resolved_config_path = session_config_path
    print_live_plan(resolved_config_path, runtime_config, session_root=session_root)

    if start_bridge and not start_launcher:
        bridge_process, bridge_thread = start_logged_process(build_bridge_command(resolved_config_path, python_executable), repo_root(), "bridge")
        try:
            return bridge_process.wait()
        finally:
            terminate_process(bridge_process)
            join_output_thread(bridge_thread)

    if start_launcher and not start_bridge:
        launcher_command, workdir = build_launcher_command(resolved_config_path)
        launcher_started_at = time.time()
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")
        try:
            return wait_for_process_with_status(
                launcher_process,
                runtime_config,
                output_fresh_since=launcher_started_at,
            )
        finally:
            terminate_process(launcher_process)
            join_output_thread(launcher_thread)

    bridge_started_at = time.time()
    bridge_process, bridge_thread = start_logged_process(build_bridge_command(resolved_config_path, python_executable), repo_root(), "bridge")
    launcher_process = None
    launcher_thread = None
    try:
        print("[stage 1/3] waiting for live bridge readiness", flush=True)
        if not wait_for_bridge_ready(resolved_config_path, bridge_process, bridge_started_at, float(runtime_config["bridge-ready-timeout-seconds"])):
            return bridge_process.returncode if bridge_process.poll() is not None else 1

        print("[stage 2/3] starting standard LEAN live-paper launcher", flush=True)
        launcher_command, workdir = build_launcher_command(resolved_config_path)
        launcher_started_at = time.time()
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")

        print("[stage 3/3] entering live status loop", flush=True)
        return wait_for_process_with_status(
            launcher_process,
            runtime_config,
            output_fresh_since=launcher_started_at,
        )
    finally:
        terminate_process(launcher_process)
        terminate_process(bridge_process)
        join_output_thread(launcher_thread)
        join_output_thread(bridge_thread)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch A-share LLM quant live bridge and LEAN live-paper engine")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    parser.add_argument("--python-executable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_live_paper(
        args.config,
        start_bridge=not args.launcher_only,
        start_launcher=not args.bridge_only,
        python_executable=args.python_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
