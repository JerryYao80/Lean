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
