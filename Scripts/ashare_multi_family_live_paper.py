#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import live_paper_console as console
import live_paper_runner as runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-multi-family-live-paper.json"


def launcher_binary_path() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"


def default_bridge_python_executable() -> str:
    preferred = Path("/root/miniconda3/envs/quant311/bin/python")
    if preferred.exists():
        return str(preferred)
    return sys.executable


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def normalize_console_output_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return "custom" if mode == "custom" else "native"


def resolve_console_output_mode(runtime_config: dict | None = None, requested_mode: str | None = None) -> str:
    configured_mode = None
    if isinstance(runtime_config, dict):
        configured_mode = runtime_config.get("console-output-mode")
    return normalize_console_output_mode(requested_mode or configured_mode)


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
        "factor-data-path": str(resolve_path(parameters.get("factor-data-path"), root / "Data" / "alternative" / "ashare-multi-family-live-factors", data_base)),
        "external-factor-path": str(resolve_path(parameters.get("external-factor-path"), root / "Data" / "alternative" / "ashare-multi-family-features", data_base)),
        "live-factor-report-file": str(resolve_path(parameters.get("live-factor-report-file"), root / "Results" / "multi-family-live-bridge-report.json", results_base)),
        "live-price-snapshot-file": str(resolve_path(parameters.get("live-price-snapshot-file"), root / "Results" / "multi-family-live-price-snapshot.json", results_base)),
        "shared-live-market-snapshot-file": str(resolve_path(parameters.get("shared-live-market-snapshot-file"), root / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json", results_base)),
        "portfolio-state-file": str(resolve_path(parameters.get("portfolio-state-file"), root / "Results" / "multi-family-live-state.json", results_base)),
        "daily-summary-file": str(resolve_path(parameters.get("daily-summary-file"), root / "Results" / "multi-family-live-daily-summary.csv", results_base)),
        "allocation-report-file": str(resolve_path(parameters.get("allocation-report-file"), root / "Results" / "multi-family-live-allocation.csv", results_base)),
        "family-exposure-file": str(resolve_path(parameters.get("family-exposure-file"), root / "Results" / "multi-family-live-family-exposure.csv", results_base)),
        "trade-report-file": str(resolve_path(parameters.get("trade-report-file"), root / "Results" / "multi-family-live-trades.csv", results_base)),
        "universe": str(parameters.get("universe") or "csi300"),
        "initial-cash": str(parameters.get("initial-cash") or "100000"),
        "console-output-mode": normalize_console_output_mode(parameters.get("console-output-mode")),
        "rebalance-frequency": str(parameters.get("rebalance-frequency") or "monthly"),
        "top-n": str(parameters.get("top-n") or "30"),
        "target-portfolio-exposure": str(parameters.get("target-portfolio-exposure") or "0.95"),
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data_v2"),
        "bridge-ready-timeout-seconds": str(parameters.get("bridge-ready-timeout-seconds") or "900"),
        "live-price-poll-interval-seconds": str(parameters.get("live-price-poll-interval-seconds") or "60"),
        "live-price-source-mode": str(parameters.get("live-price-source-mode") or "auto"),
    }


def build_live_session_root() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root() / "Results" / "live-paper" / "multi-family" / f"session-{timestamp}-{os.getpid()}"


def prepare_session_config(config_path: str | Path | None = None) -> tuple[Path, dict, Path]:
    source_config_path = resolve_live_config_path(config_path)
    runtime_config = load_live_paper_runtime_config(source_config_path)
    session_root = build_live_session_root()
    session_root.mkdir(parents=True, exist_ok=True)

    factor_root = session_root / "factor-data"
    session_config_path = session_root / "config-ashare-multi-family-live-paper.session.json"

    payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
        payload["parameters"] = parameters

    parameters.update({
        "factor-data-path": str(factor_root),
        "live-factor-report-file": str(session_root / "multi-family-live-bridge-report.json"),
        "live-price-snapshot-file": str(session_root / "multi-family-live-price-snapshot.json"),
        "daily-summary-file": str(session_root / "multi-family-live-daily-summary.csv"),
        "allocation-report-file": str(session_root / "multi-family-live-allocation.csv"),
        "family-exposure-file": str(session_root / "multi-family-live-family-exposure.csv"),
        "trade-report-file": str(session_root / "multi-family-live-trades.csv"),
        "portfolio-state-file": str(runtime_config["portfolio-state-file"]),
    })

    session_config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_config_path, load_live_paper_runtime_config(session_config_path), session_root


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or default_bridge_python_executable()
    return [
        executable,
        str(repo_root() / "Scripts" / "ashare_multi_family_live_bridge.py"),
        "--config",
        str(resolve_live_config_path(config_path)),
        "--once",
    ]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher_dir = launcher_binary_path().resolve().parent
    launcher_dll = launcher_dir / "QuantConnect.Lean.Launcher.dll"
    command = [runner.dotnet_binary_path(), str(launcher_dll), "--config", str(config_path)]
    return command, launcher_dir


def run_live_paper(
    config_path: str | Path | None = None,
    start_bridge: bool = True,
    start_launcher: bool = True,
    python_executable: str | None = None,
    console_output_mode: str | None = None,
    once: bool = False,
) -> int:
    session_config_path, runtime_config, session_root = prepare_session_config(config_path)
    resolved_console_output_mode = resolve_console_output_mode(runtime_config, console_output_mode)
    resolved_config_path = session_config_path

    print("=" * 100)
    print("Multi-Family Data-Driven Live Paper")
    print("=" * 100)
    print(f"Config file         : {resolved_config_path}", flush=True)
    print(f"Session root        : {session_root}", flush=True)
    print(f"Universe            : {runtime_config['universe']}", flush=True)
    print(f"Console output      : {resolved_console_output_mode}", flush=True)
    print(f"Initial cash        : {runtime_config['initial-cash']}", flush=True)
    print("=" * 100)

    if once and not start_launcher:
        bridge_command = build_bridge_command(resolved_config_path, python_executable)
        print(f"[once] running bridge: {' '.join(bridge_command)}", flush=True)
        result = subprocess.run(bridge_command, cwd=repo_root())
        return result.returncode

    bridge_process = None
    launcher_process = None

    try:
        if start_bridge:
            bridge_command = build_bridge_command(resolved_config_path, python_executable)
            print(f"[stage 1/2] running bridge once: {' '.join(bridge_command)}", flush=True)
            result = subprocess.run(bridge_command, cwd=repo_root())
            if result.returncode != 0:
                print(f"[bridge] bridge failed with exit code {result.returncode}", flush=True)
                return result.returncode

        if start_launcher:
            launcher_command, workdir = build_launcher_command(resolved_config_path)
            print(f"[stage 2/2] starting LEAN launcher: {' '.join(launcher_command)}", flush=True)
            launcher_process = subprocess.Popen(
                launcher_command,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            if resolved_console_output_mode == "native":
                for line in launcher_process.stdout:
                    print(line, end="", flush=True)
                return launcher_process.wait()

            started_at = time.time()
            while True:
                returncode = launcher_process.poll()
                if returncode is not None:
                    return returncode
                elapsed = int(time.time() - started_at)
                print(f"[lean heartbeat] running for {elapsed}s", flush=True)
                time.sleep(30)
    except KeyboardInterrupt:
        print("[live paper] interrupted by user", flush=True)
        return 130
    finally:
        if launcher_process and launcher_process.poll() is None:
            launcher_process.terminate()
            try:
                launcher_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                launcher_process.kill()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch multi-family live bridge and LEAN live-paper engine")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--python-executable")
    parser.add_argument("--console-output-mode", choices=("native", "custom"))
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
        console_output_mode=args.console_output_mode,
        once=args.once,
    )


if __name__ == "__main__":
    raise SystemExit(main())