"""Tests for reward source unification + layer_contrib_penalty. Spec §2.3, §3.3."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from trace_to_mdp_dataset import _compute_reward, merge_shaping_overrides  # noqa: E402


def test_layer_contrib_penalty_registered():
    s_next = {"pnl": 0.01, "_per_bar": [{"extreme_risk": -0.05, "trend": 0.1}]}
    reward_terms = [{"term": "scaled_pnl", "weight": 1.0},
                    {"term": "layer_contrib_penalty", "weight": 2.0}]
    r = _compute_reward(s_next, 1.0, reward_terms, 0.5, 0.05)
    # scaled_pnl=0.01; layer_contrib_penalty=2.0*(-(-0.05))=0.1
    assert abs(r - (0.01 + 0.1)) < 1e-6


def test_layer_contrib_penalty_zero_when_no_neg():
    s_next = {"pnl": 0.01, "_per_bar": [{"trend": 0.1, "extreme_risk": 0.05}]}
    reward_terms = [{"term": "scaled_pnl", "weight": 1.0},
                    {"term": "layer_contrib_penalty", "weight": 2.0}]
    r = _compute_reward(s_next, 1.0, reward_terms, 0.5, 0.05)
    assert abs(r - 0.01) < 1e-6


def test_merge_shaping_overrides_new_term():
    base = [{"term": "scaled_pnl", "weight": 1.0}, {"term": "drawdown_excess_penalty", "weight": 2.0}]
    overrides = {"extreme_risk_contrib_penalty": 2.13}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"]: t["weight"] for t in merged}
    assert terms["extreme_risk_contrib_penalty"] == 2.13
    assert terms["scaled_pnl"] == 1.0


def test_merge_shaping_overrides_overrides_weight():
    base = [{"term": "scaled_pnl", "weight": 1.0}, {"term": "drawdown_excess_penalty", "weight": 2.0}]
    overrides = {"drawdown_excess_penalty": 5.0}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"]: t["weight"] for t in merged}
    assert terms["drawdown_excess_penalty"] == 5.0
    assert len(merged) == 2
