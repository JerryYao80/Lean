#!/usr/bin/env python3
"""
SoloQuant Prefect Tasks — Phase 2: Individual @task for each pipeline stage.

Each task wraps the corresponding stage logic from soloquant_pipeline_runner.py,
adding Prefect-native retries, timeouts, and state tracking while preserving
backward compatibility with PID files and soloquant-pipeline-state.json.

Task categories:
  A: Finite synchronous stages — standard Prefect tasks
  B: LLM background stages — sentinel tasks (launch subprocess, return immediately)
  C: Live-paper management — sentinel tasks (start LEAN, return immediately)
  D: Data preparation — library call tasks
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from prefect import task, get_run_logger

import soloquant_orchestrator as orchestrator
from soloquant_pipeline_runner import (
    PipelineState,
    PipelineLogger,
    DefaultPipelineServices,
    default_state_path,
    default_log_path,
    run_orchestrator_command,
    _is_background_process_running,
    _cleanup_finished_background,
    _launch_background_command,
    _build_subprocess_env,
    process_alive,
    LLM_BACKGROUND_STAGES,
)

from soloquant_prefect_config import (
    RESULTS_DIR,
    load_config,
    get_pipeline_config,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_state(config: dict) -> PipelineState:
    return PipelineState(default_state_path(config))


def _should_run(stage_name: str, config: dict, interval_key: str, default_interval: int) -> bool:
    """Check interval-based skip logic using PipelineState."""
    pipeline_config = get_pipeline_config(config)
    interval = max(1, orchestrator.safe_int(pipeline_config.get(interval_key), default_interval))
    state = _get_state(config)
    return state.should_run(stage_name, interval)


def _mark_finished(stage_name: str, config: dict, report: dict) -> None:
    """Record stage completion in PipelineState."""
    state = _get_state(config)
    state.mark_finished(stage_name, report=report)


# ═══════════════════════════════════════════════════════════════════════════════
# Category A: Finite Synchronous Stages
# ═══════════════════════════════════════════════════════════════════════════════

@task(name="ingest-local-strategies", retries=1, retry_delay_seconds=60, timeout_seconds=300)
def ingest_local_strategies(config: dict, run_date: str | None = None) -> dict:
    """Interval: 1h. Ingest local strategy files."""
    logger = get_run_logger()
    if not _should_run("ingest_local_strategies", config, "local-ingest-interval-seconds", 3600):
        return {"status": "skipped", "reason": "interval_not_elapsed"}
    logger.info("stage_start stage=ingest_local_strategies")
    services = DefaultPipelineServices(mode="work")
    report = services.ingest_local_strategies(config, run_date=run_date)
    if report.get("status") != "skipped":
        _mark_finished("ingest_local_strategies", config, report)
    return report


@task(name="watch-local-src", retries=0, timeout_seconds=60)
def watch_local_src(config: dict) -> dict:
    """Interval: 5m. Scan local-src/ for new files, score and index them."""
    logger = get_run_logger()
    if not _should_run("watch_local_src", config, "local-src-interval-seconds", 300):
        return {"status": "skipped", "reason": "interval_not_elapsed"}
    logger.info("stage_start stage=watch_local_src")
    from soloquant_local_src_watcher import watch_local_src as _watch
    report = _watch(config)
    _mark_finished("watch_local_src", config, report)
    return report


@task(name="crawl-api-sources", retries=1, retry_delay_seconds=120, timeout_seconds=600)
def crawl_api_sources(config: dict) -> dict:
    """Interval: 4h. Crawl direct API sources (arXiv, Fed RSS, FRED, etc.)."""
    logger = get_run_logger()
    if not _should_run("crawl_api_sources", config, "api-crawl-interval-seconds", 14400):
        return {"status": "skipped", "reason": "interval_not_elapsed"}
    logger.info("stage_start stage=crawl_api_sources")
    from soloquant_api_crawl_tasks import crawl_all_api_sources
    report = crawl_all_api_sources(config)
    _mark_finished("crawl_api_sources", config, report)
    return report


@task(name="prepare-iv-data", retries=1, retry_delay_seconds=60, timeout_seconds=900)
def prepare_iv_data(config: dict, run_date: str | None = None) -> dict:
    """Prepare implied volatility data from Tushare."""
    logger = get_run_logger()
    logger.info("stage_start stage=prepare_iv_data")
    services = DefaultPipelineServices(mode="work")
    report = services.prepare_iv_data(config, run_date=run_date)
    _mark_finished("prepare_iv_data", config, report)
    return report


@task(name="build-event-graph", retries=1, retry_delay_seconds=60, timeout_seconds=600)
def build_event_graph(config: dict, run_date: str | None = None) -> dict:
    """Build finance event graph from crawled intelligence."""
    logger = get_run_logger()
    logger.info("stage_start stage=build_event_graph")
    report = run_orchestrator_command(["--build-finance-event-graph"], config)
    _mark_finished("build_event_graph", config, report)
    return report


@task(name="build-event-signals", retries=1, retry_delay_seconds=60, timeout_seconds=600)
def build_event_signals(config: dict, run_date: str | None = None) -> dict:
    """Build event signal context from event graph."""
    logger = get_run_logger()
    logger.info("stage_start stage=build_event_signals")
    report = run_orchestrator_command(["--build-event-signal-context"], config)
    _mark_finished("build_event_signals", config, report)
    return report


@task(name="materialize-variants", retries=1, retry_delay_seconds=60, timeout_seconds=900)
def materialize_variants(config: dict, run_date: str | None = None) -> dict:
    """Materialize generated strategy variants."""
    logger = get_run_logger()
    logger.info("stage_start stage=materialize_variants")
    report = run_orchestrator_command(["--materialize-generated-strategies"], config)
    _mark_finished("materialize_variants", config, report)
    return report


@task(name="export-influx", retries=1, retry_delay_seconds=60, timeout_seconds=600)
def export_influx(config: dict, run_date: str | None = None) -> dict:
    """Export all pipeline data to InfluxDB (5 sub-exports)."""
    logger = get_run_logger()
    logger.info("stage_start stage=export_influx")
    services = DefaultPipelineServices(mode="work")
    report = services.export_influx(config, run_date=run_date)
    _mark_finished("export_influx", config, report)
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Category B: LLM Background Stages (Sentinel — launch subprocess, return)
# ═══════════════════════════════════════════════════════════════════════════════

@task(name="data-driven-crawl", retries=0, timeout_seconds=60)
def data_driven_crawl(config: dict, run_date: str | None = None) -> dict:
    """Interval: 8h. Launch data-driven crawl scheduler as background process."""
    logger = get_run_logger()
    if not _should_run("data_driven_crawl", config, "data-driven-crawl-interval-seconds", 28800):
        return {"status": "skipped", "reason": "interval_not_elapsed"}

    if _is_background_process_running("data_driven_crawl", config):
        return {"status": "ok", "mode": "background", "action": "already_running"}

    _cleanup_finished_background("data_driven_crawl", config)
    logger.info("stage_start stage=data_driven_crawl mode=background")

    command = [
        sys.executable,
        str(orchestrator.repo_root() / "Scripts" / "soloquant_crawl_scheduler.py"),
        "--config", str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
        "--task", "data_driven_strategy", "--force", "--once",
    ]
    info = _launch_background_command(command, "data_driven_crawl", config)
    report = {"status": "ok", "mode": "background", "action": "launched", "pid": info.get("pid")}
    _mark_finished("data_driven_crawl", config, report)
    return report


@task(name="crawl-research", retries=0, timeout_seconds=60)
def crawl_research(config: dict, run_date: str | None = None) -> dict:
    """Interval: 8h. Launch research crawl scheduler as background process."""
    logger = get_run_logger()
    if not _should_run("crawl_research", config, "crawl-interval-seconds", 28800):
        return {"status": "skipped", "reason": "interval_not_elapsed"}

    if _is_background_process_running("crawl_research", config):
        return {"status": "ok", "mode": "background", "action": "already_running"}

    _cleanup_finished_background("crawl_research", config)
    logger.info("stage_start stage=crawl_research mode=background")

    command = [
        sys.executable,
        str(orchestrator.repo_root() / "Scripts" / "soloquant_crawl_scheduler.py"),
        "--config", str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
        "--task", "strategy", "--task", "finance_intelligence", "--force", "--once",
    ]
    info = _launch_background_command(command, "crawl_research", config)
    report = {"status": "ok", "mode": "background", "action": "launched", "pid": info.get("pid")}
    _mark_finished("crawl_research", config, report)
    return report


@task(name="prepare-reproduction", retries=0, timeout_seconds=60)
def prepare_reproduction(config: dict, run_date: str | None = None) -> dict:
    """Launch prepare-reproduction + analyze-finance-intelligence as background processes."""
    logger = get_run_logger()

    # Check if any background process from this stage is already running
    results = []
    for sub_stage in ["prepare_reproduction", "analyze_finance_intelligence"]:
        if _is_background_process_running(sub_stage, config):
            results.append({"stage": sub_stage, "status": "ok", "action": "already_running"})
            continue
        _cleanup_finished_background(sub_stage, config)
        logger.info("stage_start stage=%s mode=background", sub_stage)

        flag = "--prepare-reproduction" if sub_stage == "prepare_reproduction" else "--analyze-finance-intelligence"
        command = [
            sys.executable,
            str(orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"),
            "--config", str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
            flag,
        ]
        info = _launch_background_command(command, sub_stage, config)
        results.append({"stage": sub_stage, "status": "ok", "action": "launched", "pid": info.get("pid")})
        _mark_finished(sub_stage, config, results[-1])

    return {"status": "ok", "sub_stages": results}


@task(name="reproduce-one", retries=0, timeout_seconds=60)
def reproduce_one(config: dict, run_date: str | None = None) -> dict:
    """Interval: 30m. Launch LLM code generation + smoke test as background process."""
    logger = get_run_logger()
    if not _should_run("reproduce_one", config, "reproduce-interval-seconds", 1800):
        return {"status": "skipped", "reason": "interval_not_elapsed"}

    if _is_background_process_running("reproduce_one", config):
        return {"status": "ok", "mode": "background", "action": "already_running"}

    _cleanup_finished_background("reproduce_one", config)
    logger.info("stage_start stage=reproduce_one mode=background")

    command = [
        sys.executable,
        str(orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"),
        "--config", str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
        "--generate-strategy-implementations", "--max-items", "1", "--smoke-test-strategies",
    ]
    info = _launch_background_command(command, "reproduce_one", config)
    report = {"status": "ok", "mode": "background", "action": "launched", "pid": info.get("pid")}
    _mark_finished("reproduce_one", config, report)
    return report


@task(name="optimize-backtests", retries=0, timeout_seconds=60)
def optimize_backtests(config: dict, run_date: str | None = None) -> dict:
    """Interval: 30m. Launch strategy optimization as background process."""
    logger = get_run_logger()
    if not _should_run("optimize_backtests", config, "optimize-interval-seconds", 1800):
        return {"status": "skipped", "reason": "interval_not_elapsed"}

    if _is_background_process_running("optimize_backtests", config):
        return {"status": "ok", "mode": "background", "action": "already_running"}

    _cleanup_finished_background("optimize_backtests", config)
    logger.info("stage_start stage=optimize_backtests mode=background")

    command = [
        sys.executable,
        str(orchestrator.repo_root() / "Scripts" / "soloquant_orchestrator.py"),
        "--config", str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"),
        "--optimize-strategies", "--max-items", "1",
    ]
    info = _launch_background_command(command, "optimize_backtests", config)
    report = {"status": "ok", "mode": "background", "action": "launched", "pid": info.get("pid")}
    _mark_finished("optimize_backtests", config, report)
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Category C: Live-Paper & Lifecycle Management (Sentinel)
# ═══════════════════════════════════════════════════════════════════════════════

@task(name="prepare-live-market-data", retries=2, retry_delay_seconds=30, timeout_seconds=300)
def prepare_live_market_data(config: dict, now: datetime | None = None) -> dict:
    """Prepare shared market data snapshot for live-paper processes."""
    logger = get_run_logger()
    logger.info("stage_start stage=prepare_live_market_data")
    services = DefaultPipelineServices(mode="work")
    report = services.prepare_live_market_data(config, now=now)
    _mark_finished("prepare_live_market_data", config, report)
    return report


@task(name="update-lifecycle", retries=1, retry_delay_seconds=30, timeout_seconds=120)
def update_lifecycle(config: dict) -> dict:
    """Update strategy lifecycle (candidate/serving/retired) and export to InfluxDB."""
    logger = get_run_logger()
    logger.info("stage_start stage=update_lifecycle")
    services = DefaultPipelineServices(mode="work")
    report = services.update_lifecycle(config)
    _mark_finished("update_lifecycle", config, report)
    return report


@task(name="run-live-paper", retries=0, timeout_seconds=120)
def run_live_paper(config: dict) -> dict:
    """Ensure all registered live-paper strategies are running (sentinel).

    Starts LEAN dotnet processes that are not running, skips those that are.
    Does NOT wait for completion — LEAN processes run indefinitely.
    """
    logger = get_run_logger()
    logger.info("stage_start stage=run_live_paper mode=sentinel")
    services = DefaultPipelineServices(mode="work")
    report = services.run_live_paper(config)
    _mark_finished("run_live_paper", config, report)
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Monitor: Check background job status
# ═══════════════════════════════════════════════════════════════════════════════

@task(name="monitor-background-jobs", timeout_seconds=30, retries=0)
def monitor_background_jobs(config: dict) -> dict:
    """Check status of all LLM background jobs. Write summary."""
    logger = get_run_logger()
    bg_dir = Path(config.get("workflow-root", "Results/soloquant")) / "llm-background"
    summary = {}
    for pidfile in sorted(bg_dir.glob("*.pid.json")):
        stage = pidfile.stem
        info = _load_pid_json(pidfile)
        pid = orchestrator.safe_int(info.get("pid"), 0)
        alive = pid > 0 and process_alive(pid)
        summary[stage] = {"pid": pid, "alive": alive}
        logger.info("bg_job stage=%s pid=%d alive=%s", stage, pid, alive)
    return {"status": "ok", "jobs": summary}


def _load_pid_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
