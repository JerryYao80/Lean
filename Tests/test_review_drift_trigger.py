"""Tests for evolution_scheduler review_drift 5th trigger. Spec §4.1."""
import json
import sys
import tempfile
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import check_triggers, _write_feedback_action, _load_feedback_action  # noqa: E402


class _FakeManifest:
    strategy_name = "Gold2"
    raw = {"feedback": {"adapter_module": "adapters.gold2", "adapter_class": "Gold2FeedbackAdapter",
            "trigger_thresholds": {"review_status_fail": True, "max_layer_attribution_gap": 0.15,
                                   "min_narrative_trades": 20}}}


def test_review_drift_false_when_no_sidecar():
    with tempfile.TemporaryDirectory() as d:
        config = {"ppo_training": {"train_window_days": 504}}
        state = {"last_optimize_date": "2026-07-01T00:00:00"}
        triggers = check_triggers(config, state, "nonexistent_metrics.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("review_drift") is False


def test_review_drift_true_when_review_status_fail():
    with tempfile.TemporaryDirectory() as d:
        rdir = Path(d) / "Gold2" / "review"
        rdir.mkdir(parents=True)
        (rdir / "review.json").write_text(json.dumps({"layer_attribution": {}, "per_trade_narrative": list(range(50)), "run_meta": {}}))
        (rdir / "review.last_review.json").write_text(json.dumps({"review_status": "fail", "last_review": "x"}))
        config = {"ppo_training": {"train_window_days": 504}}
        state = {"last_optimize_date": "2026-07-10T00:00:00"}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("review_drift") is True


def test_write_and_load_feedback_action():
    with tempfile.TemporaryDirectory() as d:
        from adapters.base import FeedbackAction
        action = FeedbackAction(trigger=True, trigger_reason="x",
                                shaping_overrides={"extreme_risk_contrib_penalty": 2.0},
                                observation_fields=["tpv"], attribution_method="telescoping",
                                per_bar_layer_contrib=[])
        path = Path(d) / "state.feedback.json"
        _write_feedback_action(action, str(path))
        loaded = _load_feedback_action(str(path))
        assert loaded.trigger is True
        assert loaded.shaping_overrides["extreme_risk_contrib_penalty"] == 2.0
