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


def test_layer_states_skips_inspiration_pending():
    """Spec §1.4: layer in inspiration_pending → review_drift skips its gap判定."""
    ad = Gold2FeedbackAdapter()
    class _LS:
        status = "inspiration_pending"
    layer_states = {"extreme_risk": _LS()}
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [], layer_states=layer_states)
    assert "extreme_risk" not in action.trigger_reason
    assert "extreme_risk_contrib_penalty" not in action.shaping_overrides


def test_layer_states_none_is_backward_compatible():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert "extreme_risk" in action.trigger_reason


def test_per_bar_downsample_sums_to_telescoping():
    """Spec §3.3: per-bar layer contributions (telescoping)."""
    ad = Gold2FeedbackAdapter()
    # close: 10→11→12 (Δp=+1 each); tpv=1000 → scale=1000/10=100
    # w_trend=1.0, w_vol=0.5, w_ext=0.5 (no trigger), w_real=0.5
    state_trace = [
        {"ts": "2020-07-01T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 10.0},
        {"ts": "2020-07-02T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 11.0},
        {"ts": "2020-07-03T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 12.0},
    ]
    per_bar = ad._downsample_per_bar(state_trace)
    assert len(per_bar) == 2  # 2 transitions
    # bar 0→1: Δp=1, scale=100 → C_trend=1.0*100*1=100; C_vol=(0.5-1.0)*100*1=-50; C_ext=0; C_real=0
    assert per_bar[0]["trend"] == 100.0
    assert per_bar[0]["vol_target"] == -50.0
    assert per_bar[0]["extreme_risk"] == 0.0
    assert per_bar[0]["realrate_cap"] == 0.0


def test_per_bar_downsample_empty_on_missing_fields():
    ad = Gold2FeedbackAdapter()
    state_trace = [{"ts": "2020-07-01"}]
    per_bar = ad._downsample_per_bar(state_trace)
    assert per_bar == []
