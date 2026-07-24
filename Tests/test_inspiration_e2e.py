"""End-to-end: 3 gen non-convergent → trigger → hypothesis MD → provenance → redesigned. Spec §5.2."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from layer_state import load_layer_states, LayerState, transition, save_layer_states  # noqa: E402
from hypothesize import run as run_hypothesize  # noqa: E402
from provenance import write, verify_layer_improvement  # noqa: E402


def test_e2e_trigger_to_hypothesis():
    with tempfile.TemporaryDirectory() as d:
        gen_dir = Path(d) / "Results" / "auto_optimize" / "Gold2"
        gen_dir.mkdir(parents=True)
        for i, (g, w) in enumerate([(0.32, 0.5), (0.33, 1.5), (0.34, 2.13)], 1):
            (gen_dir / f"generation_{i}.json").write_text(json.dumps({
                "strategy": "Gold2", "generation": i, "review_status": "pass",
                "layer_gaps": {"extreme_risk": {"gap": g}},
                "shaping_overrides": {"extreme_risk_contrib_penalty": w},
            }))
        from trigger import detect
        from generations import read_history
        states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
        hist = read_history("Gold2", 3, repo_root=d)
        to_inspire = detect("Gold2", hist, states, {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1})
        assert "extreme_risk" in to_inspire

        mock_md = "# 受 Gold2 extreme_risk 层失效启发的策略假设\n\n## 根因诊断\ncap失效..." + "x" * 100
        hypo_dir = str(Path(d) / "local-strategies")
        with patch("hypothesize._call_llm", return_value=mock_md):
            md_path = run_hypothesize("Gold2", "extreme_risk", {}, hist,
                                      {"review": {"layer_names": ["extreme_risk"]},
                                       "inspiration": {"persistence": {"gap_threshold": 0.15}}},
                                      {"model": "glm-5.1", "temperature": 0.7}, hypothesis_dir=hypo_dir)
        assert Path(md_path).exists()
        assert len(Path(md_path).read_text()) > 100


def test_e2e_provenance_and_layer_state_redesigned():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
        save_layer_states(state_path, "Gold2", states)
        parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
        new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
        result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
        assert result["improved"] is True
        manifest_path = Path(d) / "new_manifest.json"
        manifest_path.write_text(json.dumps({"strategy_id": "NewStrategy-abc"}))
        write(str(manifest_path), "Gold2", "review.json", "extreme_risk", "hypothesis.md", 3,
              improvement_verified=True, layer_improvement=result["improvement"])
        states = load_layer_states(state_path, "Gold2")
        transition(states, "extreme_risk", "redesigned", inspired_strategy_id="NewStrategy-abc",
                   retired_shaping_terms=["extreme_risk_contrib_penalty"], candidate_status="pending_review")
        save_layer_states(state_path, "Gold2", states)
        loaded = load_layer_states(state_path, "Gold2")
        assert loaded["extreme_risk"].status == "redesigned"
        assert loaded["extreme_risk"].candidate_status == "pending_review"
