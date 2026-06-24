#!/usr/bin/env python3
"""
Strategy Control API — FastAPI server for Grafana dashboard strategy control.

Provides REST endpoints for:
- Strategy status discovery (process table is truth, same as sqctl.sh)
- Individual strategy start/stop/restart
- Group-level operations (delegates to sqctl.sh)

Runs on 0.0.0.0:5000, accessible from Grafana via Docker gateway 172.20.0.1.
"""

import os
import sys
import json
import time
import signal
import asyncio
import logging
import secrets
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Configuration ────────────────────────────────────────────────────────────

LEAN_DIR = Path(os.environ.get("LEAN_DIR", "/home/project/hope/Lean"))
SOLOQUANT_DIR = LEAN_DIR / "Results" / "soloquant"
LIVEPAPER_DIR = SOLOQUANT_DIR / "live-paper-processes"
LLM_BG_DIR = SOLOQUANT_DIR / "llm-background"
CONFIG_DIR = LEAN_DIR / "Launcher" / "config"
LAUNCHER_DLL = LEAN_DIR / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher.dll"
DOTNET_BIN = "/usr/local/dotnet/dotnet"
SQCTL_SH = LEAN_DIR / "sqctl.sh"
CONDA_ENV = os.environ.get("CONDA_ENV", "/root/miniconda3/envs/quant311/bin/python3")
API_PORT = int(os.environ.get("STRATEGY_API_PORT", "5000"))
TOKEN_FILE = SOLOQUANT_DIR / ".strategy-api-token"
AUDIT_LOG = SOLOQUANT_DIR / "strategy-control-audit.log"
CONTROL_STATE_FILE = SOLOQUANT_DIR / "live-paper-control.json"

# Bridge name -> script mapping (mirrors sqctl.sh BRIDGE_PROCS)
BRIDGE_MAP = {
    "barra-cne5v2": "barra_cne5v2_live_bridge.py",
    "barra-cne5v3-2": "barra_cne5v3_2_live_bridge.py",
    "barra-cne5v4": "barra_cne5v4_live_bridge.py",
    "barra-cne5": "barra_cne5_live_bridge.py",
    "ashare-etf-t0": "ashare_etf_t0_feature_live_bridge.py",
    "ashare-multi-family": "ashare_multi_family_live_bridge.py",
    "ashare-llm-quant": "ashare_llm_quant_live_bridge.py",
    "ashare-industry-rotation": "ashare_industry_rotation_live_bridge.py",
    "ashare-etf-dual-rotation": "ashare_etf_dual_rotation_live_bridge.py",
}

# Legacy config base name -> bridge name mapping
CONFIG_TO_BRIDGE = {
    "barra-cne5v2": "barra-cne5v2",
    "barra-cne5v3-2": "barra-cne5v3-2",
    "barra-cne5v4": "barra-cne5v4",
    "barra-cne5": "barra-cne5",
    "ashare-etf-t0-feature": "ashare-etf-t0",
    "ashare-multi-family": "ashare-multi-family",
    "ashare-llm-quant": "ashare-llm-quant",
    "ashare-industry-rotation": "ashare-industry-rotation",
    "ashare-etf-dual-rotation": "ashare-etf-dual-rotation",
}

VALID_GROUPS = {"core", "prefect", "livepaper", "legacy", "bridge", "tushare", "llm", "all"}

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("strategy-control-api")

# ─── Auth ─────────────────────────────────────────────────────────────────────

