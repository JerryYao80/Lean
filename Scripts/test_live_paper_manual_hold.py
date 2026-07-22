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
