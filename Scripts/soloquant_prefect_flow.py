#!/usr/bin/env python3
"""
SoloQuant Prefect Flow — Phase 2: Decomposed Pipeline Tick

Each pipeline stage runs as an individual Prefect @task, providing
granular observability, retries, and timeout enforcement in the
Prefect UI while preserving backward compatibility with:
  - PipelineState (soloquant-pipeline-state.json)
  - PID files for background processes
  - InfluxDB exports
  - sqctl.sh / Grafana integration (prefect-task-index.json)

The flow orchestrates the same 14 stages in the same order, with the
same interval-based skip logic and error/degradation handling.

Usage:
    # One-shot run (like --once)
    python3 soloquant_prefect_flow.py --run-once

    # Run as Prefect deployment (scheduled every 5 minutes)
    python3 soloquant_prefect_flow.py --deploy

    # Force run, ignoring interval gates
    python3 soloquant_prefect_flow.py --run-once --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure Scripts/ is on sys.path for imports
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from prefect import flow, get_run_logger

import soloquant_orchestrator as orchestrator
from soloquant_pipeline_runner import (
    PipelineState,
    PipelineLogger,
    default_state_path,
    default_log_path,
    is_pipeline_due,
    LLM_BACKGROUND_STAGES,
)

from soloquant_prefect_config import (
    PREFECT_API_URL,
    PREFECT_WORK_POOL,
    POLL_SECONDS,
    RESULTS_DIR,
    PIPELINE_STAGES,
    load_config,
    get_pipeline_config,
)

from soloquant_prefect_tasks import (
    watch_local_src,
    ingest_local_strategies,
    data_driven_crawl,
    crawl_research,
    prepare_reproduction,
    prepare_iv_data,
    build_event_graph,
    build_event_signals,
    reproduce_one,
    materialize_variants,
    optimize_backtests,
    prepare_live_market_data,
    update_lifecycle,
    run_live_paper,
    export_influx,
    monitor_background_jobs,
)


# ─── Stage dispatch: map stage name → Prefect @task function ──────────────────
# Each entry is (task_function, needs_run_date, needs_now_datetime)
STAGE_DISPATCH = {
    "ingest_local_strategies":    (ingest_local_strategies,    True,  False),
    "data_driven_crawl":          (data_driven_crawl,          True,  False),
    "crawl_research":             (crawl_research,             True,  False),
    "crawl_api_sources":         (crawl_api_sources,          True,  False),
    "prepare_reproduction":       (prepare_reproduction,       True,  False),
    "prepare_iv_data":            (prepare_iv_data,            True,  False),
    "build_event_graph":          (build_event_graph,          True,  False),
    "build_event_signals":        (build_event_signals,        True,  False),
    "reproduce_one":              (reproduce_one,              True,  False),
    "materialize_variants":       (materialize_variants,       True,  False),
    "optimize_backtests":         (optimize_backtests,         True,  False),
    "prepare_live_market_data":   (prepare_live_market_data,   False, True),
    "update_lifecycle":           (update_lifecycle,           False, False),
    "run_live_paper":             (run_live_paper,             False, False),
    "export_influx":              (export_influx,              True,  False),
}

# Stages where non-ok status means "degraded" (continue) vs "error" (break)
DEGRADED_ON_FAIL = {
    "ingest_local_strategies", "crawl_research", "crawl_api_sources", "prepare_reproduction",
    "prepare_iv_data", "build_event_graph", "build_event_signals",
    "reproduce_one", "optimize_backtests", "export_influx",
}


@flow(
    name="soloquant-pipeline-tick",
    description="Decomposed pipeline tick: run each stage as a Prefect @task.",
    timeout_seconds=600,
    retries=0,  # The flow itself handles errors internally
)
def soloquant_pipeline_tick(
    config_path: str | None = None,
    mode: str = "work",
    run_date: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run one pipeline tick as decomposed Prefect tasks.

    Each stage is a separate @task call with Prefect-native retries,
    timeouts, and observability. The overall tick report is assembled
    from individual task results to maintain backward compatibility.
    """
    logger = get_run_logger()

    config = load_config(config_path)
    state_path = default_state_path(config)
    state = PipelineState(state_path)
    now = datetime.now(timezone.utc)
    pipeline_config = get_pipeline_config(config)
    poll_seconds = max(1, int(pipeline_config.get("poll-seconds", POLL_SECONDS)))

    # Check top-level interval gate (same logic as daemon mode)
    if not force and mode == "work" and not is_pipeline_due(
        now, state.last_finished_at(), interval_seconds=poll_seconds
    ):
        report = {
            "status": "skipped",
            "reason": "interval_not_elapsed",
            "now_utc": now.isoformat(),
            "last_finished_at_utc": state.last_finished_at(),
            "finished_at_utc": now.isoformat(),
        }
        logger.info("tick_skipped reason=interval_not_elapsed")
        return report

    logger.info("tick_start mode=%s force=%s poll_seconds=%d", mode, force, poll_seconds)

    started_at = datetime.now(timezone.utc)
    tick_report: dict[str, Any] = {
        "status": "ok",
        "started_at_utc": started_at.isoformat(),
        "stages": [],
        "degraded_stages": [],
    }

    background_stages_seen: list[str] = []

    for stage_name in PIPELINE_STAGES:
        task_fn, needs_run_date, needs_now = STAGE_DISPATCH[stage_name]

        logger.info("stage_dispatch stage=%s task=%s", stage_name, task_fn.__name__)

        # Build task parameters
        task_kwargs: dict[str, Any] = {"config": config}
        if needs_run_date:
            task_kwargs["run_date"] = run_date
        if needs_now:
            task_kwargs["now"] = now

        # Call the Prefect @task
        stage_started_at = datetime.now(timezone.utc)
        try:
            stage_report = task_fn(**task_kwargs)
        except Exception as exc:
            # Prefect task raised — treat as error
            stage_report = {"status": "error", "error": str(exc)}
            logger.error("stage_exception stage=%s error=%s", stage_name, exc)

        # Ensure stage_report is a dict
        if not isinstance(stage_report, dict):
            stage_report = {"status": "ok", "result": stage_report}

        stage_status = stage_report.get("status", "ok")
        stage_finished_at = datetime.now(timezone.utc)

        stage_row = {
            "stage": stage_name,
            "status": stage_status,
            "started_at_utc": stage_started_at.isoformat(),
            "finished_at_utc": stage_finished_at.isoformat(),
            "report": stage_report,
        }
        tick_report["stages"].append(stage_row)

        logger.info("stage_complete stage=%s status=%s", stage_name, stage_status)

        # Track background stages for monitor task
        if stage_name in LLM_BACKGROUND_STAGES or stage_report.get("mode") == "background":
            background_stages_seen.append(stage_name)

        # Error / degradation handling — same logic as run_pipeline_tick()
        if stage_status != "ok":
            if stage_name in DEGRADED_ON_FAIL:
                tick_report["status"] = "degraded"
                tick_report["degraded_stage"] = stage_name
                tick_report["degraded_stages"].append(stage_name)
                logger.warning("stage_degraded stage=%s", stage_name)
                continue  # Continue to next stage
            elif stage_name not in LLM_BACKGROUND_STAGES:
                # Non-background, non-degradable → hard error, break
                tick_report["status"] = "error"
                tick_report["failed_stage"] = stage_name
                logger.error("stage_failed stage=%s — breaking tick", stage_name)
                break

    # Monitor background jobs after all background stages have been dispatched
    if background_stages_seen:
        logger.info("monitor_background stages=%s", ",".join(background_stages_seen))
        try:
            monitor_report = monitor_background_jobs(config)
            tick_report["monitor"] = monitor_report
        except Exception as exc:
            logger.warning("monitor_background_failed error=%s", exc)
            tick_report["monitor"] = {"status": "error", "error": str(exc)}

    tick_report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()

    # Record the tick in PipelineState (same as daemon mode)
    state.record_run(tick_report)

    # Write Prefect task index for sqctl.sh / Grafana integration
    _update_task_index(tick_report)

    # Export Prefect metrics to InfluxDB for Grafana visualization
    try:
        from soloquant_prefect_influx_export import export_prefect_tick_to_influx
        from soloquant_prefect_config import get_influx_config
        influx_config = get_influx_config(config)
        influx_result = export_prefect_tick_to_influx(tick_report, influx_config)
        tick_report["influx_export"] = influx_result
        logger.info("influx_export written=%s", influx_result.get("written", 0))
    except Exception as exc:
        logger.warning("influx_export_failed error=%s", exc)
        tick_report["influx_export"] = {"status": "error", "error": str(exc)}

    logger.info(
        "tick_complete status=%s stages=%d degraded=%s",
        tick_report.get("status"),
        len(tick_report.get("stages", [])),
        tick_report.get("degraded_stages", []),
    )
    return tick_report


