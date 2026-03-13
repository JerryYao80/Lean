#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-barra-cne5-live-paper.json"


def launcher_binary_path() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"


def default_bridge_python_executable() -> str:
    preferred = Path("/root/miniconda3/envs/quant311/bin/python")
    if preferred.exists():
        return str(preferred)
    return sys.executable


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def load_live_paper_runtime_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    loaded = json.loads(config_file.read_text(encoding="utf-8"))
    parameters = loaded.get("parameters") if isinstance(loaded, dict) else None
    if not isinstance(parameters, dict):
        parameters = {}

    launcher_workdir = repo_root() / "Launcher" / "bin" / "Debug"
    data_base = launcher_workdir
    results_base = launcher_workdir

    data_folder = loaded.get("data-folder") if isinstance(loaded, dict) else None
    if data_folder:
        path = Path(str(data_folder))
        data_base = path if path.is_absolute() else (launcher_workdir / path).resolve()

    results_folder = loaded.get("results-destination-folder") if isinstance(loaded, dict) else None
    if results_folder:
        path = Path(str(results_folder))
        results_base = path if path.is_absolute() else (launcher_workdir / path).resolve()

    def resolve_path(value, default: Path, base: Path) -> Path:
        path = Path(str(value)) if value else default
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    root = repo_root()
    return {
        "factor-data-path": resolve_path(parameters.get("factor-data-path"), root / "Data" / "alternative" / "barra-cne5-live-factors", data_base),
        "live-factor-report-file": resolve_path(parameters.get("live-factor-report-file"), root / "Results" / "barra-cne5-live-bridge-report.json", results_base),
        "live-price-snapshot-file": resolve_path(parameters.get("live-price-snapshot-file"), root / "Results" / "barra-cne5-live-price-snapshot.json", results_base),
        "daily-quote-archive-path": resolve_path(parameters.get("daily-quote-archive-path"), root / "Data" / "archive" / "barra-cne5-live-daily-quotes", data_base),
        "daily-summary-file": resolve_path(parameters.get("daily-summary-file"), root / "Results" / "barra-cne5-live-daily-summary.csv", results_base),
        "allocation-report-file": resolve_path(parameters.get("allocation-report-file"), root / "Results" / "barra-cne5-live-allocation.csv", results_base),
        "factor-exposure-file": resolve_path(parameters.get("factor-exposure-file"), root / "Results" / "barra-cne5-live-factor-exposure.csv", results_base),
        "trade-report-file": resolve_path(parameters.get("trade-report-file"), root / "Results" / "barra-cne5-live-trades.csv", results_base),
        "factor-source-mode": str(parameters.get("factor-source-mode") or "random"),
        "random-factor-seed": str(parameters.get("random-factor-seed") or "42"),
        "universe": str(parameters.get("symbols") or parameters.get("universe") or "csi300"),
        "bridge-ready-timeout-seconds": max(120, safe_int(parameters.get("bridge-ready-timeout-seconds"), 900)),
        "live-price-poll-interval-seconds": str(
            parameters.get("live-price-poll-interval-seconds")
            or parameters.get("live-factor-poll-interval-seconds")
            or "60"
        ),
        "live-price-refresh-interval-seconds": str(parameters.get("live-price-refresh-interval-seconds") or "60"),
        "rebalance-frequency": str(parameters.get("rebalance-frequency") or "monthly"),
        "top-n": str(parameters.get("top-n") or "30"),
        "target-portfolio-exposure": str(parameters.get("target-portfolio-exposure") or "0.95"),
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data"),
    }


def format_timestamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def count_factor_files(factor_data_path: Path) -> int:
    if not factor_data_path.exists():
        return 0
    return sum(1 for _ in factor_data_path.glob("*/*/*.csv"))


