"""Tests for signals.orchestrate. Spec §1.4, §3.2."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from signals import orchestrate  # noqa: E402


class _FakeManifest:
    def __init__(self, strategy_name, feedback_cfg):
        self.strategy_name = strategy_name
        self.raw = {"feedback": feedback_cfg} if feedback_cfg else {}


def _write_review(results_dir, strategy, review_doc, sidecar):
    rdir = Path(results_dir) / strategy / "review"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "review.json").write_text(json.dumps(review_doc))
    (rdir / "review.last_review.json").write_text(json.dumps(sidecar))


def test_orchestrate_merges_review_and_sidecar():
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}, "per_trade_narrative": [],
                                    "run_meta": {"attribution_method": "telescoping"}},
                      {"review_status": "fail", "last_review": "2026-07-12T00:00:00Z"})
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter"})
        action = orchestrate(m, d)
        assert action.trigger is True
        assert action.attribution_method == "telescoping"


def test_orchestrate_no_sidecar_returns_none():
    with tempfile.TemporaryDirectory() as d:
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter"})
        action = orchestrate(m, d)
        assert action is None


def test_orchestrate_no_feedback_block_returns_none():
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}}, {"review_status": "pass"})
        m = _FakeManifest("Gold2", None)
        action = orchestrate(m, d)
        assert action is None


def test_orchestrate_reads_state_trace_by_convention():
    """Spec §1.4: state_trace at Results/<strategy>/state_trace.jsonl."""
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}, "per_trade_narrative": list(range(50)),
                                    "run_meta": {"attribution_method": "telescoping"}},
                      {"review_status": "pass"})
        stpath = Path(d) / "Gold2" / "state_trace.jsonl"
        stpath.parent.mkdir(parents=True, exist_ok=True)
        stpath.write_text(json.dumps({"ts": "2020-07-01", "tpv": 1000.0, "dir_coef": 1.0,
                                       "w_after_vol": 0.5, "extreme_triggered": False,
                                       "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 10.0}) + "\n" +
                          json.dumps({"ts": "2020-07-02", "tpv": 1000.0, "dir_coef": 1.0,
                                       "w_after_vol": 0.5, "extreme_triggered": False,
                                       "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 11.0}))
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter",
                                     "trigger_thresholds": {"min_narrative_trades": 20,
                                       "max_layer_attribution_gap": 0.15, "review_status_fail": True}})
        action = orchestrate(m, d)
        assert action is not None
        assert len(action.per_bar_layer_contrib) == 1
