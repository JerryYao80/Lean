"""Tests for StrategyFeedbackAdapter ABC + FeedbackAction. Spec §3.1."""
import sys
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))

from adapters.base import StrategyFeedbackAdapter, FeedbackAction  # noqa: E402


def test_feedback_action_fields():
    a = FeedbackAction(trigger=True, trigger_reason="review_status=fail",
                       shaping_overrides={"extreme_risk_contrib_penalty": 2.0},
                       observation_fields=["tpv", "w_smooth"], attribution_method="telescoping",
                       per_bar_layer_contrib=[{"trend": 1.0}])
    assert a.trigger is True
    assert a.shaping_overrides["extreme_risk_contrib_penalty"] == 2.0
    assert a.observation_fields == ["tpv", "w_smooth"]
    assert a.attribution_method == "telescoping"


def test_abc_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        StrategyFeedbackAdapter()


class _GoodAdapter(StrategyFeedbackAdapter):
    LAYERS = ["trend", "vol_target"]
    def feedback_signal(self, manifest, review_doc, state_trace):
        return FeedbackAction(trigger=False, trigger_reason="",
                              shaping_overrides={}, observation_fields=[],
                              attribution_method="residual", per_bar_layer_contrib=[])


def test_good_adapter_returns_feedback_action():
    ad = _GoodAdapter()
    action = ad.feedback_signal(None, {"review_status": "pass"}, [])
    assert isinstance(action, FeedbackAction)
    assert action.trigger is False


def test_feedback_action_default_per_bar_empty():
    a = FeedbackAction(trigger=False, trigger_reason="", shaping_overrides={},
                       observation_fields=[], attribution_method="residual")
    assert a.per_bar_layer_contrib == []