def _update_task_index(report: dict[str, Any]) -> None:
    """Write a sidecar index file for external tools (sqctl.sh, Grafana)."""
    index_path = RESULTS_DIR / "prefect-task-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    index = {
        "last_tick_finished_at_utc": report.get("finished_at_utc"),
        "last_tick_status": report.get("status"),
        "stages": {},
    }

    for stage_row in report.get("stages", []):
        stage_name = stage_row.get("stage", "unknown")
        index["stages"][stage_name] = {
            "status": stage_row.get("status", "unknown"),
            "started_at_utc": stage_row.get("started_at_utc"),
            "finished_at_utc": stage_row.get("finished_at_utc"),
        }

    # Preserve previous runs history (append to list, keep last 50)
    history_path = RESULTS_DIR / "prefect-tick-history.json"
    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text())
        except (json.JSONDecodeError, ValueError):
            history = []
    history.append(index)
    history = history[-50:]  # Keep last 50
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2))

    # Write current index
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))


def _deploy_flow(config_path: str | None = None) -> None:
    """Deploy the flow using Prefect serve() — self-hosted, no Docker/storage needed.

    Prefect 3.x serve() runs the flow inline with a schedule, perfect for
    single-machine self-hosted deployments. No Docker image or remote storage required.
    """
    config = load_config(config_path)
    pipeline_config = get_pipeline_config(config)
    poll_seconds = max(1, int(pipeline_config.get("poll-seconds", POLL_SECONDS)))

    print(f"Starting SoloQuant Prefect flow with {poll_seconds}s interval...")
    print(f"Prefect UI: {PREFECT_API_URL.replace('/api', '')}")
    print(f"Press Ctrl+C to stop.")

    soloquant_pipeline_tick.serve(
        name="soloquant-pipeline-scheduled",
        interval=poll_seconds,  # seconds between runs
        parameters={
            "config_path": str(config_path or ""),
            "mode": "work",
            "run_date": None,
            "force": False,
        },
        tags=["soloquant", "pipeline"],
    )
    print(f"Deployed 'soloquant-pipeline-scheduled' to work pool '{PREFECT_WORK_POOL}'")
    print(f"Schedule: every {poll_seconds} seconds")
    print(f"Prefect UI: {PREFECT_API_URL.replace('/api', '')}/deployments")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SoloQuant Prefect Pipeline Flow")
    parser.add_argument("--config", default=None, help="Path to config-soloquant.json")
    parser.add_argument("--mode", choices=["debug", "work"], default="work")
    parser.add_argument("--run-date", default=None)
    parser.add_argument("--force", action="store_true", help="Force run, ignoring interval gate")
    parser.add_argument("--deploy", action="store_true", help="Deploy as Prefect scheduled deployment")
    parser.add_argument("--run-once", action="store_true", help="Run one tick via Prefect and exit")
    return parser


def main() -> int:
    os.environ.setdefault("PREFECT_API_URL", PREFECT_API_URL)

    args = build_parser().parse_args()

    if args.deploy:
        _deploy_flow(args.config)
        # serve() blocks, so this line is never reached
        return 0

    if args.run_once:
        report = soloquant_pipeline_tick(
            config_path=args.config,
            mode=args.mode,
            run_date=args.run_date,
            force=args.force,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("status") == "error" else 0

    # Default: run as Prefect serve (scheduled, self-hosted)
    _deploy_flow(args.config)


if __name__ == "__main__":
    raise SystemExit(main())