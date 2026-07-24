"""End-to-end: review.json + sidecar + state_trace → FeedbackAction → reward/observation. Spec §5.2."""
import json
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from signals import orchestrate  # noqa: E402
from manifest_loader import load_manifest  # noqa: E402
from trace_to_mdp_dataset import merge_shaping_overrides, _encode_state, _compute_reward  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def test_e2e_orchestrate_on_real_gold2_review():
    if not (RESULTS / "review" / "review.json").exists():
        import pytest
        pytest.skip("gold2 review artifacts absent")
    m = load_manifest(MANIFEST)
    # gold2 backtest writes to Results/gold2-betavol (results-destination-folder),
    # not Results/<strategy_name>; pass the actual results dir.
    action = orchestrate(m, str(RESULTS))
    assert action is not None
    assert action.attribution_method in ("telescoping", "residual")
    assert isinstance(action.shaping_overrides, dict)
    assert isinstance(action.observation_fields, list)
    assert "tpv" in action.observation_fields
    assert "drawdown" in action.observation_fields


def test_e2e_reward_injection_with_feedback_overrides():
    m = load_manifest(MANIFEST)
    base = [{"term": t.term, "weight": t.weight} for t in m.reward_config.shaping]
    overrides = {"extreme_risk_contrib_penalty": 2.13}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"] for t in merged}
    assert "extreme_risk_contrib_penalty" in terms
    s_next = {"pnl": 0.01, "_per_bar": [{"extreme_risk": -0.05}]}
    r = _compute_reward(s_next, 1.0, merged, 0.5, 0.05)
    assert r != 0


def test_e2e_observation_dim_matches_manifest():
    m = load_manifest(MANIFEST)
    obs_fields = m.raw["feedback"]["observation_fields"]
    s = {"tpv": 1000.0, "w_smooth": 0.5, "trend_dir": 1, "drawdown": 0.1, "pnl": 0.01}
    arr = _encode_state(s, 0, observation_fields=obs_fields)
    assert len(arr) == len(obs_fields)
