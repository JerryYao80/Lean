#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import live_paper_runner as runner


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def load_json_payload(config_path: str | Path) -> dict:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def resolve_launcher_runtime_bases(root: Path, payload: dict) -> tuple[Path, Path, Path]:
    launcher_base = root / "Launcher" / "bin" / "Debug"
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

    return launcher_base, data_base, results_base


def resolve_path(value, default: Path, base: Path) -> Path:
    path = Path(str(value)) if value else default
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def resolve_config_relative_path(value, config_path: str | Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = (Path(config_path).resolve().parent / path).resolve()
    return path


def build_live_session_root(root: Path, strategy_slug: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / "Results" / "live-paper" / strategy_slug / f"session-{timestamp}-{os.getpid()}"


def prepare_live_session_config(
    source_config_path: str | Path,
    session_root: Path,
    session_config_name: str,
    parameter_updates: dict,
    runtime_loader,
) -> tuple[Path, dict, Path]:
    session_root.mkdir(parents=True, exist_ok=True)

    payload = load_json_payload(source_config_path)
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
        payload["parameters"] = parameters
    parameters.update(parameter_updates)

    session_config_path = session_root / session_config_name
    session_config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_config_path, runtime_loader(session_config_path), session_root


def load_bridge_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def wait_for_plan_bridge_ready(
    bridge_process,
    bridge_started_at: float,
    report_path: Path,
    plan_file: Path,
    benchmark_file: Path,
    timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> bool:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    sleep_seconds = max(0.1, float(poll_interval_seconds))

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        report = load_bridge_report(report_path)
        if (
            report is not None
            and report_path.exists()
            and report_path.stat().st_mtime + 1e-6 >= bridge_started_at
            and plan_file.exists()
            and benchmark_file.exists()
            and report.get("status") == "ok"
        ):
            return True
        time.sleep(sleep_seconds)
    return False


def build_launcher_command(root: Path, config_path: str | Path) -> tuple[list[str], Path]:
    resolved_config_path = Path(config_path).resolve()
    launcher_dir = (root / "Launcher" / "bin" / "Debug").resolve()
    launcher_dll = launcher_dir / "QuantConnect.Lean.Launcher.dll"
    return [runner.dotnet_binary_path(), str(launcher_dll), "--config", str(resolved_config_path)], launcher_dir


def load_backtest_pipeline_config(
    config_path: str | Path,
    default_pipeline_config_path: str | Path,
) -> tuple[dict, Path]:
    config_file = Path(config_path).resolve()
    payload = load_json_payload(config_file)
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}

    pipeline_config = parameters.get("pipeline-config")
    if pipeline_config:
        pipeline_config_path = Path(str(pipeline_config))
        if not pipeline_config_path.is_absolute():
            pipeline_config_path = (config_file.parent / pipeline_config_path).resolve()
    else:
        pipeline_config_path = Path(default_pipeline_config_path).resolve()

    return parameters, pipeline_config_path.resolve()


def run_launcher_stage(root: Path, config_path: str | Path) -> int:
    command, workdir = build_launcher_command(root, config_path)
    process = subprocess.run(command, cwd=workdir, check=False)
    return int(process.returncode)