def load_bridge_report(report_path: Path) -> dict | None:
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def read_latest_csv_row(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None
    if not rows:
        return None
    return {
        str(key): ("" if value is None else str(value))
        for key, value in rows[-1].items()
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []
    return [
        {str(key): ("" if value is None else str(value)) for key, value in row.items()}
        for row in rows
    ]


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
    if numeric != numeric:
        return None
    return numeric


def format_weight_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric * 100.0:.2f}%"


def build_allocation_preview(path: Path, limit: int = 5) -> str | None:
    rows = read_csv_rows(path)
    if not rows:
        return None
    trade_dates = [row.get("trade_date", "") for row in rows if row.get("trade_date")]
    if not trade_dates:
        return None
    latest_trade_date = max(trade_dates)
    latest_rows = [row for row in rows if row.get("trade_date") == latest_trade_date]
    latest_rows.sort(key=lambda row: safe_float(row.get("weight")) or 0.0, reverse=True)
    preview = latest_rows[:limit]
    parts = [
        f"{row.get('symbol', '-')}={format_weight_percent(row.get('weight'))}"
        for row in preview
    ]
    if not parts:
        return None
    return f"[allocation] trade_date={latest_trade_date} top={', '.join(parts)}"


def build_exposure_preview(path: Path) -> str | None:
    row = read_latest_csv_row(path)
    if not row:
        return None
    fields = [
        ("beta", row.get("beta")),
        ("momentum", row.get("momentum")),
        ("size", row.get("size")),
        ("growth", row.get("growth")),
        ("liquidity", row.get("liquidity")),
    ]
    parts = []
    for name, value in fields:
        numeric = safe_float(value)
        parts.append(f"{name}={numeric:.3f}" if numeric is not None else f"{name}=-")
    return f"[exposure] trade_date={row.get('trade_date', '-')} {' '.join(parts)}"


def build_signal_preview(path: Path, limit: int = 5) -> str | None:
    rows = read_csv_rows(path)
    if not rows:
        return None
    latest_trade_date = rows[-1].get("trade_date") or "-"
    latest_rows = [row for row in rows if row.get("trade_date") == latest_trade_date]
    preview = latest_rows[-limit:]
    parts = []
    for row in preview:
        action = row.get("action", "-")
        symbol = row.get("symbol", "-")
        quantity = row.get("quantity", "-")
        price = safe_float(row.get("price"))
        score = safe_float(row.get("score"))
        price_text = "-" if price is None else f"{price:.2f}"
        score_text = "-" if score is None else f"{score:.4f}"
        parts.append(f"{action} {symbol} qty={quantity} px={price_text} score={score_text}")
    if not parts:
        return None
    return f"[signals] trade_date={latest_trade_date} {' | '.join(parts)}"


def build_market_preview_line(bridge_report: dict | None) -> str | None:
    if not bridge_report:
        return None
    market_preview = bridge_report.get("market_preview")
    if not isinstance(market_preview, dict):
        return None
    live_quote_report = bridge_report.get("live_quote_report")
    if not isinstance(live_quote_report, dict):
        live_quote_report = {}
    rows = list(market_preview.get("rows") or [])
    if not rows:
        return None
    preview_rows = rows[:4]
    parts = []
    for row in preview_rows:
        symbol = row.get("symbol", "-")
        close = safe_float(row.get("close"))
        pct_chg = safe_float(row.get("pct_chg"))
        close_text = "-" if close is None else f"{close:.2f}"
        pct_text = "-" if pct_chg is None else f"{pct_chg:+.2f}%"
        parts.append(f"{symbol}@{close_text}({pct_text})")
    return (
        f"[market preview] trade_date={market_preview.get('trade_date', '-')} "
        f"requested={live_quote_report.get('requested_symbol_count', market_preview.get('universe_size', 0))} "
        f"received={live_quote_report.get('received_quote_count', market_preview.get('received_count', 0))} "
        f"fetched_at={live_quote_report.get('generated_at', '-')} "
        f"sample={', '.join(parts)}"
    )


def print_live_plan(config_path: Path, runtime_config: dict) -> None:
    print("=" * 100)
    print("Barra CNE5 Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    print("Market data         : Tushare realtime daily bars via TushareDataQueue", flush=True)
    print("Order model         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source       : {runtime_config['factor-source-mode']} (seed={runtime_config['random-factor-seed']})", flush=True)
    print(f"Universe            : {runtime_config['universe']}", flush=True)
    print(f"Bridge ready timeout: {runtime_config['bridge-ready-timeout-seconds']} seconds", flush=True)
    print(f"rt_k poll interval  : {runtime_config['live-price-poll-interval-seconds']} seconds", flush=True)
    print(f"LEAN queue refresh  : {runtime_config['live-price-refresh-interval-seconds']} seconds", flush=True)
    print(f"Rebalance           : {runtime_config['rebalance-frequency']} topN={runtime_config['top-n']} exposure={runtime_config['target-portfolio-exposure']}", flush=True)
    print(f"Tushare data path   : {runtime_config['tushare-data-path']}", flush=True)
    print(f"Factor data path    : {runtime_config['factor-data-path']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-factor-report-file']}", flush=True)
    print(f"Price snapshot      : {runtime_config['live-price-snapshot-file']}", flush=True)
    print(f"Quote archive       : {runtime_config['daily-quote-archive-path']}", flush=True)
    print(f"Daily summary       : {runtime_config['daily-summary-file']}", flush=True)
    print(f"Allocation report   : {runtime_config['allocation-report-file']}", flush=True)
    print(f"Exposure report     : {runtime_config['factor-exposure-file']}", flush=True)
    print(f"Trade report        : {runtime_config['trade-report-file']}", flush=True)
    print("Process flow        : 1) bridge materializes factor snapshot 2) LEAN starts TushareDataQueue 3) queue emits daily-bar flow 4) strategy prints signals and portfolio state", flush=True)
    print("=" * 100)


def print_bridge_wait_status(runtime_config: dict) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    report_path = Path(runtime_config["live-factor-report-file"])
    snapshot_path = Path(runtime_config["live-price-snapshot-file"])
    bridge_report = load_bridge_report(report_path)
    trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0
    live_quotes = bridge_report.get("live_quote_report", {}).get("received_quote_count") if bridge_report else 0
    print(
        f"[stage bridge-wait] {format_timestamp()} trade_date={trade_date} mode={mode} "
        f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols} "
        f"live_quotes={live_quotes} snapshot_exists={int(snapshot_path.exists())}",
        flush=True,
    )
    market_preview = build_market_preview_line(bridge_report)
    if market_preview:
        print(market_preview, flush=True)


def print_runtime_status(runtime_config: dict) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    bridge_report = load_bridge_report(Path(runtime_config["live-factor-report-file"]))
    daily_row = read_latest_csv_row(Path(runtime_config["daily-summary-file"]))

    bridge_trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    bridge_mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0
    received_quotes = bridge_report.get("live_quote_report", {}).get("received_quote_count") if bridge_report else 0

    if daily_row:
        print(
            f"[live status] {format_timestamp()} trade_date={daily_row.get('trade_date', '-')} "
            f"equity={daily_row.get('equity', '-')} cash={daily_row.get('cash', '-')} "
            f"holdings={daily_row.get('holdings', '-')} eligible={daily_row.get('eligible_symbols', '-')} "
            f"selected={daily_row.get('selected_symbols', '-')} turnover={daily_row.get('turnover', '-')} "
            f"live_quotes={received_quotes} "
            f"rebalanced={daily_row.get('rebalanced', '-')}",
            flush=True,
        )
    else:
        print(
            f"[live status] {format_timestamp()} no daily summary yet "
            f"bridge_trade_date={bridge_trade_date} mode={bridge_mode} "
            f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols} "
            f"live_quotes={received_quotes}",
            flush=True,
        )

    market_preview = build_market_preview_line(bridge_report)
    if market_preview:
        print(market_preview, flush=True)

    allocation_preview = build_allocation_preview(Path(runtime_config["allocation-report-file"]))
    if allocation_preview:
        print(allocation_preview, flush=True)

    exposure_preview = build_exposure_preview(Path(runtime_config["factor-exposure-file"]))
    if exposure_preview:
        print(exposure_preview, flush=True)

    signal_preview = build_signal_preview(Path(runtime_config["trade-report-file"]))
    if signal_preview:
        print(signal_preview, flush=True)


