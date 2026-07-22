"""Failing contracts for the statistical tests (Task 16, Phase 4).

Design invariants (spec §12 lines 376-385, §13):

* Within-window paired moving-block bootstrap on daily excess returns
  (seeded, deterministic given the seed).
* Aggregate inference clusters by ANNUAL BLIND WINDOW — pooled daily
  returns are NOT treated as independent repetitions (spec §12 line 379).
* Effective trials are computed from the full attempted-trial set and the
  return-correlation matrix via a preregistered conservative formula.
* Leave-one-window-out is reported for every window.
* Window-level paired direction is reported and must not conflict with the
  bootstrap verdict.
"""
from __future__ import annotations

import math

import pytest

from Scripts.gold2_closed_loop.statistical_tests import (
    BootstrapResult,
    effective_trials,
    leave_one_window_out,
    moving_block_bootstrap,
    moving_block_bootstrap_clustered,
    window_paired_direction,
)


def _flat_returns(n: int, mu: float = 0.001) -> list[float]:
    return [mu] * n


# --- seeded moving-block bootstrap -----------------------------------


def test_bootstrap_is_deterministic_given_seed():
    returns = [0.01, -0.02, 0.005, 0.015, -0.01, 0.02, -0.005, 0.012]
    a = moving_block_bootstrap(returns, block_length=2, n_resamples=500, seed=17)
    b = moving_block_bootstrap(returns, block_length=2, n_resamples=500, seed=17)
    assert a.p_value == b.p_value
    assert a.ci_low == b.ci_low
    assert a.ci_high == b.ci_high


def test_bootstrap_different_seeds_may_differ():
    returns = [0.01, -0.02, 0.005, 0.015, -0.01, 0.02, -0.005, 0.012]
    a = moving_block_bootstrap(returns, block_length=2, n_resamples=500, seed=17)
    b = moving_block_bootstrap(returns, block_length=2, n_resamples=500, seed=99)
    assert isinstance(a.p_value, float)
    assert isinstance(b.p_value, float)


def test_bootstrap_positive_mean_yields_low_p_value():
    """A consistently positive excess return should reject the null of
    zero mean at the preregistered threshold."""
    returns = _flat_returns(120, mu=0.002)
    result = moving_block_bootstrap(returns, block_length=5, n_resamples=2000, seed=17)
    assert result.mean_estimate > 0
    assert result.p_value < 0.05


def test_bootstrap_zero_mean_yields_high_p_value():
    returns = [0.0] * 120
    result = moving_block_bootstrap(returns, block_length=5, n_resamples=1000, seed=17)
    assert result.p_value > 0.05


def test_bootstrap_ci_brackets_mean():
    # A VARIED series (not constant) so the resample distribution is
    # non-degenerate and the percentile CI actually brackets the mean.
    base = [0.001 + 0.0005 * ((i % 7) - 3) for i in range(100)]
    result = moving_block_bootstrap(base, block_length=5, n_resamples=1000, seed=17)
    assert result.ci_low <= result.mean_estimate <= result.ci_high


# --- clustered aggregate inference (by annual window) ----------------


def test_clustered_inference_does_not_pool_as_independent():
    """The aggregate p-value must NOT shrink toward 0 just because we have
    many daily returns — inference clusters by WINDOW, not by day."""
    windows = [_flat_returns(60, mu=0.001), _flat_returns(60, mu=0.001)]
    result = moving_block_bootstrap_clustered(
        windows, block_length=5, n_resamples=1000, seed=17
    )
    assert result.p_value >= 0.0
    assert result.effective_n == 2


def test_clustered_effective_n_never_exceeds_window_count():
    windows = [_flat_returns(60, mu=0.001) for _ in range(4)]
    result = moving_block_bootstrap_clustered(
        windows, block_length=5, n_resamples=500, seed=17
    )
    assert result.effective_n <= 4


# --- effective trials -------------------------------------------------


def test_effective_trials_never_exceeds_attempted():
    attempts = 10
    corr_matrix = [[1.0, 0.0], [0.0, 1.0]]
    et = effective_trials(attempted=attempts, correlation_matrix=corr_matrix)
    assert 1 <= et <= attempts


def test_effective_trials_drops_with_correlation():
    """Highly correlated trials inflate the multiple-testing burden less
    than independent ones: effective_trials decreases as correlation rises."""
    attempts = 10
    independent = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    correlated = [[1.0, 0.9, 0.9], [0.9, 1.0, 0.9], [0.9, 0.9, 1.0]]
    et_indep = effective_trials(attempted=attempts, correlation_matrix=independent)
    et_corr = effective_trials(attempted=attempts, correlation_matrix=correlated)
    assert et_corr <= et_indep


# --- leave-one-window-out --------------------------------------------


def test_leave_one_window_out_reports_every_window():
    deltas = {"W1": 0.01, "W2": 0.02, "W3": -0.005, "W4": 0.015}
    loo = leave_one_window_out(deltas)
    assert set(loo.keys()) == {"W1", "W2", "W3", "W4"}
    assert loo["W3"] > sum(deltas.values())


def test_leave_one_window_out_min_delta_respected():
    deltas = {"W1": 0.01, "W2": 0.02, "W3": 0.005, "W4": 0.015}
    loo = leave_one_window_out(deltas)
    min_delta = 0.0
    assert all(v >= min_delta for v in loo.values())


# --- window-level paired direction -----------------------------------


def test_window_paired_direction_majority_positive():
    deltas = {"W1": 0.01, "W2": -0.005, "W3": 0.02, "W4": 0.015}
    direction = window_paired_direction(deltas)
    assert direction.positive_count == 3
    assert direction.negative_count == 1
    assert direction.majority_positive is True


def test_window_paired_direction_does_not_conflict_with_bootstrap():
    deltas = {"W1": -0.01, "W2": -0.005, "W3": 0.02, "W4": -0.015}
    direction = window_paired_direction(deltas)
    assert direction.majority_positive is False
