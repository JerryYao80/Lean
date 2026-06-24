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