def get_api_token() -> str:
    """Load or generate the API bearer token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)
    return token

API_TOKEN = get_api_token()

async def verify_token(authorization: Optional[str] = Header(None)):
    """Verify bearer token on mutating endpoints."""
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if authorization[7:] != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

# ─── Audit ────────────────────────────────────────────────────────────────────

def audit_log(action: str, target: str, result: str, detail: str = ""):
    """Append to audit log file."""
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} | {action} | {target} | {result} | {detail}\n"
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(line)
    except Exception as e:
        log.warning(f"Audit log write failed: {e}")

# ─── Process Helpers ──────────────────────────────────────────────────────────

def pid_alive(pid: int) -> bool:
    """Check if a PID is alive (not zombie)."""
    try:
        state_path = Path(f"/proc/{pid}/status")
        if state_path.exists():
            for line in state_path.read_text().splitlines():
                if line.startswith("State:"):
                    state = line.split()[1]
                    if state == "Z":
                        return False
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError, FileNotFoundError):
        pass
    return False

# ─── Manual Control State ─────────────────────────────────────────────────────

def _load_control_state() -> dict:
    """Load the sticky manual-control state.

    Returns {} when the file is missing/corrupt (treat as no holds → backward compatible).
    """
    try:
        if CONTROL_STATE_FILE.exists():
            data = json.loads(CONTROL_STATE_FILE.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        log.warning(f"control state load failed ({CONTROL_STATE_FILE}): {e}")
    return {}


def _save_control_state(strategy_id: str, action: str, actor: str = "grafana") -> None:
    """Record a sticky manual-control action.

    action == "stop"  → manual_hold=True  (orchestrator must not auto-restart)
    action == "start" → manual_hold=False (release hold)
    Never raises — a write failure must not block the underlying start/stop of the process.
    """
    try:
        state = _load_control_state()
        state[strategy_id] = {
            "manual_hold": action == "stop",
            "last_action": action,
            "last_action_at": datetime.now(timezone.utc).isoformat(),
            "last_actor": actor,
        }
        CONTROL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONTROL_STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        tmp.replace(CONTROL_STATE_FILE)
    except Exception as e:
        log.warning(f"control state save failed ({strategy_id}={action}): {e}")

def get_uptime_seconds(pid: int) -> Optional[int]:
    """Get process uptime in seconds."""
    try:
        result = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except Exception:
        pass
    return None

# ─── Process Resources (for GET /api/strategies) ──────────────────────────────

_proc_cpu_prev: dict = {}  # pid -> (utime+stime jiffies, monotonic seconds)
try:
    _CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
except (ValueError, AttributeError, OSError):
    _CLK_TCK = 100

def get_cpu_percent(pid: int) -> Optional[float]:
    """Instantaneous CPU% from two /proc/<pid>/stat samples.

    Returns None on the first sample (no baseline) or any read failure.
    Module-level cache holds the previous (jiffies, ts) per pid.
    """
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        utime = int(fields[13])
        stime = int(fields[14])
        total = utime + stime
        now = time.monotonic()
        prev = _proc_cpu_prev.get(pid)
        _proc_cpu_prev[pid] = (total, now)
        if prev is None:
            return None
        dticks = total - prev[0]
        dtime = now - prev[1]
        if dtime <= 0:
            return None
        return round((dticks / _CLK_TCK) / dtime * 100.0, 2)
    except Exception:
        return None

def get_rss_mb(pid: int) -> Optional[int]:
    """Resident set size in MB from /proc/<pid>/status VmRSS (kB)."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:
        return None
    return None

def _resources_for(pid: Optional[int]) -> tuple:
    """(cpu_percent, rss_mb) for a pid, (None, None) if pid falsy."""
    if not pid:
        return None, None
    return get_cpu_percent(pid), get_rss_mb(pid)