def normalize_lean_output_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    if "STATISTICS::" in text:
        return f"[stats] {text.split('STATISTICS::', 1)[1].strip()}"
    if "TushareDataQueue." in text or "TushareDataConverter." in text:
        payload = text.split("TRACE::", 1)[1].strip() if "TRACE::" in text else text
        return f"[data] {payload}"
    if "TRACE:: Log:" in text:
        return f"[algo] {text.split('TRACE:: Log:', 1)[1].strip()}"
    if "TRACE:: Debug:" in text:
        message = text.split("TRACE:: Debug:", 1)[1].strip()
        interesting_tokens = (
            "AShareBarraCNE5Algorithm initialized",
            "Execution mode:",
            "Synthetic execution enabled:",
            "rebalance",
            "[signal",
            "[daily]",
            "[portfolio]",
            "Saved Barra outputs",
            "Runtime Error",
            "completed in",
        )
        return f"[algo] {message}" if any(token in message for token in interesting_tokens) else None
    if "ERROR::" in text or "Unhandled exception" in text or "RuntimeError" in text:
        return text
    return None


def normalize_process_output_line(label: str, line: str) -> str | None:
    text = line.rstrip("\n")
    if label == "lean":
        normalized = normalize_lean_output_line(text)
        return None if normalized is None else f"[lean] {normalized}"
    text = text.strip()
    if not text:
        return None
    return f"[{label}] {text}"


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


def join_output_thread(thread: threading.Thread | None) -> None:
    if thread is None:
        return
    thread.join(timeout=5)


def wait_for_process_with_status(
    process: subprocess.Popen,
    runtime_config: dict,
    status_interval_seconds: float = 15.0,
) -> int:
    next_status = time.time() + max(1.0, float(status_interval_seconds))
    while True:
        returncode = process.poll()
        if returncode is not None:
            print_runtime_status(runtime_config)
            return returncode

        now = time.time()
        if now >= next_status:
            print_runtime_status(runtime_config)
            next_status = now + max(1.0, float(status_interval_seconds))

        time.sleep(1.0)


