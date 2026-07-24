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


PATH_KEYS = {
    "tushare-data-path",
    "factor-output-path",
    "factor-report-file",
    "backtest-config",
    "launcher-binary",
    "daily-summary-file",
    "family-exposure-file",
    "pipeline-report-file",
}
STAGE_LABELS = {
    "export": "Feature Data Export",
    "backtest": "LEAN Backtest",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_pipeline_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-multi-family-pipeline.json"


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "field-mapping-path": "/home/project/tushare-downloader/tushare_field_mapping.json",
        "factor-output-path": str(root / "Data" / "alternative" / "ashare-multi-family-features"),
        "factor-report-file": str(root / "Results" / "multi-family-export-report.json"),
        "backtest-config": str(root / "Launcher" / "config" / "config-ashare-multi-family-backtest.json"),
        "launcher-binary": str(root / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"),
        "daily-summary-file": str(root / "Results" / "multi-family-daily-summary.csv"),
        "family-exposure-file": str(root / "Results" / "multi-family-family-exposure.csv"),
        "pipeline-report-file": str(root / "Results" / "multi-family-pipeline-report.json"),
        "skip-export-stage-if-output-exists": True,
        "universe": "csi300",
        "start-date": "20200101",
        "end-date": "20251231",
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
        if not path.exists():
            print(f"[warn] pipeline config not found: {path}, using defaults", flush=True)
        else:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


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


def format_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def print_separator() -> None:
    print("=" * 100)


def print_key_value(label: str, value) -> None:
    print(f"{label:<20}: {value}")


def print_pipeline_plan(config: dict, stages: list[str]) -> None:
    print_separator()
    print("Multi-Family Data-Driven Pipeline")
    print_separator()
    print_key_value("Planned stages", " -> ".join(stages) if stages else "none")
    print_key_value("Stage count", len(stages))
    print_key_value("Feature output path", config.get("factor-output-path"))
    print_key_value("Backtest config", config.get("backtest-config"))
    print_key_value("Launcher binary", config.get("launcher-binary"))
    print_key_value("Pipeline report", config.get("pipeline-report-file"))
    print_separator()


def run_export_stage(config: dict) -> dict:
    output_path = Path(config["factor-output-path"])
    if bool_from_config(config.get("skip-export-stage-if-output-exists"), True) and has_factor_output(output_path):
        files = sorted(output_path.glob("*/*/*.csv"))
        return {
            "status": "ok",
            "mode": "reuse",
            "output_path": str(output_path),
            "existing_file_count": len(files),
        }

    export_script = repo_root() / "Scripts" / "export_ashare_multi_family_feature_data.py"
    if not export_script.exists():
        raise FileNotFoundError(f"Missing export script: {export_script}")

    command = [
        sys.executable,
        str(export_script),
        "--tushare-data-path", config["tushare-data-path"],
        "--output-path", config["factor-output-path"],
        "--universe", config.get("universe", "csi300"),
        "--start-date", config.get("start-date", "20200101"),
        "--end-date", config.get("end-date", "20251231"),
        "--report-file", config.get("factor-report-file", ""),
    ]

    field_mapping = config.get("field-mapping-path")
    if field_mapping:
        command.extend(["--field-mapping-path", field_mapping])

    print(f"[export launch] command={' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=repo_root(), capture_output=False)
    if result.returncode != 0:
        raise RuntimeError(f"Export script failed with exit code {result.returncode}")

    return {
        "status": "ok",
        "mode": "export",
        "output_path": str(output_path),
        "existing_file_count": len(sorted(output_path.glob("*/*/*.csv"))),
    }


def run_subprocess_with_heartbeat(command: list[str], cwd: Path, heartbeat_seconds: float) -> tuple[int, float]:
    heartbeat = max(1.0, float(heartbeat_seconds or 30))
    print(f"[backtest launch] command={' '.join(command)}", flush=True)
    process = subprocess.Popen(command, cwd=cwd)
    started = time.monotonic()
    next_heartbeat = started + heartbeat

    while True:
        returncode = process.poll()
        now = time.monotonic()
        if returncode is not None:
            elapsed = now - started
            print(f"[backtest complete] returncode={returncode} elapsed={format_seconds(elapsed)}", flush=True)
            return returncode, elapsed

        if now >= next_heartbeat:
            print(
                f"[backtest heartbeat] status=running elapsed={format_seconds(now - started)} "
                f"waiting_for=LEAN launcher completion",
                flush=True,
            )
            next_heartbeat = now + heartbeat

        time.sleep(max(0.1, min(1.0, next_heartbeat - now)))


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
        "family_exposure_file": config["family-exposure-file"],
    }


def write_pipeline_report(config: dict, payload: dict) -> None:
    report_path = Path(config["pipeline-report-file"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(
    config: dict,
    run_export: bool = True,
    run_backtest: bool = True,
) -> dict:
    stages = []
    if run_export:
        stages.append("export")
    if run_backtest:
        stages.append("backtest")
    print_pipeline_plan(config, stages)
    payload = {
        "status": "ok",
        "config": {
            "factor-output-path": config.get("factor-output-path"),
            "backtest-config": config.get("backtest-config"),
        },
        "stages": {},
    }
    current_stage_index = 0

    try:
        if run_export:
            current_stage_index += 1
            print(f"[{current_stage_index}/{len(stages)}] Feature Data Export", flush=True)
            print_key_value("  Output path", config["factor-output-path"])
            started = time.perf_counter()
            payload["stages"]["export"] = run_export_stage(config)
            elapsed = time.perf_counter() - started
            print_key_value(f"[{current_stage_index}/{len(stages)}] Completed in", f"{elapsed:.2f}s")
            print("-" * 100)

        if run_backtest:
            current_stage_index += 1
            print(f"[{current_stage_index}/{len(stages)}] LEAN Backtest", flush=True)
            print_key_value("  Config", config["backtest-config"])
            print_key_value("  Launcher", config["launcher-binary"])
            started = time.perf_counter()
            payload["stages"]["backtest"] = run_backtest_stage(config)
            elapsed = time.perf_counter() - started
            print_key_value(f"[{current_stage_index}/{len(stages)}] Completed in", f"{elapsed:.2f}s")
            print("-" * 100)
    except Exception as error:
        payload["status"] = "error"
        payload["error"] = str(error)
        write_pipeline_report(config, payload)
        raise

    write_pipeline_report(config, payload)
    print_separator()
    print("Pipeline Completed")
    print_separator()
    print_key_value("Pipeline report", config["pipeline-report-file"])
    print_key_value("Daily summary", config["daily-summary-file"])
    print_separator()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the multi-family data-driven pipeline")
    parser.add_argument("--config", default=str(default_pipeline_config_path()))
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--backtest-only", action="store_true")
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--field-mapping-path")
    parser.add_argument("--factor-output-path")
    parser.add_argument("--factor-report-file")
    parser.add_argument("--backtest-config")
    parser.add_argument("--launcher-binary")
    parser.add_argument("--daily-summary-file")
    parser.add_argument("--family-exposure-file")
    parser.add_argument("--pipeline-report-file")
    parser.add_argument("--universe")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--backtest-heartbeat-seconds", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "tushare-data-path": args.tushare_data_path,
        "field-mapping-path": args.field_mapping_path,
        "factor-output-path": args.factor_output_path,
        "factor-report-file": args.factor_report_file,
        "backtest-config": args.backtest_config,
        "launcher-binary": args.launcher_binary,
        "daily-summary-file": args.daily_summary_file,
        "family-exposure-file": args.family_exposure_file,
        "pipeline-report-file": args.pipeline_report_file,
        "universe": args.universe,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "backtest-heartbeat-seconds": args.backtest_heartbeat_seconds,
    }
    config = load_pipeline_config(args.config, overrides)

    run_export = not args.backtest_only
    run_backtest = not args.export_only

    report = run_pipeline(config, run_export=run_export, run_backtest=run_backtest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
