#!/usr/bin/env python3
"""
SoloQuant Prefect → InfluxDB Export

Writes Prefect pipeline tick results to InfluxDB for Grafana visualization.
Two measurements:

  soloquant_prefect_tick   — per-tick summary (status, duration, stage counts)
  soloquant_prefect_stage  — per-stage detail (status, duration, mode)

These complement (never replace) the existing soloquant_pipeline_funnel,
soloquant_strategy_progress, and strategy_lifecycle measurements.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator
from soloquant_prefect_config import (
    RESULTS_DIR,
    load_config,
    get_influx_config,
)


# ─── Line Protocol Builders ───────────────────────────────────────────────────

def _build_tick_lines(
    report: dict[str, Any],
    now: datetime,
) -> list[str]:
    """Build InfluxDB line protocol for soloquant_prefect_tick measurement."""
    ts_ns = int(now.timestamp() * 1e9)
    status = str(report.get("status", "unknown"))
    started = report.get("started_at_utc", "")
    finished = report.get("finished_at_utc", "")

    # Compute duration in seconds
    duration_s = 0.0
    if started and finished:
        try:
            t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            duration_s = (t1 - t0).total_seconds()
        except (ValueError, TypeError):
            pass

    stages = report.get("stages", [])
    total_stages = len(stages)
    ok_stages = sum(1 for s in stages if s.get("status") == "ok")
    error_stages = sum(1 for s in stages if s.get("status") == "error")
    skipped_stages = sum(1 for s in stages if s.get("status") == "skipped")
    degraded_stages = len(report.get("degraded_stages", []))

    # Status code for color mapping in Grafana
    status_code = {"ok": 1, "skipped": 0, "degraded": 2, "error": 3}.get(status, -1)

    fields = [
        f"status_code={status_code}i",
        f'status_text="{status}"',
        f"duration_seconds={duration_s:.1f}",
        f"total_stages={total_stages}i",
        f"ok_stages={ok_stages}i",
        f"error_stages={error_stages}i",
        f"skipped_stages={skipped_stages}i",
        f"degraded_stages={degraded_stages}i",
    ]

    line = f"soloquant_prefect_tick {','.join(fields)} {ts_ns}"
    return [line]


def _build_stage_lines(
    report: dict[str, Any],
    now: datetime,
) -> list[str]:
    """Build InfluxDB line protocol for soloquant_prefect_stage measurement."""
    ts_ns = int(now.timestamp() * 1e9)
    lines: list[str] = []

    for stage_row in report.get("stages", []):
        if not isinstance(stage_row, dict):
            continue
        stage_name = str(stage_row.get("stage", "unknown"))
        status = str(stage_row.get("status", "unknown"))
        started = stage_row.get("started_at_utc", "")
        finished = stage_row.get("finished_at_utc", "")

        # Compute duration
        duration_s = 0.0
        if started and finished:
            try:
                t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
                duration_s = (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass

        # Extract mode from stage report
        stage_report = stage_row.get("report", {})
        mode = str(stage_report.get("mode", "sync")) if isinstance(stage_report, dict) else "sync"

        # Status code
        status_code = {"ok": 1, "skipped": 0, "error": 3}.get(status, -1)

        escaped_stage = orchestrator.escape_influx_key(stage_name)
        escaped_mode = orchestrator.escape_influx_key(mode)

        fields = [
            f"status_code={status_code}i",
            f'status_text="{status}"',
            f"duration_seconds={duration_s:.2f}",
            f'mode="{mode}"',
        ]

        # Add PID for background tasks
        pid = stage_report.get("pid") if isinstance(stage_report, dict) else None
        if pid:
            fields.append(f"pid={int(pid)}i")

        line = f"soloquant_prefect_stage,stage={escaped_stage},mode={escaped_mode} {','.join(fields)} {ts_ns}"
        lines.append(line)

    return lines


# ─── Export Functions ──────────────────────────────────────────────────────────

def export_prefect_tick_to_influx(
    report: dict[str, Any],
    influx_config: dict[str, Any],
    now: datetime | None = None,
    writer=None,
) -> dict[str, Any]:
    """Write Prefect tick and stage metrics to InfluxDB."""
    now = now or datetime.now(timezone.utc)

    tick_lines = _build_tick_lines(report, now)
    stage_lines = _build_stage_lines(report, now)
    all_lines = tick_lines + stage_lines

    if not all_lines:
        return {"status": "ok", "written": 0}

    url = str(influx_config.get("url") or "http://localhost:8086")
    org = str(influx_config.get("org") or "lean")
    bucket = str(influx_config.get("bucket") or "quant")
    token_env = str(influx_config.get("token-env-var") or "INFLUXDB_TOKEN")
    token = os.getenv(token_env, "").strip()
    if not token:
        token = str(influx_config.get("token-default") or "").strip()

    write_fn = writer or orchestrator.write_lines_to_influx
    try:
        written = write_fn(all_lines, url, org, bucket, token)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "lines": len(all_lines)}

    return {
        "status": "ok",
        "written": written,
        "tick_lines": len(tick_lines),
        "stage_lines": len(stage_lines),
    }


def export_from_task_index(
    influx_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Export the latest tick from prefect-task-index.json to InfluxDB.

    This is useful for cron-based or sqctl.sh-based periodic export
    when the Prefect flow is not running.
    """
    now = now or datetime.now(timezone.utc)
    index_path = RESULTS_DIR / "prefect-task-index.json"
    if not index_path.exists():
        return {"status": "ok", "written": 0, "reason": "no_task_index"}

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {"status": "error", "error": "invalid_task_index"}

    # Reconstruct a report-like structure from the index
    report: dict[str, Any] = {
        "status": index.get("last_tick_status", "unknown"),
        "finished_at_utc": index.get("last_tick_finished_at_utc"),
        "stages": [],
    }
    for stage_name, stage_info in index.get("stages", {}).items():
        report["stages"].append({
            "stage": stage_name,
            "status": stage_info.get("status", "unknown"),
            "started_at_utc": stage_info.get("started_at_utc"),
            "finished_at_utc": stage_info.get("finished_at_utc"),
        })

    if not influx_config:
        config = load_config()
        influx_config = get_influx_config(config)

    return export_prefect_tick_to_influx(report, influx_config, now=now)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Export Prefect tick data to InfluxDB")
    parser.add_argument("--config", default=None, help="Path to config-soloquant.json")
    parser.add_argument("--from-index", action="store_true",
                        help="Export from prefect-task-index.json instead of live report")
    args = parser.parse_args()

    config = load_config(args.config)
    influx_config = get_influx_config(config)

    if args.from_index:
        result = export_from_task_index(influx_config)
    else:
        # Read the latest tick from history
        history_path = RESULTS_DIR / "prefect-tick-history.json"
        if not history_path.exists():
            print("No tick history found. Run the pipeline first.")
            return 1
        try:
            history = json.loads(history_path.read_text())
        except (json.JSONDecodeError, ValueError):
            print("Invalid tick history.")
            return 1
        if not history:
            print("Empty tick history.")
            return 1
        latest = history[-1]
        # Reconstruct report from index
        report = {
            "status": latest.get("last_tick_status", "unknown"),
            "started_at_utc": latest.get("stages", {}) and next(
                (s.get("started_at_utc") for s in latest["stages"].values()
                 if isinstance(s, dict)), ""),
            "finished_at_utc": latest.get("last_tick_finished_at_utc"),
            "stages": [
                {"stage": name, **info}
                for name, info in latest.get("stages", {}).items()
            ],
        }
        result = export_prefect_tick_to_influx(report, influx_config)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    import argparse
    raise SystemExit(main())