"""
SoloQuant Prefect Configuration
Centralized constants for Prefect server, work pool, and paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ─── Prefect Server ──────────────────────────────────────────────────────────

PREFECT_SERVER_HOST = "0.0.0.0"
PREFECT_SERVER_PORT = 4200
# Internal API URL (used by worker/flow on same machine)
PREFECT_API_URL = f"http://localhost:{PREFECT_SERVER_PORT}/api"
# External API URL (used by browser UI to reach the server)
PREFECT_UI_API_URL = "http://84.8.248.198:4200/api"
PREFECT_WORK_POOL = "soloquant-pool"

# ─── Paths ───────────────────────────────────────────────────────────────────

LEAN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = LEAN_ROOT / "Scripts"
RESULTS_DIR = LEAN_ROOT / "Results" / "soloquant"

PREFECT_TASK_INDEX_PATH = RESULTS_DIR / "prefect-task-index.json"
PREFECT_SERVER_LOG = RESULTS_DIR / "prefect-server.log"
PREFECT_WORKER_LOG = RESULTS_DIR / "prefect-worker.log"

# ─── Pipeline Configuration ─────────────────────────────────────────────────

POLL_SECONDS = 300  # Default pipeline tick interval

# Stage names in execution order
PIPELINE_STAGES = (
    "ingest_local_strategies",
    "watch_local_src",
    "data_driven_crawl"
    "crawl_research",
    "crawl_api_sources",
    "prepare_reproduction",
    "prepare_iv_data",
    "build_event_graph",
    "build_event_signals",
    "reproduce_one",
    "materialize_variants",
    "optimize_backtests",
    "prepare_live_market_data",
    "update_lifecycle",
    "run_live_paper",
    "export_influx",
)

# Stages that launch background Popen processes (sentinel pattern in Prefect)
LLM_BACKGROUND_STAGES = frozenset({
    "crawl_research",
    "prepare_reproduction",
    "reproduce_one",
    "optimize_backtests",
})

# ─── Config Loading ──────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = LEAN_ROOT / "Launcher" / "config" / "config-soloquant.json"


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load SoloQuant config JSON."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(path) as f:
        return json.load(f)


def get_prefect_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract Prefect-specific config section."""
    if config is None:
        config = load_config()
    return config.get("prefect", {})


def get_pipeline_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract pipeline config section."""
    if config is None:
        config = load_config()
    return config.get("pipeline", {})


def get_influx_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract InfluxDB config section."""
    if config is None:
        config = load_config()
    return config.get("influxdb", {})
