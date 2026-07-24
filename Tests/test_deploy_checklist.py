"""Tests for deploy_gate checklist printing. Spec §5.3.1."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import _print_deploy_checklist  # noqa: E402
from adapters.base import FeedbackAction  # noqa: E402


def test_print_deploy_checklist_outputs_3_items():
    action = FeedbackAction(trigger=True, trigger_reason="layer_gap extreme_risk=-0.32>0.15",
                            shaping_overrides={"extreme_risk_contrib_penalty": 2.13},
                            observation_fields=["tpv", "w_smooth", "trend_dir"],
                            attribution_method="telescoping", per_bar_layer_contrib=[])
    out = _print_deploy_checklist(action, onnx_path="/tmp/policy.onnx",
                                  manifest_path="manifest.yaml",
                                  prev_gen_shaping={"extreme_risk_contrib_penalty": 1.0})
    assert "[deploy_gate checklist]" in out
    assert "extreme_risk_contrib_penalty" in out
    assert "2.13" in out
    assert "obs_dim" in out


def test_print_deploy_checklist_flags_weight_jump():
    action = FeedbackAction(trigger=True, trigger_reason="",
                            shaping_overrides={"extreme_risk_contrib_penalty": 3.0},
                            observation_fields=["tpv"], attribution_method="residual", per_bar_layer_contrib=[])
    out = _print_deploy_checklist(action, onnx_path="x", manifest_path="m",
                                  prev_gen_shaping={"extreme_risk_contrib_penalty": 1.0})
    assert "jump" in out.lower() or "跳变" in out


def test_print_deploy_checklist_inspiration_candidate_reminder(tmp_path):
    """Spec §4.5.1: redesigned + pending_review layer → 候选策略待审提醒 (spirit2 #3 / spirit3 #1)."""
    import json
    sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
    state_path = tmp_path / "state.json"
    # parent strategy Gold2 has extreme_risk layer redesigned w/ pending_review candidate
    state_path.write_text(json.dumps({"layer_states": {"Gold2": {
        "extreme_risk": {"layer": "extreme_risk", "status": "redesigned",
                         "pending_since_generation": 3, "inspired_strategy_id": "NewStrategy-abc",
                         "retired_shaping_terms": ["extreme_risk_contrib_penalty"],
                         "candidate_deployed": False, "candidate_status": "pending_review"}}}}))
    action = FeedbackAction(trigger=False, trigger_reason="", shaping_overrides={},
                            observation_fields=["tpv"], attribution_method="residual", per_bar_layer_contrib=[])
    out = _print_deploy_checklist(action, onnx_path="x", manifest_path="m",
                                  prev_gen_shaping=None,
                                  state_path=str(state_path), manifest_strategy_name="Gold2")
    assert "inspiration 候选策略待审" in out
    assert "NewStrategy-abc" in out
    assert "pending_review" in out or "审" in out
