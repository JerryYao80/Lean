#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import live_paper_runner as runner
import plan_strategy_runner as plan_runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-industry-rotation-live-paper.json"


def launcher_binary_path() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def build_live_session_root() -> Path:
    return plan_runner.build_live_session_root(repo_root(), "ashare-industry-rotation")


def load_runtime_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    payload = plan_runner.load_json_payload(config_file)
    parameters = payload.get("parameters") if isinstance(payload, dict) else {}
    if not isinstance(parameters, dict):
        parameters = {}

    root = repo_root()
    launcher_base, data_base, results_base = plan_runner.resolve_launcher_runtime_bases(root, payload)
    config_base = launcher_base
    variant_name = str(parameters.get("variant-name") or "rotation_resonance")
    benchmark_file = plan_runner.resolve_path(
        parameters.get("benchmark-file"),
        root / "Data" / "alternative" / "ashare-industry-rotation-live" / "benchmark" / "000300.SH.csv",
        data_base,
    )
    return {
        "variant-name": variant_name,
        "plan-directory": plan_runner.resolve_path(parameters.get("plan-directory"), root / "Data" / "alternative" / "ashare-industry-rotation-live", data_base),
        "plan-file": plan_runner.resolve_path(parameters.get("plan-file"), benchmark_file.parent.parent / f"{variant_name}.csv", data_base),
        "benchmark-file": benchmark_file,
        "daily-nav-file": plan_runner.resolve_path(parameters.get("daily-nav-file"), root / "Results" / "ashare-industry-rotation-live-daily.csv", results_base),
        "rebalance-file": plan_runner.resolve_path(parameters.get("rebalance-file"), root / "Results" / "ashare-industry-rotation-live-rebalances.csv", results_base),
        "summary-file": plan_runner.resolve_path(parameters.get("summary-file"), root / "Results" / "ashare-industry-rotation-live-summary.json", results_base),
        "live-bridge-report-file": plan_runner.resolve_path(parameters.get("live-bridge-report-file"), root / "Results" / "ashare-industry-rotation-live-bridge-report.json", results_base),
        "dataset-catalog": plan_runner.resolve_path(parameters.get("dataset-catalog"), root / "Launcher" / "config" / "config-ashare-dataset-catalog.json", config_base),
        "pipeline-config": plan_runner.resolve_path(parameters.get("pipeline-config"), root / "Launcher" / "config" / "config-ashare-industry-rotation-pipeline.json", config_base),
        "shared-live-market-snapshot-file": plan_runner.resolve_path(parameters.get("shared-live-market-snapshot-file"), root / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json", results_base),
        "shared-live-market-report-file": plan_runner.resolve_path(parameters.get("shared-live-market-report-file"), root / "Results" / "shared-live-market" / "ashare-live-market-report.json", results_base),
        "shared-live-market-archive-path": plan_runner.resolve_path(parameters.get("shared-live-market-archive-path"), root / "Data" / "archive" / "ashare-live-market-daily-quotes", data_base),
        "bridge-ready-timeout-seconds": max(60, plan_runner.safe_int(parameters.get("bridge-ready-timeout-seconds"), 600)),
        "live-plan-poll-interval-seconds": max(30, plan_runner.safe_int(parameters.get("live-plan-poll-interval-seconds"), 180)),
        "shared-live-market-refresh-interval-seconds": max(1, plan_runner.safe_int(parameters.get("shared-live-market-refresh-interval-seconds"), 60)),
        "live-price-source-mode": str(parameters.get("live-price-source-mode") or "auto"),
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data_v2"),
    }


def prepare_session_config(config_path: str | Path | None = None) -> tuple[Path, dict, Path]:
    source_config_path = resolve_live_config_path(config_path)
    runtime_config = load_runtime_config(source_config_path)
    session_root = build_live_session_root()

    plan_root = session_root / "plan-data"
    plan_file = plan_root / f"{runtime_config['variant-name']}.csv"
    benchmark_file = plan_root / "benchmark" / "000300.SH.csv"
    parameter_updates = {
        "plan-directory": str(plan_root),
        "plan-file": str(plan_file),
        "benchmark-file": str(benchmark_file),
        "dataset-catalog": str(runtime_config["dataset-catalog"]),
        "pipeline-config": str(runtime_config["pipeline-config"]),
        "daily-nav-file": str(session_root / "ashare-industry-rotation-live-daily.csv"),
        "rebalance-file": str(session_root / "ashare-industry-rotation-live-rebalances.csv"),
        "summary-file": str(session_root / "ashare-industry-rotation-live-summary.json"),
        "live-bridge-report-file": str(session_root / "ashare-industry-rotation-live-bridge-report.json"),
    }
    return plan_runner.prepare_live_session_config(
        source_config_path=source_config_path,
        session_root=session_root,
        session_config_name="config-ashare-industry-rotation-live-paper.session.json",
        parameter_updates=parameter_updates,
        runtime_loader=load_runtime_config,
    )


def wait_for_bridge_ready(
    config_path: str | Path | None,
    bridge_process,
    bridge_started_at: float,
    timeout_seconds: float,
) -> bool:
    runtime_config = load_runtime_config(config_path)
    return plan_runner.wait_for_plan_bridge_ready(
        bridge_process=bridge_process,
        bridge_started_at=bridge_started_at,
        report_path=Path(runtime_config["live-bridge-report-file"]),
        plan_file=Path(runtime_config["plan-file"]),
        benchmark_file=Path(runtime_config["benchmark-file"]),
        timeout_seconds=timeout_seconds,
    )


def print_live_plan(config_path: Path, runtime_config: dict, session_root: Path | None = None) -> None:
    print("=" * 100)
    print("A-Share Industry Rotation Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    if session_root is not None:
        print(f"Session root        : {session_root}", flush=True)
    print("Market data         : full-market rt_k/rt_etf_k snapshot with GBM fallback off-session", flush=True)
    print("Console output      : native no-broker LEAN live-paper stdout", flush=True)
    print(f"Variant             : {runtime_config['variant-name']}", flush=True)
    print(f"Plan file           : {runtime_config['plan-file']}", flush=True)
    print(f"Benchmark file      : {runtime_config['benchmark-file']}", flush=True)
    print(f"Pipeline config     : {runtime_config['pipeline-config']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-bridge-report-file']}", flush=True)
    print(f"Shared mkt snapshot : {runtime_config['shared-live-market-snapshot-file']}", flush=True)
    print(f"Shared mkt report   : {runtime_config['shared-live-market-report-file']}", flush=True)
    print(f"Shared mkt archive  : {runtime_config['shared-live-market-archive-path']}", flush=True)
    print(f"Market cache refresh: {runtime_config['shared-live-market-refresh-interval-seconds']} seconds", flush=True)
    print(f"Plan refresh        : {runtime_config['live-plan-poll-interval-seconds']} seconds", flush=True)
    print(f"Bridge wait timeout : {runtime_config['bridge-ready-timeout-seconds']} seconds", flush=True)
    print("Process flow        : full-market realtime snapshot -> industry rotation plan refresh -> standard LEAN live-paper", flush=True)
    print("=" * 100)


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or runner.default_bridge_python_executable()
    return [
        executable,
        str(repo_root() / "Scripts" / "ashare_industry_rotation_live_bridge.py"),
        "--config",
        str(resolve_live_config_path(config_path)),
    ]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    return plan_runner.build_launcher_command(repo_root(), resolve_live_config_path(config_path))


def run_live_paper(
    config_path: str | Path | None = None,
    start_bridge: bool = True,
    start_launcher: bool = True,
    python_executable: str | None = None,
) -> int:
    session_config_path, runtime_config, session_root = prepare_session_config(config_path)
    resolved_config_path = session_config_path
    print_live_plan(resolved_config_path, runtime_config, session_root=session_root)

    bridge_spec = runner.ProcessSpec(
        command=build_bridge_command(resolved_config_path, python_executable),
        cwd=repo_root(),
        label="bridge",
        passthrough=False,
    )
    launcher_command, workdir = build_launcher_command(resolved_config_path)
    launcher_spec = runner.ProcessSpec(
        command=launcher_command,
        cwd=workdir,
        label="lean",
        passthrough=True,
    )
    return runner.run_native_live_paper_session(
        strategy_name="A-share industry rotation live paper",
        start_bridge=start_bridge,
        start_launcher=start_launcher,
        bridge_spec=bridge_spec,
        launcher_spec=launcher_spec,
        wait_for_bridge_ready=(lambda process, bridge_started_at: wait_for_bridge_ready(
            resolved_config_path,
            process,
            bridge_started_at,
            float(runtime_config["bridge-ready-timeout-seconds"]),
        )) if start_bridge and start_launcher else None,
        bridge_timeout_message="Timed out waiting for bridge to build live industry rotation plan files.",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch A-share industry rotation live bridge and LEAN live-paper engine")
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
