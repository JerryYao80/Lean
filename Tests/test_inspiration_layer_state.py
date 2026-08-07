"""Tests for LayerState + state machine + load/save. Spec §1.2, §2.3."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from layer_state import LayerState, transition, load_layer_states, save_layer_states  # noqa: E402


def test_layer_state_defaults():
    ls = LayerState(layer="extreme_risk", status="optimizing")
    assert ls.status == "optimizing"
    assert ls.pending_since_generation == -1
    assert ls.inspired_strategy_id == ""
    assert ls.retired_shaping_terms == []
    assert ls.candidate_deployed is False
    assert ls.candidate_status == "none"


def test_transition_optimizing_to_inspiration_pending():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    transition(states, "extreme_risk", "inspiration_pending", pending_since_generation=3)
    assert states["extreme_risk"].status == "inspiration_pending"
    assert states["extreme_risk"].pending_since_generation == 3


def test_transition_inspiration_pending_to_redesigned():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
    transition(states, "extreme_risk", "redesigned", inspired_strategy_id="NewStrategy-abc",
               retired_shaping_terms=["extreme_risk_contrib_penalty"], candidate_status="pending_review")
    assert states["extreme_risk"].status == "redesigned"
    assert states["extreme_risk"].inspired_strategy_id == "NewStrategy-abc"
    assert states["extreme_risk"].retired_shaping_terms == ["extreme_risk_contrib_penalty"]
    assert states["extreme_risk"].candidate_status == "pending_review"


def test_transition_timeout_back_to_optimizing():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
    transition(states, "extreme_risk", "optimizing")
    assert states["extreme_risk"].status == "optimizing"
    assert states["extreme_risk"].pending_since_generation == -1


def test_transition_redesigned_to_inspiration_pending_allowed():
    states = {"extreme_risk": LayerState("extreme_risk", "redesigned", candidate_status="deployed")}
    transition(states, "extreme_risk", "inspiration_pending", pending_since_generation=10)
    assert states["extreme_risk"].status == "inspiration_pending"


def test_transition_illegal_raises():
    import pytest
    states = {"extreme_risk": LayerState("extreme_risk", "redesigned")}
    with pytest.raises(ValueError, match="illegal"):
        transition(states, "extreme_risk", "optimizing")


def test_load_save_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3,
                                              candidate_status="pending_review")}
        save_layer_states(state_path, "Gold2", states)
        loaded = load_layer_states(state_path, "Gold2")
        assert loaded["extreme_risk"].status == "inspiration_pending"
        assert loaded["extreme_risk"].candidate_status == "pending_review"


def test_load_missing_strategy_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        loaded = load_layer_states(str(Path(d) / "state.json"), "Nonexistent")
        assert loaded == {}


def test_load_without_layer_field():
    """Tests backward compatibility: layer_state dict without explicit 'layer' field."""
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        # Simulate serialized dict without 'layer' field (backward compatibility)
        Path(state_path).write_text(json.dumps({
            'layer_states': {
                'Gold2': {
                    'extreme_risk': {
                        'status': 'inspiration_pending',
                        'pending_since_generation': 3,
                        'inspired_strategy_id': 'NewStrategy-abc',
                        'retired_shaping_terms': ['extreme_risk_contrib_penalty'],
                        'candidate_deployed': False,
                        'candidate_status': 'pending_review'
                    }
                }
            }
        }))
        loaded = load_layer_states(state_path, "Gold2")
        assert loaded["extreme_risk"].status == "inspiration_pending"
        assert loaded["extreme_risk"].layer == "extreme_risk"  # Auto-inferred from key
        assert loaded["extreme_risk"].pending_since_generation == 3
        assert loaded["extreme_risk"].inspired_strategy_id == "NewStrategy-abc"
        assert loaded["extreme_risk"].candidate_status == "pending_review"

