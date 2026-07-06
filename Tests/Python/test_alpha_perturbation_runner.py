import numpy as np, pytest
from alpha_perturbation_runner import sample_alpha_sequence, AlphaPerturbationConfig


def test_beta_distribution_alpha_in_unit_interval():
    cfg = AlphaPerturbationConfig(distribution="beta", n_bars=100, seed=42, beta_a=8, beta_b=2)
    seq = sample_alpha_sequence(cfg)
    assert len(seq) == 100
    assert all(0.0 <= a <= 1.0 for a in seq)
    assert np.var(seq) > 0.01


def test_uniform_distribution_alpha_in_range():
    cfg = AlphaPerturbationConfig(distribution="uniform", n_bars=50, seed=42, uniform_low=0.3, uniform_high=1.0)
    seq = sample_alpha_sequence(cfg)
    assert len(seq) == 50
    assert all(0.3 <= a <= 1.0 for a in seq)


def test_seed_reproducibility():
    cfg = AlphaPerturbationConfig(distribution="beta", n_bars=20, seed=123)
    seq1 = sample_alpha_sequence(cfg)
    seq2 = sample_alpha_sequence(cfg)
    assert np.allclose(seq1, seq2)
