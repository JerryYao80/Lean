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
