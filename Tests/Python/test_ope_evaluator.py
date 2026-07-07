import numpy as np, pytest
from ope_evaluator import fqe_evaluate, fqe_self_check, compute_lower_bounds

def test_fqe_evaluate_returns_finite_value(tmp_path):
    from synthetic_sanity_mdp import build_synthetic_dataset
    from offline_rl_trainer import train_offline_rl, OfflineRLConfig
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=100, seed=1)
    cfg = OfflineRLConfig(algorithm="cql", n_steps=50, device="cpu", seed=0)
    policy_path = str(tmp_path / "p.pt")
    train_offline_rl(obs, acts, rews, terms, cfg, policy_path)
    import d3rlpy
    model = d3rlpy.load_learnable(policy_path)
    value = fqe_evaluate(model, obs, acts, rews, terms, n_steps=50)
    assert np.isfinite(value)
    assert isinstance(value, float)

def test_fqe_self_check_calibrated():
    from synthetic_sanity_mdp import build_synthetic_dataset
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=100, seed=2)
    true_return = float(np.mean(rews))
    report = fqe_self_check(obs, acts, rews, terms, n_steps=50)
    assert "estimate" in report
    assert report["true_return"] == pytest.approx(true_return, abs=1e-6)

def test_lower_bounds_includes_random_and_constant():
    from synthetic_sanity_mdp import build_synthetic_dataset
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=50, seed=3)
    bounds = compute_lower_bounds(obs, rews, terms)
    assert "random_alpha" in bounds
    assert "constant_alpha_1" in bounds
    assert isinstance(bounds["random_alpha"], float)
