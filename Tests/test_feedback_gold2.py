"""Tests for Gold2FeedbackAdapter trigger + min_trades gate + shaping_overrides. Spec §3.2, §3.3."""
import sys
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from adapters.base import FeedbackAction  # noqa: E402
from adapters.gold2 import Gold2FeedbackAdapter  # noqa: E402


def _manifest(thresholds=None, term_map=None, obs_fields=None):
    class _M:
        raw = {
            "feedback": {
                "trigger_thresholds": thresholds or {"review_status_fail": True,
                    "max_layer_attribution_gap": 0.15, "min_narrative_trades": 20},
                "shaping_term_map": term_map or {"extreme_risk": "extreme_risk_contrib_penalty",
                                                  "realrate_cap": "realrate_cap_contrib_penalty"},
                "observation_fields": obs_fields or ["tpv", "w_smooth"],
            }
        }
    return _M()


def _review(status="pass", layer_pcts=None, n_trades=100):
    # defaults all within gap threshold 0.15 so no trigger/shaping by default
    return {
        "review_status": status,
        "layer_attribution": {
            "trend": {"pnl_pct_of_total": (layer_pcts or {}).get("trend", 0.10)},
            "vol_target": {"pnl_pct_of_total": (layer_pcts or {}).get("vol_target", 0.08)},
            "extreme_risk": {"pnl_pct_of_total": (layer_pcts or {}).get("extreme_risk", -0.05)},
            "realrate_cap": {"pnl_pct_of_total": (layer_pcts or {}).get("realrate_cap", 0.07)},
        },
        "per_trade_narrative": list(range(n_trades)),
        "run_meta": {"attribution_method": "telescoping"},
    }


def test_layers_constant():
    assert Gold2FeedbackAdapter.LAYERS == ["trend", "vol_target", "extreme_risk", "realrate_cap"]


def test_review_status_fail_triggers():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(status="fail"), [])
    assert action.trigger is True
    assert "review_status=fail" in action.trigger_reason


def test_layer_gap_triggers():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is True
    assert "extreme_risk" in action.trigger_reason


def test_no_trigger_when_pass_small_gap():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.05}), [])
    assert action.trigger is False


def test_min_trades_gate_skips_layer_gap():
    """Spec §3.2 eval-update2 #3: n_trades<min → layer_gap skipped."""
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(n_trades=5, layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is False
    assert "small-sample" in action.trigger_reason or "skipped" in action.trigger_reason


def test_min_trades_gate_keeps_review_status_fail():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(status="fail", n_trades=5, layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is True
    assert "review_status=fail" in action.trigger_reason


def test_shaping_overrides_weight():
    """Spec §3.3: weight = clamp(gap/0.15, 0.5, 3.0). extreme_risk gap=0.32 → 2.13."""
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert "extreme_risk_contrib_penalty" in action.shaping_overrides
    assert abs(action.shaping_overrides["extreme_risk_contrib_penalty"] - 2.13) < 0.01


def test_shaping_overrides_empty_when_no_gap():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.05}), [])
    assert action.shaping_overrides == {}


def test_no_state_trace_per_bar_empty():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(), [])
    assert action.per_bar_layer_contrib == []


def test_attribution_method_passthrough():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(), [])
    assert action.attribution_method == "telescoping"
