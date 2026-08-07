"""Tests for trigger.detect (continuous N-gen non-convergence + ceiling). Spec §2.4, spirit2 #1."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from layer_state import LayerState  # noqa: E402
from trigger import detect  # noqa: E402

_THRESHOLDS = {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1}
_CEILING = 3.0


def _gen(layer, gaps, weights):
    return [
        {"generation": i + 1, "layer_gaps": {layer: {"gap": g}},
         "shaping_overrides": {layer + "_contrib_penalty": w}}
        for i, (g, w) in enumerate(zip(gaps, weights))
    ]


def test_triggers_when_gap_persistent_and_weight_rising():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert "extreme_risk" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)


def test_no_trigger_when_weight_converged():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.32, 0.32], [1.0, 1.03, 1.05])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_no_trigger_when_fewer_than_min_generations():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.33], [0.5, 1.5])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_ceiling_judges_nonconvergent():
    """spirit2 #1: weight pinned at ceiling [3.0,3.0,3.0] + gap over → non-convergent."""
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.40, 0.60], [3.0, 3.0, 3.0])
    assert "extreme_risk" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)


def test_no_trigger_when_layer_already_inspiration_pending():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending")}
    history = _gen("extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_generic_layer_name_not_hardcoded():
    states = {"my_custom_layer": LayerState("my_custom_layer", "optimizing")}
    history = _gen("my_custom_layer", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert "my_custom_layer" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)