def find_pids_matching(pattern: str) -> list:
    """Find all PIDs matching a pattern (like pgrep -f)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except Exception:
        pass
    return []

def find_pid_matching(pattern: str) -> Optional[int]:
    """Find first PID matching a pattern."""
    pids = find_pids_matching(pattern)
    return pids[0] if pids else None

def get_lean_config_of_pid(pid: int) -> Optional[str]:
    """Extract --config path from a running LEAN process."""
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            import re
            m = re.search(r"--config\s+(\S+)", cmdline)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None

def get_proc_comm(pid: int) -> str:
    """Get /proc/pid/comm (process name)."""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except Exception:
        return ""

def discover_lean_processes() -> list:
    """Discover all running LEAN dotnet processes. Returns list of {pid, config, classification}."""
    results = []
    for pid in find_pids_matching(f"dotnet.*{LAUNCHER_DLL.name}"):
        if get_proc_comm(pid) != "dotnet":
            continue  # Skip bash wrappers
        config = get_lean_config_of_pid(pid)
        if config and "/soloquant/strategies/" in config:
            cls = "soloquant"
        elif config and "/Launcher/config/" in config:
            cls = "legacy"
        else:
            cls = "unknown"
        results.append({"pid": pid, "config": config, "classification": cls})
    return results

def graceful_stop(pid: int, name: str, timeout: int = 20) -> bool:
    """Gracefully stop a process: SIGTERM then SIGKILL after timeout."""
    if not pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True

    waited = 0
    while pid_alive(pid) and waited < timeout:
        time.sleep(1)
        waited += 1

    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
        except ProcessLookupError:
            pass

    return not pid_alive(pid)

def read_algorithm_type_name(config_path: str) -> Optional[str]:
    """Read algorithm-type-name from a LEAN config JSON file."""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg.get("algorithm-type-name")
    except Exception:
        return None

# ─── Strategy Registry ────────────────────────────────────────────────────────

class StrategyInfo(BaseModel):
    strategy_id: str
    algorithm_id: Optional[str] = None
    group: str  # "soloquant-lp", "legacy-lp", "bridge", "core"
    status: str  # "running", "stopped"
    pid: Optional[int] = None
    uptime_seconds: Optional[int] = None
    config_path: Optional[str] = None
    bridge: Optional[str] = None
    started_at_utc: Optional[str] = None
    cpu_percent: Optional[float] = None
    rss_mb: Optional[int] = None
    manual_hold: bool = False

class StrategiesResponse(BaseModel):
    strategies: list
    timestamp: str

class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    strategies_total: int
    strategies_running: int

class ActionResponse(BaseModel):
    success: bool
    message: str
    strategy_id: str
    action: str

class GroupResponse(BaseModel):
    success: bool
    message: str
    group: str
    action: str

# Cache
_strategy_cache: list = []
_cache_timestamp: float = 0.0
_CACHE_TTL = 5.0  # seconds

def _build_strategy_registry() -> list:
    """Build the full strategy registry by scanning PID files, configs, and process table."""
    strategies = []
    lean_procs = discover_lean_processes()
    running_configs = {p["config"]: p["pid"] for p in lean_procs if p["config"]}
    control_state = _load_control_state()

    # -- SoloQuant live-paper strategies --
    if LIVEPAPER_DIR.exists():
        for pidfile in sorted(LIVEPAPER_DIR.glob("*.pid.json")):
            try:
                with open(pidfile) as f:
                    info = json.load(f)
            except Exception:
                continue

            sid = info.get("strategy_id", pidfile.stem)
            pid = info.get("pid", 0)
            config_path = info.get("live-paper-config", "")
            started_at = info.get("started_at_utc", "")

            # Determine algorithm_id from config
            alg_id = None
            if config_path and Path(config_path).exists():
                alg_id = read_algorithm_type_name(config_path)

            # Check if actually running (process table is truth)
            alive = pid_alive(pid) if pid else False
            actual_pid = pid if alive else None
            uptime = get_uptime_seconds(pid) if alive else None

            # Also check if a different PID is running with this config
            if not alive and config_path in running_configs:
                actual_pid = running_configs[config_path]
                alive = True
                uptime = get_uptime_seconds(actual_pid)

            cpu, rss = _resources_for(actual_pid)
            strategies.append(StrategyInfo(
                strategy_id=sid,
                algorithm_id=alg_id or sid,
                group="soloquant-lp",
                status="running" if alive else "stopped",
                pid=actual_pid,
                uptime_seconds=uptime,
                config_path=config_path,
                started_at_utc=started_at,
                cpu_percent=cpu,
                rss_mb=rss,
                manual_hold=bool(control_state.get(sid, {}).get("manual_hold", False)),
            ))

    # -- Legacy live-paper strategies --
    for config_file in sorted(CONFIG_DIR.glob("config-*live-paper*.json")):
        # Skip smoke configs
        if "smoke" in config_file.name:
            continue

        config_path = str(config_file)
        base_name = config_file.stem.replace("config-", "").replace("-live-paper", "")
        alg_id = read_algorithm_type_name(config_path)

        # Find if this config is running
        running_pid = None
        for p in lean_procs:
            if p["config"] == config_path and pid_alive(p["pid"]):
                running_pid = p["pid"]
                break

        # Also check for multiple instances
        if running_pid is None and config_path in running_configs:
            running_pid = running_configs[config_path]

        alive = running_pid is not None and pid_alive(running_pid)
        uptime = get_uptime_seconds(running_pid) if alive else None

        # Find matching bridge
        bridge_name = CONFIG_TO_BRIDGE.get(base_name)

        cpu, rss = _resources_for(running_pid)
        strategies.append(StrategyInfo(
            strategy_id=alg_id or base_name,
            algorithm_id=alg_id or base_name,
            group="legacy-lp",
            status="running" if alive else "stopped",
            pid=running_pid if alive else None,
            uptime_seconds=uptime,
            config_path=config_path,
            bridge=bridge_name,
            cpu_percent=cpu,
            rss_mb=rss,
            manual_hold=bool(control_state.get(alg_id or base_name, {}).get("manual_hold", False)),
        ))

    # -- Bridge processes --
    for bname, bscript in BRIDGE_MAP.items():
        pid = find_pid_matching(bscript)
        alive = pid is not None and pid_alive(pid)
        uptime = get_uptime_seconds(pid) if alive else None

        cpu, rss = _resources_for(pid if alive else None)
        strategies.append(StrategyInfo(
            strategy_id=f"bridge:{bname}",
            algorithm_id=None,
            group="bridge",
            status="running" if alive else "stopped",
            pid=pid if alive else None,
            uptime_seconds=uptime,
            cpu_percent=cpu,
            rss_mb=rss,
        ))

    # -- Core processes --
    core_procs = {
        "pipeline-runner": "soloquant_pipeline_runner.py.*--daemon",
        "crawl-scheduler": "soloquant_crawl_scheduler.py.*--daemon",
    }
    for cname, cpattern in core_procs.items():
        pid = find_pid_matching(cpattern)
        alive = pid is not None and pid_alive(pid)
        uptime = get_uptime_seconds(pid) if alive else None

        cpu, rss = _resources_for(pid if alive else None)
        strategies.append(StrategyInfo(
            strategy_id=f"core:{cname}",
            algorithm_id=None,
            group="core",
            status="running" if alive else "stopped",
            pid=pid if alive else None,
            uptime_seconds=uptime,
            cpu_percent=cpu,
            rss_mb=rss,
        ))

    return strategies

def get_strategies() -> list:
    """Get strategies, using cache if fresh."""
    global _strategy_cache, _cache_timestamp
    now = time.time()
    if now - _cache_timestamp > _CACHE_TTL:
        _strategy_cache = _build_strategy_registry()
        _cache_timestamp = now
    return _strategy_cache

def invalidate_cache():
    """Force cache refresh on next access."""
    global _cache_timestamp
    _cache_timestamp = 0.0

# ─── Individual Strategy Actions ──────────────────────────────────────────────

def start_strategy_by_config(config_path: str, cwd: str = None):
    """Start a LEAN process with the given config. Returns (success, message, new_pid)."""
    if not Path(config_path).exists():
        return False, f"Config not found: {config_path}", None

    work_dir = cwd or str(LEAN_DIR / "Launcher" / "bin" / "Debug")
    try:
        proc = subprocess.Popen(
            [DOTNET_BIN, str(LAUNCHER_DLL), "--config", config_path],
            cwd=work_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Give it a moment to start
        time.sleep(2)
        if pid_alive(proc.pid):
            return True, f"Started PID {proc.pid}", proc.pid
        else:
            return False, "Process exited immediately", None
    except Exception as e:
        return False, str(e), None

def start_bridge_by_name(bridge_name: str):
    """Start a bridge process by name. Returns (success, message)."""
    script = BRIDGE_MAP.get(bridge_name)
    if not script:
        return False, f"Unknown bridge: {bridge_name}"

    script_path = LEAN_DIR / "Scripts" / script
    if not script_path.exists():
        return False, f"Bridge script not found: {script_path}"

    try:
        proc = subprocess.Popen(
            [CONDA_ENV, str(script_path), "--interval", "60"],
            cwd=str(LEAN_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1)
        if pid_alive(proc.pid):
            return True, f"Bridge {bridge_name} started (PID {proc.pid})"
        else:
            return False, f"Bridge {bridge_name} exited immediately"
    except Exception as e:
        return False, str(e)

def stop_bridge_by_name(bridge_name: str):
    """Stop a bridge process by name. Returns (success, message)."""
    script = BRIDGE_MAP.get(bridge_name)
    if not script:
        return False, f"Unknown bridge: {bridge_name}"

    pids = find_pids_matching(script)
    if not pids:
        return True, f"Bridge {bridge_name} not running"

    for pid in pids:
        graceful_stop(pid, f"bridge:{bridge_name}", timeout=10)

    return True, f"Bridge {bridge_name} stopped"

# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Strategy Control API",
    description="REST API for controlling SoloQuant strategies from Grafana dashboards",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://84.8.248.198:3000", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

START_TIME = time.time()

@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    strategies = get_strategies()
    return HealthResponse(
        status="ok",
        uptime_seconds=round(time.time() - START_TIME, 1),
        strategies_total=len(strategies),
        strategies_running=sum(1 for s in strategies if s.status == "running"),
    )

@app.get("/api/strategies", response_model=StrategiesResponse)
async def list_strategies(group: Optional[str] = Query(None, description="Filter by group")):
    """List all strategies with current status."""
    strategies = get_strategies()
    if group:
        strategies = [s for s in strategies if s.group == group]
    return StrategiesResponse(
        strategies=[s.dict() for s in strategies],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

@app.get("/api/strategies/by-algorithm/{algorithm_id}")
async def get_strategy_by_algorithm(algorithm_id: str):
    """Lookup strategy by algorithm_id (for Grafana template variable $algorithm_id)."""
    strategies = get_strategies()
    matches = [s for s in strategies if s.algorithm_id == algorithm_id]
    if not matches:
        # Try partial match
        matches = [s for s in strategies if s.algorithm_id and algorithm_id in s.algorithm_id]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No strategy found for algorithm_id: {algorithm_id}")
    return matches[0].dict()

@app.post("/api/strategies/{strategy_id}/start", response_model=ActionResponse)
async def start_strategy(strategy_id: str, _=Depends(verify_token)):
    """Start a stopped strategy."""
    invalidate_cache()
    strategies = get_strategies()
    target = next((s for s in strategies if s.strategy_id == strategy_id), None)

    if not target:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    if target.status == "running":
        return ActionResponse(success=True, message="Already running", strategy_id=strategy_id, action="start")

    # Start the LEAN process
    config_path = target.config_path
    if not config_path:
        raise HTTPException(status_code=400, detail=f"No config path for strategy: {strategy_id}")

    success, msg, new_pid = start_strategy_by_config(config_path)
    audit_log("start", strategy_id, "success" if success else "failed", msg)
    if success:
        _save_control_state(strategy_id, "start")

    # Also start matching bridge
    if target.bridge:
        b_ok, b_msg = start_bridge_by_name(target.bridge)
        audit_log("start", f"bridge:{target.bridge}", "success" if b_ok else "failed", b_msg)

    invalidate_cache()
    return ActionResponse(success=success, message=msg, strategy_id=strategy_id, action="start")

@app.post("/api/strategies/{strategy_id}/stop", response_model=ActionResponse)
async def stop_strategy(strategy_id: str, _=Depends(verify_token)):
    """Stop a running strategy."""
    invalidate_cache()
    strategies = get_strategies()
    target = next((s for s in strategies if s.strategy_id == strategy_id), None)

    if not target:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    if target.status == "stopped":
        return ActionResponse(success=True, message="Already stopped", strategy_id=strategy_id, action="stop")

    if not target.pid:
        raise HTTPException(status_code=400, detail=f"No PID for strategy: {strategy_id}")

    # Stop the LEAN process
    ok = graceful_stop(target.pid, strategy_id, timeout=20)
    msg = f"Stopped PID {target.pid}" if ok else f"Failed to stop PID {target.pid}"
    audit_log("stop", strategy_id, "success" if ok else "failed", msg)
    _save_control_state(strategy_id, "stop")

    # Also stop matching bridge
    if target.bridge:
        b_ok, b_msg = stop_bridge_by_name(target.bridge)
        audit_log("stop", f"bridge:{target.bridge}", "success" if b_ok else "failed", b_msg)

    invalidate_cache()
    return ActionResponse(success=ok, message=msg, strategy_id=strategy_id, action="stop")

@app.post("/api/strategies/{strategy_id}/restart", response_model=ActionResponse)
async def restart_strategy(strategy_id: str, _=Depends(verify_token)):
    """Restart a strategy (stop then start)."""
    invalidate_cache()
    strategies = get_strategies()
    target = next((s for s in strategies if s.strategy_id == strategy_id), None)

    if not target:
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    # Stop if running
    if target.status == "running" and target.pid:
        graceful_stop(target.pid, strategy_id, timeout=20)
        if target.bridge:
            stop_bridge_by_name(target.bridge)
        time.sleep(2)

    # Start
    config_path = target.config_path
    if not config_path:
        raise HTTPException(status_code=400, detail=f"No config path for strategy: {strategy_id}")

    success, msg, new_pid = start_strategy_by_config(config_path)
    audit_log("restart", strategy_id, "success" if success else "failed", msg)
    if success:
        _save_control_state(strategy_id, "start")

    if target.bridge:
        b_ok, b_msg = start_bridge_by_name(target.bridge)
        audit_log("restart", f"bridge:{target.bridge}", "success" if b_ok else "failed", b_msg)

    invalidate_cache()
    return ActionResponse(success=success, message=msg, strategy_id=strategy_id, action="restart")

@app.post("/api/groups/{group}/start", response_model=GroupResponse)
async def group_start(group: str, _=Depends(verify_token)):
    """Start all strategies in a group (delegates to sqctl.sh)."""
    if group not in VALID_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid group: {group}. Valid: {VALID_GROUPS}")

    try:
        result = subprocess.run(
            [str(SQCTL_SH), "start", group],
            capture_output=True, text=True, timeout=120
        )
        msg = result.stdout.strip() or result.stderr.strip() or "Done"
        success = result.returncode == 0
    except Exception as e:
        msg = str(e)
        success = False

    audit_log("group-start", group, "success" if success else "failed", msg[:200])
    invalidate_cache()
    return GroupResponse(success=success, message=msg, group=group, action="start")

@app.post("/api/groups/{group}/stop", response_model=GroupResponse)
async def group_stop(group: str, _=Depends(verify_token)):
    """Stop all strategies in a group (delegates to sqctl.sh)."""
    if group not in VALID_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid group: {group}. Valid: {VALID_GROUPS}")

    try:
        result = subprocess.run(
            [str(SQCTL_SH), "stop", group],
            capture_output=True, text=True, timeout=120
        )
        msg = result.stdout.strip() or result.stderr.strip() or "Done"
        success = result.returncode == 0
    except Exception as e:
        msg = str(e)
        success = False

    audit_log("group-stop", group, "success" if success else "failed", msg[:200])
    invalidate_cache()
    return GroupResponse(success=success, message=msg, group=group, action="stop")

@app.post("/api/groups/{group}/restart", response_model=GroupResponse)
async def group_restart(group: str, _=Depends(verify_token)):
    """Restart all strategies in a group (delegates to sqctl.sh)."""
    if group not in VALID_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid group: {group}. Valid: {VALID_GROUPS}")

    try:
        result = subprocess.run(
            [str(SQCTL_SH), "restart", group],
            capture_output=True, text=True, timeout=180
        )
        msg = result.stdout.strip() or result.stderr.strip() or "Done"
        success = result.returncode == 0
    except Exception as e:
        msg = str(e)
        success = False

    audit_log("group-restart", group, "success" if success else "failed", msg[:200])
    invalidate_cache()
    return GroupResponse(success=success, message=msg, group=group, action="restart")

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT, workers=1)