def wait_for_bridge_ready(
    config_path: str | Path | None,
    bridge_process: subprocess.Popen,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
) -> bool:
    runtime_config = load_live_paper_runtime_config(config_path)
    factor_data_path = Path(runtime_config["factor-data-path"])
    report_path = Path(runtime_config["live-factor-report-file"])
    snapshot_path = Path(runtime_config["live-price-snapshot-file"])
    deadline = time.time() + max(1.0, float(timeout_seconds))
    next_status = time.time()

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        bridge_report = load_bridge_report(report_path)
        received_quotes = 0
        if bridge_report:
            received_quotes = int(bridge_report.get("live_quote_report", {}).get("received_quote_count") or 0)
        if report_path.exists() and snapshot_path.exists() and received_quotes > 0 and any(factor_data_path.glob("*/*/*.csv")):
            return True
        now = time.time()
        if now >= next_status:
            print_bridge_wait_status(runtime_config)
            next_status = now + max(5.0, float(poll_interval_seconds))
        time.sleep(max(0.1, float(poll_interval_seconds)))

    bridge_report = load_bridge_report(report_path)
    received_quotes = int((bridge_report or {}).get("live_quote_report", {}).get("received_quote_count") or 0)
    return report_path.exists() and snapshot_path.exists() and received_quotes > 0 and any(factor_data_path.glob("*/*/*.csv"))


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or default_bridge_python_executable()
    return [
        executable,
        str(repo_root() / "Scripts" / "barra_cne5_live_bridge.py"),
        "--config",
        str(resolve_live_config_path(config_path)),
    ]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher = launcher_binary_path().resolve()
    command = [str(launcher), "--config", str(config_path)]
    return command, launcher.parent


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_live_paper(
    config_path: str | Path | None = None,
    start_bridge: bool = True,
    start_launcher: bool = True,
    python_executable: str | None = None,
) -> int:
    resolved_config_path = resolve_live_config_path(config_path)
    runtime_config = load_live_paper_runtime_config(resolved_config_path)
    print_live_plan(resolved_config_path, runtime_config)

    if not start_bridge and not start_launcher:
        return 0

    if start_bridge and not start_launcher:
        print("[stage 1/1] starting bridge only", flush=True)
        bridge_process, bridge_thread = start_logged_process(
            build_bridge_command(config_path, python_executable),
            repo_root(),
            "bridge",
        )
        try:
            return bridge_process.wait()
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping bridge", flush=True)
            return 130
        finally:
            terminate_process(bridge_process)
            join_output_thread(bridge_thread)

    if start_launcher and not start_bridge:
        launcher_command, workdir = build_launcher_command(config_path)
        print("[stage 1/1] starting launcher only", flush=True)
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")
        try:
            return wait_for_process_with_status(launcher_process, runtime_config)
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher", flush=True)
            return 130
        finally:
            terminate_process(launcher_process)
            join_output_thread(launcher_thread)

    print("[stage 1/4] starting bridge process", flush=True)
    bridge_process, bridge_thread = start_logged_process(
        build_bridge_command(config_path, python_executable),
        repo_root(),
        "bridge",
    )
    launcher_process = None
    launcher_thread = None
    try:
        print("[stage 2/4] waiting for bridge to materialize live factor files", flush=True)
        if not wait_for_bridge_ready(
            config_path,
            bridge_process,
            timeout_seconds=float(runtime_config["bridge-ready-timeout-seconds"]),
        ):
            if bridge_process.poll() is not None:
                return bridge_process.returncode
            print(
                "Timed out waiting for bridge to materialize live factor files. "
                "Consider increasing `bridge-ready-timeout-seconds` if the realtime universe is large.",
                file=sys.stderr,
            )
            return 1

        launcher_command, workdir = build_launcher_command(config_path)
        print("[stage 3/4] bridge ready; starting LEAN live-paper engine", flush=True)
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")
        try:
            print("[stage 4/4] entering live status loop", flush=True)
            return wait_for_process_with_status(launcher_process, runtime_config)
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher and bridge", flush=True)
            return 130
    finally:
        terminate_process(launcher_process)
        terminate_process(bridge_process)
        join_output_thread(launcher_thread)
        join_output_thread(bridge_thread)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch Barra CNE5 live bridge and LEAN live-paper engine")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    parser.add_argument("--python-executable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start_bridge = not args.launcher_only
    start_launcher = not args.bridge_only
    return run_live_paper(
        args.config,
        start_bridge=start_bridge,
        start_launcher=start_launcher,
        python_executable=args.python_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
