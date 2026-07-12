"""Tests for observation_fields injection. Spec §3.4."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from trace_to_mdp_dataset import _encode_state  # noqa: E402


def test_encode_state_default_8_dim():
    s = {"tpv": 1.0, "cash_pct": 0.5, "var_1d99": 0.7, "var_regime": 0.3,
         "drawdown": 0.1, "n_open_positions": 1, "pnl": 0.01}
    arr = _encode_state(s, 0)
    assert len(arr) == 8


def test_encode_state_custom_fields():
    s = {"tpv": 1000.0, "w_smooth": 0.5, "trend_dir": 1, "extreme_triggered": False}
    arr = _encode_state(s, 0, observation_fields=["tpv", "w_smooth", "trend_dir", "extreme_triggered"])
    assert len(arr) == 4
    assert arr[0] == 1000.0
    assert arr[2] == 1
    assert arr[3] == 0


def test_encode_state_missing_field_fills_zero():
    s = {"tpv": 1000.0}
    arr = _encode_state(s, 0, observation_fields=["tpv", "w_smooth", "drawdown"])
    assert len(arr) == 3
    assert arr[1] == 0
    assert arr[2] == 0
