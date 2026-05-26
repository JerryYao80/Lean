#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import ashare_etf_dual_rotation_pipeline as dual_pipeline
import plan_strategy_runner as plan_runner
import tushare_lean_export


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_backtest_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-etf-dual-rotation-lean-backtest.json"


def resolve_backtest_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_backtest_config_path().resolve()


def load_runtime_config(config_path: str | Path | None = None) -> tuple[dict, Path]:
    return plan_runner.load_backtest_pipeline_config(
        resolve_backtest_config_path(config_path),
        repo_root() / "Launcher" / "config" / "config-ashare-etf-dual-rotation-pipeline.json",
    )


def normalize_trade_date(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return text
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def load_plan_symbol_universe(plan_file: str | Path) -> list[str]:
    symbols: set[str] = set()
    with Path(plan_file).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = str((row or {}).get("symbol") or "").strip().upper()
            if symbol:
                symbols.add(symbol)
    return sorted(symbols)


def load_export_runtime_config(backtest_config_path: str | Path | None = None) -> dict:
    resolved_config_path = resolve_backtest_config_path(backtest_config_path)
    parameters, pipeline_config_path = load_runtime_config(resolved_config_path)
    payload = plan_runner.load_json_payload(resolved_config_path)
    _, data_base, results_base = plan_runner.resolve_launcher_runtime_bases(repo_root(), payload)
    pipeline_config = dual_pipeline.load_pipeline_config(pipeline_config_path)
    plan_file = plan_runner.resolve_config_relative_path(parameters.get("plan-file"), resolved_config_path)
    return {
        "plan-file": str(plan_file) if plan_file is not None else None,
        "lean-data-path": str(data_base),
        "tushare-data-path": str(pipeline_config["tushare-data-path"]),
        "start-date": normalize_trade_date(parameters.get("start-date")),
        "end-date": normalize_trade_date(parameters.get("end-date")),
        "report-file": str(results_base / "ashare-etf-dual-rotation-lean-export-report.json"),
    }


def run_pipeline_stage(backtest_config_path: str | Path | None = None) -> dict:
    resolved_config_path = resolve_backtest_config_path(backtest_config_path)
    parameters, pipeline_config_path = load_runtime_config(resolved_config_path)
    plan_file = plan_runner.resolve_config_relative_path(parameters.get("plan-file"), resolved_config_path)
    benchmark_file = plan_runner.resolve_config_relative_path(parameters.get("benchmark-file"), resolved_config_path)
    overrides = {
        "plan-directory": str(plan_file.parent) if plan_file is not None else None,
        "benchmark-file": str(benchmark_file) if benchmark_file is not None else None,
    }
    return dual_pipeline.run_pipeline(dual_pipeline.load_pipeline_config(pipeline_config_path, overrides))


def run_export_stage(backtest_config_path: str | Path | None = None) -> dict:
    config = load_export_runtime_config(backtest_config_path)
    plan_file = config.get("plan-file")
    if not plan_file or not Path(plan_file).exists():
        raise FileNotFoundError(f"ETF dual rotation plan file was not found: {plan_file}")

    universe = load_plan_symbol_universe(plan_file)
    return tushare_lean_export.export_symbol_universe(
        universe=universe,
        tushare_data_path=config["tushare-data-path"],
        lean_data_path=config["lean-data-path"],
        start_date=config.get("start-date"),
        end_date=config.get("end-date"),
        report_file=config.get("report-file"),
    )


def run_launcher_stage(backtest_config_path: str | Path | None = None) -> int:
    return plan_runner.run_launcher_stage(repo_root(), resolve_backtest_config_path(backtest_config_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ETF dual rotation pipeline and standard LEAN backtest")
    parser.add_argument("--config", default=str(default_backtest_config_path()))
    parser.add_argument("--pipeline-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.launcher_only:
        pipeline_report = run_pipeline_stage(args.config)
        print(json.dumps(pipeline_report, ensure_ascii=False, indent=2), flush=True)
        export_report = run_export_stage(args.config)
        print(json.dumps(export_report, ensure_ascii=False, indent=2), flush=True)
        if args.pipeline_only:
            return 0
    return run_launcher_stage(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
