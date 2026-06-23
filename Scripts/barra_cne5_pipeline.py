#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import barra_cne5_factor_bridge
import barra_cne5_monte_carlo


ENVIRONMENT_OVERRIDES = {
    "external-factor-path": "BARRA_CNE5_EXTERNAL_FACTOR_PATH",
    "tushare-data-path": "BARRA_CNE5_TUSHARE_DATA_PATH",
    "launcher-binary": "LEAN_LAUNCHER_PATH",
}
PATH_KEYS = {
    "external-factor-path",
    "tushare-data-path",
    "factor-output-path",
    "factor-report-file",
    "backtest-config",
    "launcher-binary",
    "daily-summary-file",
    "factor-exposure-file",
    "monte-carlo-report-file",
    "monte-carlo-json-report-file",
    "pipeline-report-file",
}
STAGE_LABELS = {
    "factor": "Factor Preparation",
    "backtest": "LEAN Backtest",
    "monte_carlo": "Monte Carlo Analysis",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_pipeline_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-barra-cne5-pipeline.json"


def default_config() -> dict:
    root = repo_root()
    return {
        "factor-source-mode": "auto",
        "random-factor-seed": 42,
        "external-factor-path": None,
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "factor-output-path": str(root / "Data" / "alternative" / "barra-cne5-factors"),
        "factor-report-file": str(root / "Results" / "barra-cne5-factor-bridge-report.json"),
        "backtest-config": str(root / "Launcher" / "config" / "config-barra-cne5-backtest.json"),
        "launcher-binary": str(root / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"),
        "daily-summary-file": str(root / "Results" / "barra-cne5-daily-summary.csv"),
        "factor-exposure-file": str(root / "Results" / "barra-cne5-factor-exposure.csv"),
        "monte-carlo-report-file": str(root / "Results" / "barra-cne5-monte-carlo-report.txt"),
        "monte-carlo-json-report-file": str(root / "Results" / "barra-cne5-monte-carlo-report.json"),
        "pipeline-report-file": str(root / "Results" / "barra-cne5-pipeline-report.json"),
        "skip-factor-stage-if-output-exists": True,
        "universe": "csi300",
        "index-code": "000300.SH",
        "market-symbol": "000300.SH",
        "start-date": "20200101",
        "end-date": "20251231",
        "date": None,
        "trial-count": 5000,
        "horizon-days": 252,
        "block-size": 21,
        "seed": 42,
        "factor-perturbation-scale": 0.15,
        "factor-worker-count": "auto",
        "parallel-date-block-size": 5,
        "progress-interval-symbols": 500,
        "progress-interval-files": 100,
        "progress-interval-trials": 250,
        "backtest-heartbeat-seconds": 30,
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()
    if config_path:
        path = Path(config_path).resolve()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def apply_environment_overrides(config: dict) -> dict:
    resolved = dict(config)
    for key, env_name in ENVIRONMENT_OVERRIDES.items():
        if resolved.get(key):
            continue
        value = os.getenv(env_name)
        if value:
            resolved[key] = value
    return resolved


def bool_from_config(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return default


def has_factor_output(output_path: str | Path) -> bool:
    root = Path(output_path)
    return any(root.glob("*/*/*.csv"))


def discover_external_factor_path() -> str | None:
    root = repo_root()
    direct_candidates = [
        root / "local_data" / "barra-cne5-factors",
        root / "local_data" / "barra_cne5_factors",
        root / "local_data" / "barra-cne5",
        root / "local_data" / "barra_cne5",
        root / "local_data" / "barra",
        root / "Results" / "barra-cne5-factors",
    ]

    for candidate in direct_candidates:
        if not candidate.exists():
            continue
        if barra_cne5_factor_bridge.collect_external_factor_files(candidate):
            return str(candidate.resolve())

    searchable_roots = [
        root / "local_data",
        root / "Results",
    ]
    for search_root in searchable_roots:
        if not search_root.exists():
            continue
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in barra_cne5_factor_bridge.SUPPORTED_FACTOR_EXTENSIONS:
                continue
            parts = [part.lower() for part in path.parts]
            if not any("barra" in part or "cne5" in part for part in parts):
                continue
            return str(path.resolve())

    return None


def resolve_runtime_config(config: dict) -> dict:
    resolved = apply_environment_overrides(config)
    if not resolved.get("external-factor-path"):
        discovered = discover_external_factor_path()
        if discovered:
            resolved["external-factor-path"] = discovered
    return resolve_config_paths(resolved, repo_root())


def planned_stages(run_factor: bool, run_backtest: bool, run_monte_carlo: bool) -> list[str]:
    stages: list[str] = []
    if run_factor:
        stages.append("factor")
    if run_backtest:
        stages.append("backtest")
    if run_monte_carlo:
        stages.append("monte_carlo")
    return stages


def stage_percentages(index: int, total: int) -> tuple[int, int]:
    return int(((index - 1) * 100) / max(total, 1)), int((index * 100) / max(total, 1))


def print_separator() -> None:
    print("=" * 100)


def print_key_value(label: str, value) -> None:
    print(f"{label:<20}: {value}")


def format_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def print_pipeline_plan(config: dict, stages: list[str]) -> None:
    print_separator()
    print("Barra CNE5 Pipeline")
    print_separator()
    print_key_value("Planned stages", " -> ".join(stages) if stages else "none")
    print_key_value("Stage count", len(stages))
    print_key_value("Factor source mode", resolve_factor_mode(config))
    print_key_value("External factors", config.get("external-factor-path") or "(not set)")
    print_key_value("Factor output path", config.get("factor-output-path"))
    print_key_value("Backtest config", config.get("backtest-config"))
    print_key_value("Launcher binary", config.get("launcher-binary"))
    print_key_value("Pipeline report", config.get("pipeline-report-file"))
    print_separator()


def print_stage_start(index: int, total: int, stage_key: str, details: dict[str, object]) -> None:
    start_percent, end_percent = stage_percentages(index, total)
    print(f"[{index}/{total} | {start_percent}% -> {end_percent}%] {STAGE_LABELS[stage_key]}")
    for key, value in details.items():
        print_key_value(f"  {key}", value)


def summarize_stage_result(stage_key: str, result: dict) -> dict[str, object]:
    if stage_key == "factor":
        mode = result.get("mode")
        if mode == "reuse":
            return {
                "Mode": mode,
                "Existing files": result.get("existing_file_count", 0),
                "Output path": result.get("output_path"),
            }
        if mode == "import":
            return {
                "Mode": mode,
                "Accepted files": result.get("accepted_file_count", 0),
                "Written symbols": result.get("written_symbol_count", 0),
                "Date range": f"{result.get('date_range', {}).get('start')} -> {result.get('date_range', {}).get('end')}",
            }
        return {
            "Mode": mode,
            "Written symbols": result.get("written_symbol_count", 0),
            "Trading dates": result.get("trading_dates", {}).get("count", 0),
            "Universe": result.get("resolved_symbol_count", 0),
            "Workers": result.get("factor_worker_count", 1),
            "Parallel": result.get("parallel_mode", "single-process"),
        }

    if stage_key == "backtest":
        return {
            "Return code": result.get("returncode"),
            "Daily summary": result.get("daily_summary_file"),
            "Factor exposure": result.get("factor_exposure_file"),
        }

    scenarios = sorted((result.get("scenarios") or {}).keys())
    return {
        "Scenarios": ", ".join(scenarios) if scenarios else "(none)",
        "Trade days": result.get("base_backtest", {}).get("trade_days", 0),
        "JSON report": result.get("json_report_file", result.get("json-report-file", "(configured output)")),
    }


def print_stage_complete(index: int, total: int, stage_key: str, result: dict, elapsed_seconds: float) -> None:
    print_key_value(f"[{index}/{total}] Completed in", f"{elapsed_seconds:.2f}s")
    for key, value in summarize_stage_result(stage_key, result).items():
        print_key_value(f"  {key}", value)
    print("-" * 100)


def print_stage_failure(index: int, total: int, stage_key: str, error: Exception) -> None:
    print_key_value(f"[{index}/{total}] Failed", STAGE_LABELS[stage_key])
    print_key_value("  Error", str(error))
    print_separator()


def resolve_factor_mode(config: dict) -> str:
    requested = (config.get("factor-source-mode") or "auto").strip().lower()
    if requested in {"reuse", "skip"}:
        return "reuse"
    if requested == "import":
        return "import"
    if requested == "random":
        return "random"
    if requested == "build":
        return "build"

    if bool_from_config(config.get("skip-factor-stage-if-output-exists"), True) and has_factor_output(config["factor-output-path"]):
        return "reuse"
    if config.get("external-factor-path"):
        return "import"
    return "build"


def run_factor_stage(config: dict) -> dict:
    mode = resolve_factor_mode(config)
    if mode == "reuse":
        output_path = Path(config["factor-output-path"])
        files = sorted(output_path.glob("*/*/*.csv"))
        return {
            "status": "ok",
            "mode": "reuse",
            "output_path": str(output_path),
            "existing_file_count": len(files),
        }

    bridge_config = barra_cne5_factor_bridge.load_pipeline_config(
        overrides={
            "factor-source-mode": mode,
            "external-factor-path": config.get("external-factor-path"),
            "tushare-data-path": config.get("tushare-data-path"),
            "output-path": config.get("factor-output-path"),
            "report-file": config.get("factor-report-file"),
            "random-factor-seed": config.get("random-factor-seed"),
            "universe": config.get("universe"),
            "index-code": config.get("index-code"),
            "market-symbol": config.get("market-symbol"),
            "start-date": config.get("start-date"),
            "end-date": config.get("end-date"),
            "date": config.get("date"),
            "symbols": config.get("symbols"),
            "factor-worker-count": config.get("factor-worker-count"),
            "parallel-date-block-size": config.get("parallel-date-block-size"),
            "progress-interval-symbols": config.get("progress-interval-symbols"),
            "progress-interval-files": config.get("progress-interval-files"),
        }
    )
    return barra_cne5_factor_bridge.run_factor_bridge(bridge_config)


def run_subprocess_with_heartbeat(command: list[str], cwd: Path, heartbeat_seconds: float) -> tuple[int, float]:
    heartbeat = max(1.0, float(heartbeat_seconds or 30))
    print(f"[backtest launch] command={' '.join(command)}", flush=True)
    print(
        f"[backtest launch] cwd={cwd} heartbeat_interval={format_seconds(heartbeat)}",
        flush=True,
    )
    process = subprocess.Popen(command, cwd=cwd)
    started = time.monotonic()
    next_heartbeat = started + heartbeat

    while True:
        returncode = process.poll()
        now = time.monotonic()
        if returncode is not None:
            elapsed = now - started
            print(
                f"[backtest complete] returncode={returncode} elapsed={format_seconds(elapsed)}",
                flush=True,
            )
            return returncode, elapsed

        if now >= next_heartbeat:
            print(
                f"[backtest heartbeat] status=running elapsed={format_seconds(now - started)} "
                f"waiting_for=LEAN launcher completion",
                flush=True,
            )
            next_heartbeat = now + heartbeat

        sleep_seconds = max(0.1, min(1.0, next_heartbeat - now))
        time.sleep(sleep_seconds)


def run_backtest_stage(config: dict) -> dict:
    launcher = Path(config["launcher-binary"])
    if not launcher.exists():
        raise FileNotFoundError(f"Missing launcher binary: {launcher}")

    backtest_config = Path(config["backtest-config"])
    if not backtest_config.exists():
        raise FileNotFoundError(f"Missing backtest config: {backtest_config}")

    command = [str(launcher), "--config", str(backtest_config)]
    returncode, elapsed_seconds = run_subprocess_with_heartbeat(
        command,
        cwd=launcher.parent,
        heartbeat_seconds=float(config.get("backtest-heartbeat-seconds", 30)),
    )
    if returncode != 0:
        raise RuntimeError(f"LEAN launcher failed with exit code {returncode}")

    return {
        "status": "ok",
        "mode": "backtest",
        "command": command,
        "returncode": returncode,
        "elapsed_seconds": elapsed_seconds,
        "daily_summary_file": config["daily-summary-file"],
        "factor_exposure_file": config["factor-exposure-file"],
    }


def run_monte_carlo_stage(config: dict) -> dict:
    monte_carlo_config = barra_cne5_monte_carlo.load_pipeline_config(
        overrides={
            "daily-summary-file": config.get("daily-summary-file"),
            "factor-exposure-file": config.get("factor-exposure-file"),
            "trial-count": config.get("trial-count"),
            "horizon-days": config.get("horizon-days"),
            "block-size": config.get("block-size"),
            "seed": config.get("seed"),
            "factor-perturbation-scale": config.get("factor-perturbation-scale"),
            "report-file": config.get("monte-carlo-report-file"),
            "json-report-file": config.get("monte-carlo-json-report-file"),
            "progress-interval-trials": config.get("progress-interval-trials"),
        }
    )
    report = barra_cne5_monte_carlo.run_monte_carlo(monte_carlo_config)
    report["status"] = "ok"
    report["json_report_file"] = config.get("monte-carlo-json-report-file")
    return report


def write_pipeline_report(config: dict, payload: dict) -> None:
    report_path = Path(config["pipeline-report-file"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(
    config: dict,
    run_factor: bool = True,
    run_backtest: bool = True,
    run_monte_carlo: bool = True,
) -> dict:
    config = resolve_runtime_config(config)
    stages = planned_stages(run_factor, run_backtest, run_monte_carlo)
    print_pipeline_plan(config, stages)
    payload = {
        "status": "ok",
        "config": {
            "factor-source-mode": resolve_factor_mode(config),
            "external-factor-path": config.get("external-factor-path"),
            "factor-output-path": config.get("factor-output-path"),
            "backtest-config": config.get("backtest-config"),
        },
        "stages": {},
    }
    current_stage_index = 0

    try:
        if run_factor:
            current_stage_index += 1
            print_stage_start(current_stage_index, len(stages), "factor", {
                "Action": {
                    "reuse": "reuse existing standardized factor files",
                    "import": "import external factor files into LEAN format",
                    "random": "generate synthetic random factor files for pipeline unblock",
                    "build": "build Barra factors from Tushare parquet datasets",
                }[resolve_factor_mode(config)],
                "Factor workers": config.get("factor-worker-count"),
                "Date block size": config.get("parallel-date-block-size"),
                "Factor output path": config["factor-output-path"],
                "External factors": config.get("external-factor-path") or "(not set)",
            })
            started = time.perf_counter()
            payload["stages"]["factor"] = run_factor_stage(config)
            print_stage_complete(current_stage_index, len(stages), "factor", payload["stages"]["factor"], time.perf_counter() - started)
        elif not has_factor_output(config["factor-output-path"]):
            raise FileNotFoundError(
                f"Factor stage skipped but no factor files exist under {config['factor-output-path']}"
            )

        if run_backtest:
            current_stage_index += 1
            print_stage_start(current_stage_index, len(stages), "backtest", {
                "Action": "run QuantConnect LEAN launcher with isolated Barra config",
                "Config": config["backtest-config"],
                "Launcher": config["launcher-binary"],
                "Heartbeat": f"{config['backtest-heartbeat-seconds']} seconds",
                "Daily summary": config["daily-summary-file"],
                "Factor exposure": config["factor-exposure-file"],
            })
            started = time.perf_counter()
            payload["stages"]["backtest"] = run_backtest_stage(config)
            print_stage_complete(current_stage_index, len(stages), "backtest", payload["stages"]["backtest"], time.perf_counter() - started)

        if run_monte_carlo:
            current_stage_index += 1
            print_stage_start(current_stage_index, len(stages), "monte_carlo", {
                "Action": "run Monte Carlo analysis from backtest outputs",
                "Daily summary": config["daily-summary-file"],
                "Factor exposure": config["factor-exposure-file"],
                "Trial count": config["trial-count"],
                "Horizon days": config["horizon-days"],
            })
            started = time.perf_counter()
            payload["stages"]["monte_carlo"] = run_monte_carlo_stage(config)
            print_stage_complete(current_stage_index, len(stages), "monte_carlo", payload["stages"]["monte_carlo"], time.perf_counter() - started)
    except Exception as error:
        payload["status"] = "error"
        if current_stage_index > 0 and current_stage_index <= len(stages):
            failed_stage = stages[current_stage_index - 1]
        else:
            failed_stage = "initialization"
        payload["failed_stage"] = failed_stage
        payload["error"] = str(error)
        write_pipeline_report(config, payload)
        if failed_stage in STAGE_LABELS:
            print_stage_failure(current_stage_index, len(stages), failed_stage, error)
        raise

    write_pipeline_report(config, payload)
    print_separator()
    print("Pipeline Completed")
    print_separator()
    print_key_value("Pipeline report", config["pipeline-report-file"])
    print_key_value("Daily summary", config["daily-summary-file"])
    print_key_value("Monte Carlo JSON", config["monte-carlo-json-report-file"])
    print_separator()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the automated Barra CNE5 pipeline")
    parser.add_argument("--config", default=str(default_pipeline_config_path()))
    parser.add_argument("--factor-only", action="store_true")
    parser.add_argument("--backtest-only", action="store_true")
    parser.add_argument("--monte-carlo-only", action="store_true")
    parser.add_argument("--factor-source-mode")
    parser.add_argument("--random-factor-seed", type=int)
    parser.add_argument("--external-factor-path")
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--factor-output-path")
    parser.add_argument("--factor-report-file")
    parser.add_argument("--backtest-config")
    parser.add_argument("--launcher-binary")
    parser.add_argument("--daily-summary-file")
    parser.add_argument("--factor-exposure-file")
    parser.add_argument("--monte-carlo-report-file")
    parser.add_argument("--monte-carlo-json-report-file")
    parser.add_argument("--pipeline-report-file")
    parser.add_argument("--universe")
    parser.add_argument("--index-code")
    parser.add_argument("--market-symbol")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--date")
    parser.add_argument("--trial-count", type=int)
    parser.add_argument("--horizon-days", type=int)
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--factor-perturbation-scale", type=float)
    parser.add_argument("--factor-worker-count")
    parser.add_argument("--parallel-date-block-size", type=int)
    parser.add_argument("--progress-interval-symbols", type=int)
    parser.add_argument("--progress-interval-files", type=int)
    parser.add_argument("--progress-interval-trials", type=int)
    parser.add_argument("--backtest-heartbeat-seconds", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "factor-source-mode": args.factor_source_mode,
        "random-factor-seed": args.random_factor_seed,
        "external-factor-path": args.external_factor_path,
        "tushare-data-path": args.tushare_data_path,
        "factor-output-path": args.factor_output_path,
        "factor-report-file": args.factor_report_file,
        "backtest-config": args.backtest_config,
        "launcher-binary": args.launcher_binary,
        "daily-summary-file": args.daily_summary_file,
        "factor-exposure-file": args.factor_exposure_file,
        "monte-carlo-report-file": args.monte_carlo_report_file,
        "monte-carlo-json-report-file": args.monte_carlo_json_report_file,
        "pipeline-report-file": args.pipeline_report_file,
        "universe": args.universe,
        "index-code": args.index_code,
        "market-symbol": args.market_symbol,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "date": args.date,
        "trial-count": args.trial_count,
        "horizon-days": args.horizon_days,
        "block-size": args.block_size,
        "seed": args.seed,
        "factor-perturbation-scale": args.factor_perturbation_scale,
        "factor-worker-count": args.factor_worker_count,
        "parallel-date-block-size": args.parallel_date_block_size,
        "progress-interval-symbols": args.progress_interval_symbols,
        "progress-interval-files": args.progress_interval_files,
        "progress-interval-trials": args.progress_interval_trials,
        "backtest-heartbeat-seconds": args.backtest_heartbeat_seconds,
    }
    config = load_pipeline_config(args.config, overrides)
    config = resolve_runtime_config(config)

    run_factor = not args.backtest_only and not args.monte_carlo_only
    run_backtest = not args.factor_only and not args.monte_carlo_only
    run_monte = not args.factor_only and not args.backtest_only

    report = run_pipeline(config, run_factor=run_factor, run_backtest=run_backtest, run_monte_carlo=run_monte)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
