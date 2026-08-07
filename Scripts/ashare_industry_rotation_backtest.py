#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import ashare_industry_rotation_pipeline as rotation_pipeline
import plan_strategy_runner as plan_runner


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_backtest_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-industry-rotation-lean-backtest.json"


def resolve_backtest_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_backtest_config_path().resolve()


def load_runtime_config(config_path: str | Path | None = None) -> tuple[dict, Path]:
    return plan_runner.load_backtest_pipeline_config(
        resolve_backtest_config_path(config_path),
        repo_root() / "Launcher" / "config" / "config-ashare-industry-rotation-pipeline.json",
    )


def run_pipeline_stage(backtest_config_path: str | Path | None = None) -> dict:
    resolved_config_path = resolve_backtest_config_path(backtest_config_path)
    parameters, pipeline_config_path = load_runtime_config(resolved_config_path)
    plan_file = plan_runner.resolve_config_relative_path(parameters.get("plan-file"), resolved_config_path)
    benchmark_file = plan_runner.resolve_config_relative_path(parameters.get("benchmark-file"), resolved_config_path)
    overrides = {
        "plan-directory": str(plan_file.parent) if plan_file is not None else None,
        "benchmark-file": str(benchmark_file) if benchmark_file is not None else None,
    }
    return rotation_pipeline.run_pipeline(rotation_pipeline.load_pipeline_config(pipeline_config_path, overrides))


def run_launcher_stage(backtest_config_path: str | Path | None = None) -> int:
    return plan_runner.run_launcher_stage(repo_root(), resolve_backtest_config_path(backtest_config_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run industry rotation pipeline and standard LEAN backtest")
    parser.add_argument("--config", default=str(default_backtest_config_path()))
    parser.add_argument("--pipeline-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.launcher_only:
        report = run_pipeline_stage(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if args.pipeline_only:
            return 0
    return run_launcher_stage(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
