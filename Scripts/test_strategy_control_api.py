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


def test_stop_by_algorithm_sets_manual_hold(client, control_file, monkeypatch):
    # strategy_id "alpha-123" but algorithm_id "AlphaAlgo" — resolve by algorithm
    info = api.StrategyInfo(strategy_id="alpha-123", algorithm_id="AlphaAlgo",
                            group="soloquant-lp", status="running", pid=99999, config_path="/tmp/cfg.json")
    monkeypatch.setattr(api, "get_strategies", lambda: [info])
    monkeypatch.setattr(api, "graceful_stop", lambda p, n, timeout=20: True)
    r = client.post("/api/strategies/by-algorithm/AlphaAlgo/stop", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["strategy_id"] == "alpha-123"
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is True


def test_start_by_algorithm_clears_manual_hold(client, control_file, monkeypatch):
    api._save_control_state("alpha-123", "stop")
    info = api.StrategyInfo(strategy_id="alpha-123", algorithm_id="AlphaAlgo",
                            group="soloquant-lp", status="stopped", pid=None, config_path="/tmp/cfg.json")
    monkeypatch.setattr(api, "get_strategies", lambda: [info])
    monkeypatch.setattr(api, "start_strategy_by_config", lambda cp: (True, "ok", 12345))
    r = client.post("/api/strategies/by-algorithm/AlphaAlgo/start", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["strategy_id"] == "alpha-123"
    state = json.loads(control_file.read_text())
    assert state["alpha-123"]["manual_hold"] is False


def test_by_algorithm_404_when_unknown(client, monkeypatch):
    monkeypatch.setattr(api, "get_strategies", lambda: [])
    r = client.post("/api/strategies/by-algorithm/Nope/stop", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 404


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
