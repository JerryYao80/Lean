#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
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
        "daily-summary-file": resolve_path(parameters.get("daily-summary-file"), root / "Results" / "barra-cne5-live-daily-summary.csv", results_base),
        "allocation-report-file": resolve_path(parameters.get("allocation-report-file"), root / "Results" / "barra-cne5-live-allocation.csv", results_base),
        "factor-exposure-file": resolve_path(parameters.get("factor-exposure-file"), root / "Results" / "barra-cne5-live-factor-exposure.csv", results_base),
        "trade-report-file": resolve_path(parameters.get("trade-report-file"), root / "Results" / "barra-cne5-live-trades.csv", results_base),
        "factor-source-mode": str(parameters.get("factor-source-mode") or "random"),
        "random-factor-seed": str(parameters.get("random-factor-seed") or "42"),
        "universe": str(parameters.get("symbols") or parameters.get("universe") or "csi300"),
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


def print_live_plan(config_path: Path, runtime_config: dict) -> None:
    print("=" * 100)
    print("Barra CNE5 Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    print("Market data         : Tushare realtime daily bars via TushareDataQueue", flush=True)
    print("Order model         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source       : {runtime_config['factor-source-mode']} (seed={runtime_config['random-factor-seed']})", flush=True)
    print(f"Universe            : {runtime_config['universe']}", flush=True)
    print(f"Rebalance           : {runtime_config['rebalance-frequency']} topN={runtime_config['top-n']} exposure={runtime_config['target-portfolio-exposure']}", flush=True)
    print(f"Tushare data path   : {runtime_config['tushare-data-path']}", flush=True)
    print(f"Factor data path    : {runtime_config['factor-data-path']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-factor-report-file']}", flush=True)
    print(f"Daily summary       : {runtime_config['daily-summary-file']}", flush=True)
    print(f"Allocation report   : {runtime_config['allocation-report-file']}", flush=True)
    print(f"Exposure report     : {runtime_config['factor-exposure-file']}", flush=True)
    print(f"Trade report        : {runtime_config['trade-report-file']}", flush=True)
    print("=" * 100)


def print_bridge_wait_status(runtime_config: dict) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    report_path = Path(runtime_config["live-factor-report-file"])
    bridge_report = load_bridge_report(report_path)
    trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0
    print(
        f"[bridge wait] {format_timestamp()} trade_date={trade_date} mode={mode} "
        f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols}",
        flush=True,
    )


def print_runtime_status(runtime_config: dict) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    bridge_report = load_bridge_report(Path(runtime_config["live-factor-report-file"]))
    daily_row = read_latest_csv_row(Path(runtime_config["daily-summary-file"]))

    bridge_trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    bridge_mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0

    if daily_row:
        print(
            f"[live status] {format_timestamp()} trade_date={daily_row.get('trade_date', '-')} "
            f"equity={daily_row.get('equity', '-')} cash={daily_row.get('cash', '-')} "
            f"holdings={daily_row.get('holdings', '-')} eligible={daily_row.get('eligible_symbols', '-')} "
            f"selected={daily_row.get('selected_symbols', '-')} turnover={daily_row.get('turnover', '-')} "
            f"rebalanced={daily_row.get('rebalanced', '-')}",
            flush=True,
        )
    else:
        print(
            f"[live status] {format_timestamp()} no daily summary yet "
            f"bridge_trade_date={bridge_trade_date} mode={bridge_mode} "
            f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols}",
            flush=True,
        )


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
    deadline = time.time() + max(1.0, float(timeout_seconds))
    next_status = time.time()

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        if report_path.exists() and any(factor_data_path.glob("*/*/*.csv")):
            return True
        now = time.time()
        if now >= next_status:
            print_bridge_wait_status(runtime_config)
            next_status = now + max(5.0, float(poll_interval_seconds))
        time.sleep(max(0.1, float(poll_interval_seconds)))

    return report_path.exists() and any(factor_data_path.glob("*/*/*.csv"))


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
        bridge_process = subprocess.Popen(build_bridge_command(config_path, python_executable), cwd=repo_root())
        try:
            return bridge_process.wait()
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping bridge", flush=True)
            return 130
        finally:
            terminate_process(bridge_process)

    if start_launcher and not start_bridge:
        launcher_command, workdir = build_launcher_command(config_path)
        launcher_process = subprocess.Popen(launcher_command, cwd=workdir)
        try:
            return wait_for_process_with_status(launcher_process, runtime_config)
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher", flush=True)
            return 130
        finally:
            terminate_process(launcher_process)

    bridge_process = subprocess.Popen(build_bridge_command(config_path, python_executable), cwd=repo_root())
    launcher_process = None
    try:
        if not wait_for_bridge_ready(config_path, bridge_process):
            if bridge_process.poll() is not None:
                return bridge_process.returncode
            print("Timed out waiting for bridge to materialize live factor files.", file=sys.stderr)
            return 1

        launcher_command, workdir = build_launcher_command(config_path)
        print("[launcher] starting LEAN live-paper engine", flush=True)
        launcher_process = subprocess.Popen(launcher_command, cwd=workdir)
        try:
            return wait_for_process_with_status(launcher_process, runtime_config)
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher and bridge", flush=True)
            return 130
    finally:
        terminate_process(launcher_process)
        terminate_process(bridge_process)


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
