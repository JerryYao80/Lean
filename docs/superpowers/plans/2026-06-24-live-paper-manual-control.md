# live-paper 手动启停控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sticky manual start/stop control for live-paper strategies so an operator (via Grafana buttons) can stop a strategy and the SoloQuant pipeline will *not* auto-restart it — without touching the existing 43-panel barra dashboard.

**Architecture:** A small sticky control-state file (`Results/soloquant/live-paper-control.json`, keyed by `strategy_id`, `manual_hold: bool`) is the single source of truth. The existing `strategy_control_api.py` stop/start endpoints write it; the pipeline launcher `start_registered_live_paper_strategies` reads it and skips held strategies; `GET /api/strategies` surfaces `manual_hold` + per-process `cpu_percent`/`rss_mb`. A volkovlabs-button-panel (panel #44, append-only) on the barra dashboard POSTs start/stop through the already-authenticated Infinity datasource (`strategy-control-api`, which carries `Authorization: Bearer <token>` via `secureJsonData`). All new behavior defaults to off → zero impact on existing flows until an operator acts.

**Tech Stack:** Python 3 / FastAPI / Pydantic (strategy_control_api.py), Python stdlib `/proc` readers, pytest (tests colocated in `Scripts/`), Grafana + volkovlabs-button-panel + yesoreyeram-infinity-datasource.

**Spec:** `docs/superpowers/specs/2026-06-24-live-paper-manual-control-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `Scripts/strategy_control_api.py` | Control API. Add control-state file helpers, wire stop/start/restart to write `manual_hold`, add resource + `manual_hold` fields to `GET /api/strategies`. | Modify |
| `Scripts/soloquant_pipeline_runner.py` | Live-paper launcher. Add `manual_hold` guard to `start_registered_live_paper_strategies`. | Modify |
| `monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json` | Barra dashboard (43 panels). Append ONE volkovlabs-button-panel (#44) at bottom; existing 43 panels byte-identical. | Modify (append-only) |
| `monitoring/grafana/install_strategy_control.sh` | Grafana deploy script. Add `grafana-cli plugins install volkovlabs-button-panel` step. | Modify (additive) |
| `Scripts/test_strategy_control_api.py` | API unit tests (control state, resource fields). | Create |
| `Scripts/test_live_paper_manual_hold.py` | Launcher guard unit tests. | Create |
| `Scripts/test_barra_dashboard_regression.py` | Regression test: 43 existing panels' `(title, gridPos, type)` unchanged. | Create |
| `Results/soloquant/live-paper-control.json` | Runtime sticky state. Created at first stop. | Created at runtime (not committed) |

**Conventions discovered (do not deviate):**
- Python tests are **colocated** with scripts: `Scripts/test_*.py` (see `Scripts/test_rt_daily_downloader.py`). Run with the `quant311` env: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_<name>.py -v`.
- `strategy_control_api.py` auth: `verify_token` is `Depends`-ed only on mutating endpoints. `GET` endpoints are open. The Infinity datasource (`monitoring/grafana/install_strategy_control.sh:62`) injects `secureJsonData.authCustomHeader: "Authorization: Bearer ${TOKEN}"` → **all** datasource-proxied requests (GET/POST) carry auth automatically.
- Control state file lives **sibling to the registry**: `Results/soloquant/live-paper-control.json` (= `Path(registry_path).parent / "live-paper-control.json"`). Keyed by the same `strategy_id` the start/stop endpoints receive.

---

## Task 1: Control-state read/write helpers (API)

Add a sticky control-state file reader/writer to `strategy_control_api.py`. Pure functions, fully unit-testable.

**Files:**
- Modify: `Scripts/strategy_control_api.py` (add constant after line 43 `AUDIT_LOG = ...`, add helper functions in the "Process Helpers" section after `pid_alive`, ~line 134)
- Test: `Scripts/test_strategy_control_api.py` (create)

- [ ] **Step 1: Write the failing test**

Create `Scripts/test_strategy_control_api.py`:

```python
"""Tests for live-paper manual control state (manual_hold stickiness)."""
import json
from pathlib import Path

import pytest

import strategy_control_api as api


@pytest.fixture
def control_file(tmp_path, monkeypatch):
    f = tmp_path / "live-paper-control.json"
    monkeypatch.setattr(api, "CONTROL_STATE_FILE", f)
    return f


def test_load_control_state_missing_file_returns_empty(control_file):
    assert api._load_control_state() == {}


def test_save_control_state_stop_sets_manual_hold_true(control_file):
    api._save_control_state("alpha-123", "stop", actor="grafana")
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is True
    assert state["alpha-123"]["last_action"] == "stop"
    assert state["alpha-123"]["last_actor"] == "grafana"
    assert "last_action_at" in state["alpha-123"]


def test_save_control_state_start_sets_manual_hold_false(control_file):
    api._save_control_state("alpha-123", "stop")
    api._save_control_state("alpha-123", "start")
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is False
    assert state["alpha-123"]["last_action"] == "start"


def test_save_control_state_preserves_other_strategies(control_file):
    api._save_control_state("alpha-123", "stop")
    api._save_control_state("beta-456", "start")
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is True
    assert state["beta-456"]["manual_hold"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: FAIL / collection error — `AttributeError: module 'strategy_control_api' has no attribute 'CONTROL_STATE_FILE'` (or `_load_control_state`).

- [ ] **Step 3: Add the constant and helpers**

In `Scripts/strategy_control_api.py`, after line 43 (`AUDIT_LOG = SOLOQUANT_DIR / "strategy-control-audit.log"`), add:

```python
CONTROL_STATE_FILE = SOLOQUANT_DIR / "live-paper-control.json"
```

In the "Process Helpers" section, after `pid_alive` (after line 134), add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add Scripts/strategy_control_api.py Scripts/test_strategy_control_api.py
git commit -m "feat(live-paper): add sticky manual_hold control-state helpers

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Wire manual_hold into stop/start/restart endpoints

Make stop set `manual_hold=true`, start/restart clear it. Non-blocking (write failures only log).

**Files:**
- Modify: `Scripts/strategy_control_api.py` — `start_strategy` (lines 528-555), `stop_strategy` (557-584), `restart_strategy` (586-616)
- Test: `Scripts/test_strategy_control_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `Scripts/test_strategy_control_api.py`:

```python
from fastapi.testclient import TestClient


@pytest.fixture
def client(control_file, monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    return TestClient(api.app)


def _stub_registry(monkeypatch, strategy_id, status="running", pid=99999, config_path="/tmp/cfg.json"):
    """Make get_strategies return one synthetic strategy without touching the process table."""
    info = api.StrategyInfo(
        strategy_id=strategy_id, algorithm_id=strategy_id, group="soloquant-lp",
        status=status, pid=pid, config_path=config_path,
    )
    monkeypatch.setattr(api, "get_strategies", lambda: [info])
    # Avoid real process start/stop in unit tests
    monkeypatch.setattr(api, "start_strategy_by_config", lambda cp: (True, "ok", 12345))
    monkeypatch.setattr(api, "graceful_stop", lambda p, n, timeout=20: True)
    return info


def test_stop_sets_manual_hold(client, control_file, monkeypatch):
    _stub_registry(monkeypatch, "alpha-123", status="running", pid=99999)
    r = client.post("/api/strategies/alpha-123/stop", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is True


def test_start_clears_manual_hold(client, control_file, monkeypatch):
    # Pre-seed a hold, then start should clear it.
    api._save_control_state("alpha-123", "stop")
    _stub_registry(monkeypatch, "alpha-123", status="stopped", pid=None)
    r = client.post("/api/strategies/alpha-123/start", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is False


def test_restart_clears_manual_hold(client, control_file, monkeypatch):
    api._save_control_state("alpha-123", "stop")
    _stub_registry(monkeypatch, "alpha-123", status="running", pid=99999)
    r = client.post("/api/strategies/alpha-123/restart", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: the 3 new tests FAIL — control file not written by the endpoints (assertion error on `control_file.read_text()`).

- [ ] **Step 3: Wire the endpoints**

In `stop_strategy`, immediately after the `audit_log("stop", strategy_id, "success" if ok else "failed", msg)` line (currently line 576) and before the `# Also stop matching bridge` comment, add:

```python
    _save_control_state(strategy_id, "stop")
```

In `start_strategy`, after `success, msg, new_pid = start_strategy_by_config(config_path)` and its `audit_log(...)` (currently line 547), add:

```python
    if success:
        _save_control_state(strategy_id, "start")
```

In `restart_strategy`, after `success, msg, new_pid = start_strategy_by_config(config_path)` and its `audit_log("restart", ...)` (currently line 609), add the same block:

```python
    if success:
        _save_control_state(strategy_id, "start")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add Scripts/strategy_control_api.py Scripts/test_strategy_control_api.py
git commit -m "feat(live-paper): stop sets manual_hold, start/restart clear it

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Resource fields + manual_hold in GET /api/strategies

Add `cpu_percent`, `rss_mb`, `manual_hold` to `StrategyInfo` and populate them. `cpu_percent` uses a cached two-sample `/proc/<pid>/stat` read (true instantaneous %); `rss_mb` reads `VmRSS` from `/proc/<pid>/status`.

**Files:**
- Modify: `Scripts/strategy_control_api.py` — `StrategyInfo` (lines 238-247), add resource readers, populate in `_build_strategy_registry` (276-395)
- Test: `Scripts/test_strategy_control_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `Scripts/test_strategy_control_api.py`:

```python
import os
import time


def test_get_rss_mb_for_self():
    # The test process itself has a nonzero resident set.
    mb = api.get_rss_mb(os.getpid())
    assert mb is not None and mb > 0


def test_get_rss_mb_dead_pid_returns_none():
    assert api.get_rss_mb(2_000_000) is None


def test_get_cpu_percent_returns_none_on_first_sample():
    # First sample has no prior baseline → None.
    api._proc_cpu_prev.pop(os.getpid(), None)
    assert api.get_cpu_percent(os.getpid()) is None


def test_get_cpu_percent_second_sample_is_float():
    pid = os.getpid()
    api._proc_cpu_prev.pop(pid, None)
    api.get_cpu_percent(pid)            # seed
    time.sleep(0.05)
    val = api.get_cpu_percent(pid)      # delta
    assert val is not None and isinstance(val, float) and val >= 0.0


def test_list_strategies_has_new_fields(client, control_file, monkeypatch):
    # Construct the strategy with resources computed via the real helpers, so this
    # test verifies the fields serialize into the GET response (the /proc reading
    # itself is covered by test_get_rss_mb_for_self / test_get_cpu_percent_*).
    pid = os.getpid()
    info = api.StrategyInfo(
        strategy_id="alpha-123", algorithm_id="alpha-123", group="soloquant-lp",
        status="running", pid=pid, config_path="/tmp/cfg.json",
        cpu_percent=api.get_cpu_percent(pid),
        rss_mb=api.get_rss_mb(pid),
        manual_hold=False,
    )
    monkeypatch.setattr(api, "get_strategies", lambda: [info])
    r = client.get("/api/strategies")
    assert r.status_code == 200
    item = r.json()["strategies"][0]
    assert "cpu_percent" in item
    assert "rss_mb" in item
    assert "manual_hold" in item
    assert item["manual_hold"] is False  # no hold written
    assert item["rss_mb"] is not None and item["rss_mb"] > 0


def test_build_registry_attaches_resources(monkeypatch, control_file):
    """_build_strategy_registry itself must stamp cpu_percent/rss_mb/manual_hold.

    We point LIVEPAPER_DIR at a temp dir with one synthetic pid.json whose pid is
    the test process, so the registry has one running strategy with real resources.
    """
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    pidfile = tmp / "alpha-123.pid.json"
    pidfile.write_text(json.dumps({"strategy_id": "alpha-123", "pid": os.getpid()}))
    monkeypatch.setattr(api, "LIVEPAPER_DIR", tmp)
    monkeypatch.setattr(api, "discover_lean_processes", lambda: [])
    strategies = api._build_strategy_registry()
    match = next((s for s in strategies if s.strategy_id == "alpha-123"), None)
    assert match is not None
    assert match.rss_mb is not None and match.rss_mb > 0
    assert match.manual_hold is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: new tests FAIL — `AttributeError: module 'strategy_control_api' has no attribute 'get_rss_mb'`.

- [ ] **Step 3: Add resource readers**

In `Scripts/strategy_control_api.py`, in the Process Helpers section (after `get_uptime_seconds`, ~line 148), add:

```python
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
```

- [ ] **Step 4: Extend StrategyInfo and populate in the registry**

Extend the `StrategyInfo` model (lines 238-247) — add three fields after `started_at_utc`:

```python
    cpu_percent: Optional[float] = None
    rss_mb: Optional[int] = None
    manual_hold: bool = False
```

At the top of `_build_strategy_registry` (after `running_configs = ...`, ~line 280), load the control state once:

```python
    control_state = _load_control_state()
```

Then populate the new fields at each `strategies.append(StrategyInfo(...))` site:

- **soloquant-lp** (~line 312): add `cpu, rss = _resources_for(actual_pid)` just before the append, and inside the existing `StrategyInfo(...)` call (keep all existing kwargs) add:
```python
                cpu_percent=cpu,
                rss_mb=rss,
                manual_hold=bool(control_state.get(sid, {}).get("manual_hold", False)),
```

- **legacy-lp** (~line 350): add `cpu, rss = _resources_for(running_pid)` before the append, and inside the existing call add:
```python
                cpu_percent=cpu,
                rss_mb=rss,
                manual_hold=bool(control_state.get(alg_id or base_name, {}).get("manual_hold", False)),
```

- **bridge** (~line 367): add `cpu, rss = _resources_for(pid)` before the append, and add `cpu_percent=cpu, rss_mb=rss,` inside the call (bridges keep `manual_hold=False` default).

- **core** (~line 386): same as bridge (`cpu, rss = _resources_for(pid)`; add `cpu_percent=cpu, rss_mb=rss,`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add Scripts/strategy_control_api.py Scripts/test_strategy_control_api.py
git commit -m "feat(live-paper): surface cpu_percent/rss_mb/manual_hold in GET /api/strategies

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Orchestrator manual_hold guard

The launcher `start_registered_live_paper_strategies` skips strategies with `manual_hold=true`. This is what makes a stop *sticky* — the next pipeline tick will not relaunch.

**Files:**
- Modify: `Scripts/soloquant_pipeline_runner.py` — add helper + guard in `start_registered_live_paper_strategies` (460-538)
- Test: `Scripts/test_live_paper_manual_hold.py` (create)

**Key facts:**
- `start_registered_live_paper_strategies(registry_path, process_root=None, popen=subprocess.Popen, process_alive=..., extra_env=None)` — `popen` and `process_alive` are injectable, so tests use fakes (no real LEAN process).
- Guard inserts **after** the `retired` skip (line 494) and **before** `pid_file = ...` (line 496): a held strategy must not be launched even when its PID is dead.

- [ ] **Step 1: Write the failing test**

Create `Scripts/test_live_paper_manual_hold.py`:

```python
"""Tests: manual_hold strategies are skipped by the live-paper launcher."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import soloquant_pipeline_runner as pr


REGISTRY = {
    "strategies": [
        {
            "strategy_id": "alpha-123",
            "status": "serving",
            "live-paper-config": "",  # filled per-test
        }
    ]
}


def _write_registry(tmp_path):
    cfg = tmp_path / "config-live-paper.json"
    cfg.write_text("{}")
    REGISTRY["strategies"][0]["live-paper-config"] = str(cfg)
    reg = tmp_path / "strategy-registry.json"
    reg.write_text(json.dumps(REGISTRY))
    return reg


class FakePopen:
    def __init__(self, *a, **k):
        self.pid = 4242
        FakePopen.calls += 1

FakePopen.calls = 0


def test_manual_hold_strategy_is_skipped(tmp_path):
    reg = _write_registry(tmp_path)
    (tmp_path / "live-paper-control.json").write_text(json.dumps({
        "alpha-123": {"manual_hold": True, "last_action": "stop"}
    }))
    FakePopen.calls = 0
    report = pr.start_registered_live_paper_strategies(
        reg, process_root=tmp_path,
        popen=FakePopen, process_alive=lambda pid: False,
    )
    assert report["started_count"] == 0
    assert FakePopen.calls == 0
    reasons = [s.get("reason") for s in report["skipped"]]
    assert "manual_hold" in reasons


def test_no_hold_strategy_is_launched(tmp_path):
    reg = _write_registry(tmp_path)
    # No control file → no hold.
    FakePopen.calls = 0
    report = pr.start_registered_live_paper_strategies(
        reg, process_root=tmp_path,
        popen=FakePopen, process_alive=lambda pid: False,
    )
    assert report["started_count"] == 1
    assert FakePopen.calls == 1


def test_manual_hold_false_strategy_is_launched(tmp_path):
    reg = _write_registry(tmp_path)
    (tmp_path / "live-paper-control.json").write_text(json.dumps({
        "alpha-123": {"manual_hold": False, "last_action": "start"}
    }))
    FakePopen.calls = 0
    report = pr.start_registered_live_paper_strategies(
        reg, process_root=tmp_path,
        popen=FakePopen, process_alive=lambda pid: False,
    )
    assert report["started_count"] == 1
    assert FakePopen.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_live_paper_manual_hold.py -v`
Expected: `test_manual_hold_strategy_is_skipped` FAILS — the held strategy is launched (`started_count == 1`, `FakePopen.calls == 1`) because no guard exists yet.

- [ ] **Step 3: Add the helper and guard**

In `Scripts/soloquant_pipeline_runner.py`, add a module-level helper just above `def start_registered_live_paper_strategies` (before line 460):

```python
def _is_strategy_manually_held(strategy_id: str, control_state_path: str | Path | None) -> bool:
    """True if the operator manually stopped this strategy (sticky hold).

    Missing file / missing entry / read error → False (backward compatible).
    """
    if not control_state_path:
        return False
    path = Path(control_state_path)
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return bool(data.get(strategy_id, {}).get("manual_hold", False))
    except Exception:
        pass
    return False
```

In `start_registered_live_paper_strategies`, add a `control_state_path` parameter to the signature (after `extra_env`):

```python
    extra_env: dict | None = None,
    control_state_path: str | Path | None = None,
) -> dict:
```

Resolve the default right after `root.mkdir(...)` (after line 479):

```python
    if control_state_path is None:
        control_state_path = Path(registry_path).resolve().parent / "live-paper-control.json"
```

Insert the guard **after** the `if status == "retired":` block (after line 494, before `pid_file = root / ...` on line 496):

```python
        if _is_strategy_manually_held(strategy_id, control_state_path):
            skipped.append({"strategy_id": strategy_id, "reason": "manual_hold"})
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_live_paper_manual_hold.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add Scripts/soloquant_pipeline_runner.py Scripts/test_live_paper_manual_hold.py
git commit -m "feat(live-paper): skip manual_hold strategies in launcher (sticky stop)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Barra dashboard regression baseline test

Lock the existing 43 panels BEFORE adding the button panel. This test fails the build if any existing panel's `(title, gridPos, type)` changes — the hard constraint.

**Files:**
- Create: `Scripts/test_barra_dashboard_regression.py`
- Reference: `monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json` (43 panels, uid `barra-cne5-live-paper`)

- [ ] **Step 1: Write the test**

Create `Scripts/test_barra_dashboard_regression.py`:

```python
"""Regression test: the barra-cne5-live-paper dashboard's original panels must never change."""
import json
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "monitoring" / "grafana" / "dashboards" / "lean" / "barra-cne5-live-paper.json"


def _panel_fingerprint(p: dict) -> tuple:
    gp = p.get("gridPos", {})
    return (
        p.get("title", ""),
        (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y")),
        p.get("type", ""),
    )


def test_dashboard_exists():
    assert DASHBOARD.exists(), f"missing {DASHBOARD}"


def test_original_43_panels_unchanged():
    """The 43 panels present before this feature must keep their (title, gridPos, type).

    New panels (e.g. the control button panel) are allowed and must come AFTER index 42.
    """
    db = json.loads(DASHBOARD.read_text())
    panels = db.get("panels", [])
    assert len(panels) >= 43, f"expected >=43 panels, got {len(panels)}"
    baseline = json.loads((Path(__file__).parent / "_barra_baseline_43.json").read_text())
    current = [_panel_fingerprint(p) for p in panels[:43]]
    assert current == [tuple(f) for f in baseline], (
        "An original panel's (title, gridPos, type) changed — this violates the "
        "'do not modify existing dashboard' constraint."
    )


def test_algorithm_id_variable_preserved():
    db = json.loads(DASHBOARD.read_text())
    names = [v.get("name") for v in db.get("templating", {}).get("list", [])]
    assert "algorithm_id" in names
```

- [ ] **Step 2: Generate the baseline snapshot**

Run this once to capture the current 43-panel fingerprint into the baseline file (the test compares against it):

```bash
cd /home/project/hope/Lean && /root/miniconda3/envs/quant311/bin/python3 -c "
import json
from pathlib import Path
d = json.load(open('monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json'))
def fp(p):
    gp = p.get('gridPos', {})
    return [p.get('title',''), [gp.get('h'),gp.get('w'),gp.get('x'),gp.get('y')], p.get('type','')]
Path('Scripts/_barra_baseline_43.json').write_text(json.dumps([fp(p) for p in d['panels'][:43]], ensure_ascii=False, indent=2))
print('baseline written, panels snapshotted:', len(d['panels']))
"
```

- [ ] **Step 3: Run test to verify it passes (baseline established)**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_barra_dashboard_regression.py -v`
Expected: PASS (3 passed) — confirms the dashboard is currently in baseline state.

- [ ] **Step 4: Commit**

```bash
git add Scripts/test_barra_dashboard_regression.py Scripts/_barra_baseline_43.json
git commit -m "test(live-paper): regression baseline for barra dashboard 43 panels

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Grafana button panel (append-only, panel #44)

Append exactly ONE `volkovlabs-button-panel` (#44) at the bottom of the committed dashboard. It has Start/Stop buttons that POST through the authenticated Infinity datasource. The regression test from Task 5 must still pass (original 43 unchanged).

**Mechanism:** The Infinity datasource `strategy-control-api` already injects `Authorization: Bearer <token>` on every proxied request (see `install_strategy_control.sh:62`). volkovlabs-button-panel fires an API request **on click** using a chosen datasource — so pointing its buttons at the Infinity datasource gives authenticated POST with no token in the dashboard JSON.

**Files:**
- Modify: `monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json` (append panel #44)
- Modify: `Scripts/test_barra_dashboard_regression.py` (append one assertion)
- Modify: `monitoring/grafana/install_strategy_control.sh` (install the plugin)

- [ ] **Step 1: Append the button panel to the dashboard**

Run this idempotent merge script (append-only; aborts if a control panel already exists; never touches the first 43 panels):

```bash
cd /home/project/hope/Lean && /root/miniconda3/envs/quant311/bin/python3 - <<'PY'
import json
from pathlib import Path

P = Path("monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json")
db = json.loads(P.read_text())
panels = db["panels"]

assert len(panels) == 43, f"expected 43 baseline panels, got {len(panels)} — re-run Task 5 first"

# Abort if already added (idempotent)
if any(p.get("title") == "Manual Control" for p in panels):
    print("already present, skipping"); raise SystemExit(0)

# Bottom of the grid: existing panels end at y=88.
bottom = max((p.get("gridPos", {}).get("y", 0) + p.get("gridPos", {}).get("h", 0)) for p in panels)

INFINITY = {"type": "yesoreyeram-infinity-datasource", "uid": "strategy-control-api"}

button_panel = {
    "id": 900,
    "type": "volkovlabs-button-panel",
    "title": "Manual Control",
    "datasource": INFINITY,
    "gridPos": {"h": 4, "w": 24, "x": 0, "y": bottom},
    "fieldConfig": {"defaults": {}, "overrides": []},
    "options": {
        "buttons": [
            {
                "name": "Start",
                "type": "API",
                "enable": True,
                "icon": "play",
                "background": "green",
                "api": {
                    "method": "POST",
                    "url": "/api/strategies/by-algorithm/${algorithm_id}/start",
                    "datasource": INFINITY,
                    "data": "",
                    "headers": [],
                },
            },
            {
                "name": "Stop",
                "type": "API",
                "enable": True,
                "icon": "stop",
                "background": "red",
                "api": {
                    "method": "POST",
                    "url": "/api/strategies/by-algorithm/${algorithm_id}/stop",
                    "datasource": INFINITY,
                    "data": "",
                    "headers": [],
                },
            },
        ]
    },
}

panels.append(button_panel)
P.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
print(f"appended panel #44 at y={bottom}; total panels now {len(panels)}")
PY
```

- [ ] **Step 2: Verify regression test still passes**

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_barra_dashboard_regression.py -v`
Expected: PASS — original 43 fingerprints unchanged. (If it fails, the merge touched an existing panel — undo and re-do Step 1.)

- [ ] **Step 3: Add a panel-count assertion to the regression test**

Append to `Scripts/test_barra_dashboard_regression.py`:

```python
def test_button_panel_appended_once():
    db = json.loads(DASHBOARD.read_text())
    control = [p for p in db["panels"] if p.get("title") == "Manual Control"]
    assert len(control) == 1, "exactly one Manual Control button panel expected"
    assert control[0]["type"] == "volkovlabs-button-panel"
```

Run: `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_barra_dashboard_regression.py -v`
Expected: PASS (4 passed).

- [ ] **Step 4: Add plugin install to the deploy script**

In `monitoring/grafana/install_strategy_control.sh`, after the existing plugin install line (line 37: `docker exec ... grafana-cli plugins install yesoreyeram-infinity-datasource ...`), add:

```bash
log_info "Installing volkovlabs-button-panel plugin..."
docker exec "${GRAFANA_CONTAINER}" grafana-cli plugins install volkovlabs-button-panel 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json \
        Scripts/test_barra_dashboard_regression.py \
        monitoring/grafana/install_strategy_control.sh
git commit -m "feat(live-paper): add Manual Control button panel (#44) to barra dashboard

Append-only: original 43 panels untouched. volkovlabs-button-panel POSTs
start/stop through the authenticated Infinity datasource.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Deploy + end-to-end verification + docs

Wire it all together on the live server and verify the full flow: Grafana Stop → PID dies + `manual_hold=true` → next pipeline tick does NOT relaunch; Start → resumes.

**Files:**
- No new source; this task runs deploy commands. The API daemon must be restarted to pick up Tasks 1-3; the pipeline-runner daemon restarted to pick up Task 4.
- Doc: `docs/up-soloquant.md` (append a short "Manual Control" section)

- [ ] **Step 1: Restart the Strategy Control API + pipeline runner**

```bash
cd /home/project/hope/Lean
./sqctl.sh restart core          # pipeline-runner picks up the manual_hold guard (Task 4)
# API restart — if sqctl.sh has an 'api' group use it, else use the install script (it restarts uvicorn in its Step 6):
./sqctl.sh restart api 2>/dev/null || bash monitoring/grafana/install_strategy_control.sh
curl -sf http://localhost:5000/api/health && echo "  <- api healthy"
```
Expected: JSON with `"status":"ok"`.

- [ ] **Step 2: Deploy the dashboard + plugin to Grafana**

```bash
# Installs the button-panel plugin (added in Task 6 Step 4), then redeploys dashboards.
bash monitoring/grafana/install_strategy_control.sh
# Copy the updated barra dashboard into Grafana's bind-mounted dashboards dir.
cp monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json \
   /home/project/curiocity/Lean/grafana/dashboards/barra-cne5-live-paper.json
```

Open `http://84.8.248.198:3000/d/barra-cne5-live-paper/` and confirm:
- The original 43 panels are unchanged.
- A new "Manual Control" panel with Start/Stop buttons renders at the bottom.

> **Schema note:** If the buttons render but clicking does nothing (silent failure), the installed volkovlabs-button-panel version may use a slightly different `options.buttons[].api` schema. In the Grafana editor, open the Manual Control panel, confirm each button's "API Request" is method=POST + the URL + datasource=`Strategy Control API`, fix in-UI, save, then export and reconcile the committed JSON so the file matches what's deployed. Re-run the Task 5/6 regression tests afterward.

- [ ] **Step 3: End-to-end verification (manual, record results)**

Pick one **serving** live-paper strategy from `Results/soloquant/strategy-registry.json`. Let `<SID>` be its `strategy_id` and `<AID>` its `algorithm_id`.

1. **Stop via API** (simulates the Grafana Stop button):
   ```bash
   TOKEN=$(cat Results/soloquant/.strategy-api-token)
   curl -s -X POST http://localhost:5000/api/strategies/<SID>/stop -H "Authorization: Bearer $TOKEN"
   ```
   Expected: `{"success":true,"action":"stop",...}`.
2. **Confirm stickiness:**
   ```bash
   cat Results/soloquant/live-paper-control.json    # shows <SID> manual_hold: true
   ```
3. **Force a live-paper tick** and confirm the strategy is NOT relaunched:
   ```bash
   python3 Scripts/soloquant_pipeline_runner.py --config config-soloquant.json --once
   ps -ef | grep "[<AID>]" | grep -v grep || echo "no LEAN process for <AID> (correct)"
   ```
   Expected: no LEAN process for `<AID>` is running (the launcher skipped it with reason `manual_hold`).
4. **Resume via API** (simulates Grafana Start):
   ```bash
   curl -s -X POST http://localhost:5000/api/strategies/<SID>/start -H "Authorization: Bearer $TOKEN"
   ```
   Expected: `{"success":true,...}`; `live-paper-control.json` shows `manual_hold: false`; the next pipeline tick relaunches it.
5. **Resource fields:**
   ```bash
   curl -s "http://localhost:5000/api/strategies?group=soloquant-lp" | python3 -m json.tool | grep -E 'cpu_percent|rss_mb|manual_hold'
   ```
   Expected: `rss_mb` is a positive integer for running strategies; `manual_hold` matches control state.

- [ ] **Step 4: Document the operator workflow**

Append to `docs/up-soloquant.md` a "## 手动启停 live-paper 策略" section:

```markdown
## 手动启停 live-paper 策略

在 Live Paper Trading 看板 (`/d/barra-cne5-live-paper/`) 底部 "Manual Control" 面板：
- 选定 `algorithm_id` 变量 → 点 **Stop**：立即停进程，并写入粘性 `manual_hold=true`，
  pipeline 后续 tick 不会自动重启该策略。
- 点 **Start**：清除 `manual_hold=false` 并启动，pipeline 恢复正常维护。
- 后端：`Results/soloquant/live-paper-control.json`（按 strategy_id 记录）。
  API：`POST /api/strategies/{id}/stop|start`（Bearer token，见 `.strategy-api-token`）。
  GET `/api/strategies` 含每进程 `cpu_percent`/`rss_mb` 与 `manual_hold`。
```

- [ ] **Step 5: Commit**

```bash
git add docs/up-soloquant.md
git commit -m "docs(live-paper): document manual start/stop operator workflow

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Verification (whole plan)

After all tasks:

- [ ] `/root/miniconda3/envs/quant311/bin/python3 -m pytest Scripts/test_strategy_control_api.py Scripts/test_live_paper_manual_hold.py Scripts/test_barra_dashboard_regression.py -v` → all green.
- [ ] `git log --oneline -7` shows 7 commits, one per task.
- [ ] `monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json` has exactly 44 panels; first 43 match `Scripts/_barra_baseline_43.json`.
- [ ] Manual E2E (Task 7 Step 3) recorded: stop sticks across a pipeline tick; start resumes; resource fields populate.

## Out of scope (per spec §6)

- Automatic resource-threshold rotation (Prefect flow).
- Changes to `strategy-registry.json` schema.
- Any change to the original 43 panels, the layout, or the `algorithm_id` variable.
- The `strategy-control.json` dashboard.
