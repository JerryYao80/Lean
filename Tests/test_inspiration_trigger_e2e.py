"""Tests for evolution_scheduler 6th trigger + generation log + timeout. Spec §4."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import check_triggers  # noqa: E402


class _FakeManifest:
    strategy_name = "Gold2"
    raw = {
        "feedback": {"adapter_module": "adapters.gold2", "adapter_class": "Gold2FeedbackAdapter",
            "trigger_thresholds": {"review_status_fail": True, "max_layer_attribution_gap": 0.15, "min_narrative_trades": 20}},
        "inspiration": {"persistence": {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1},
                        "timeout_generations": 5},
    }


def _write_gen_history(repo_root, strategy, layer, gaps, weights):
    gen_dir = Path(repo_root) / "Results" / "auto_optimize" / strategy
    gen_dir.mkdir(parents=True, exist_ok=True)
    for i, (g, w) in enumerate(zip(gaps, weights), 1):
        (gen_dir / f"generation_{i}.json").write_text(json.dumps({
            "strategy": strategy, "generation": i, "review_status": "pass",
            "layer_gaps": {layer: {"gap": g}}, "shaping_overrides": {layer + "_contrib_penalty": w},
        }))


def test_inspiration_trigger_true_when_3_gen_nonconvergent():
    with tempfile.TemporaryDirectory() as d:
        _write_gen_history(d, "Gold2", "extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
        state_path = str(Path(d) / "state.json")
        Path(state_path).write_text(json.dumps({"generation_count": 3}))
        state = {"_state_path": state_path, "generation_count": 3, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("inspiration") is True


def test_inspiration_false_when_fewer_than_3_gen():
    with tempfile.TemporaryDirectory() as d:
        _write_gen_history(d, "Gold2", "extreme_risk", [0.32, 0.33], [0.5, 1.5])
        state_path = str(Path(d) / "state.json")
        Path(state_path).write_text(json.dumps({"generation_count": 2}))
        state = {"_state_path": state_path, "generation_count": 2, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("inspiration") is False


def test_timeout_recycles_inspiration_pending_to_optimizing():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        Path(state_path).write_text(json.dumps({
            "generation_count": 10,
            "layer_states": {"Gold2": {"extreme_risk": {"status": "inspiration_pending", "pending_since_generation": 1,
                "inspired_strategy_id": "", "retired_shaping_terms": [], "candidate_deployed": False, "candidate_status": "none"}}}
        }))
        state = {"_state_path": state_path, "generation_count": 10, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        from layer_state import load_layer_states
        states = load_layer_states(state_path, "Gold2")
        assert states["extreme_risk"].status == "optimizing"
