"""Failing contracts for the interface readiness recheck (Task 12).

The readiness check rejects any proof module that imports/reads production
daemon/evolution/generation/layer-state, globs the global Results, hard-codes
OptionVolArb paths, hard-codes ONNX obs-dim, has a writable blind mount, or
enables the fallbacks. Spec §6 / §9.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.interface_readiness import (
    InterfaceReadiness,
    inspect_proof_interfaces,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_readiness_rejects_daemon_import(tmp_path):
    (tmp_path / "bad.py").write_text("import evolution_scheduler\n")
    result = inspect_proof_interfaces(tmp_path)
    assert "evolution_scheduler" in result.forbidden_references


def test_readiness_rejects_layer_state_import(tmp_path):
    (tmp_path / "bad.py").write_text("from Scripts.inspiration import layer_state\n")
    result = inspect_proof_interfaces(tmp_path)
    assert "layer_state" in result.forbidden_references


def test_readiness_rejects_global_results_glob(tmp_path):
    (tmp_path / "bad.py").write_text(
        "import glob\nx = glob.glob('../../Results/*')\n"
    )
    result = inspect_proof_interfaces(tmp_path)
    assert "global_results_glob" in result.forbidden_patterns


def test_readiness_rejects_optionvolarb_path(tmp_path):
    (tmp_path / "bad.py").write_text(
        "p = 'OptionVolArb/CQL/trace.json'\n"
    )
    result = inspect_proof_interfaces(tmp_path)
    assert "optionvolarb_path" in result.forbidden_patterns


def test_readiness_rejects_hardcoded_obs_dim(tmp_path):
    (tmp_path / "bad.py").write_text("obs_dim = 8\n")
    result = inspect_proof_interfaces(tmp_path)
    assert "hardcoded_obs_dim" in result.forbidden_patterns


def test_readiness_rejects_enabled_fallbacks(tmp_path):
    (tmp_path / "bad.py").write_text(
        "fallback_download_enabled = True\n"
    )
    result = inspect_proof_interfaces(tmp_path)
    assert "enabled_fallback" in result.forbidden_patterns


def test_readiness_passes_clean_module(tmp_path):
    (tmp_path / "clean.py").write_text(
        "from Scripts.gold2_closed_loop.adapters.base import TrialResult\n"
        "def f(): return 1\n"
    )
    result = inspect_proof_interfaces(tmp_path)
    assert result.forbidden_references == set()
    assert result.forbidden_patterns == set()
    assert result.ready is True


def test_readiness_rejects_writable_blind_mount(tmp_path):
    """A blind mount that is writable (not read-only) fails readiness."""
    blind = tmp_path / "blind"
    blind.mkdir()
    # Writable by default (mode has write bits for owner).
    result = inspect_proof_interfaces(tmp_path, blind_mount=blind)
    assert "writable_blind_mount" in result.forbidden_patterns
    assert result.ready is False
