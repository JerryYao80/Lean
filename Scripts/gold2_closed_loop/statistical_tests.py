"""Statistical inference for the Gold2 closed-loop proof (Task 16, Phase 4).

Implements the design §12 / §13 statistical contract:

* Within-window PAIRED moving-block bootstrap on daily excess returns
  (seeded, deterministic given the seed). The moving-block bootstrap
  resamples contiguous blocks (not individual points) to preserve the
  short-range autocorrelation of daily returns.
* Aggregate inference CLUSTERS BY ANNUAL BLIND WINDOW (spec §12 line 379:
  "聚合推断以年度盲测窗口为 cluster，不把池化日收益当作独立重复实验"). The
  clustered bootstrap resamples WINDOWS (with replacement), each
  contributing its within-window block-bootstrap mean; it never pools
  daily returns across windows as if independent.
* Effective trials: computed from the full attempted-trial count and the
  return-correlation matrix via a preregistered conservative formula
  (spec §12 line 383). Conservative = the effective trial count is the
  eigenvalue-based "number of independent dimensions" of the correlation
  matrix, floored at 1 and capped at the attempted count.
* Leave-one-window-out: reported for every window (spec §12 line 380).
* Window-level paired direction: the count of positive vs negative
  per-window deltas; must not conflict with the bootstrap verdict (spec
  §12 line 384).

All randomness is seeded (``random.Random(seed)``) so the bootstrap is
deterministic and reproducible. This module is PROOF-ONLY and does NOT
reconstruct accounting.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BootstrapResult:
    """Result of a (within-window or clustered) moving-block bootstrap.

    Fields
    ------
    mean_estimate:
        The point estimate of the mean excess return (the mean of the
        original series, or the mean of within-window means for the
        clustered form).
    p_value:
        Two-sided bootstrap p-value for the null that the mean is zero.
    ci_low / ci_high:
        The (1 - alpha) confidence interval on the mean (alpha = 1 -
        ci_level, default ci_level 0.95).
    effective_n:
        The number of independent units the inference is based on (the
        series length for within-window; the window count for clustered).
    """

    mean_estimate: float
    p_value: float
    ci_low: float
    ci_high: float
    effective_n: int


@dataclass(frozen=True)
class PairedDirection:
    """Window-level paired direction (spec §12 line 380)."""

    positive_count: int
    negative_count: int
    zero_count: int
    majority_positive: bool


# ---------------------------------------------------------------------
# Within-window moving-block bootstrap
# ---------------------------------------------------------------------


def moving_block_bootstrap(
    returns: Sequence[float],
    *,
    block_length: int,
    n_resamples: int,
    seed: int,
    ci_level: float = 0.95,
) -> BootstrapResult:
    """Paired moving-block bootstrap on a within-window excess-return series.

    Resamples ``block_length``-day contiguous blocks (with replacement) to
    form ``n_resamples`` bootstrap series of the same length as the input,
    computes each resample's mean, and derives the two-sided p-value and
    the percentile confidence interval.

    The bootstrap is SEEDED (``random.Random(seed)``): identical inputs +
    identical seed -> identical output (deterministic, reproducible).
    """
    n = len(returns)
    if n == 0:
        raise ValueError("moving_block_bootstrap: returns must be non-empty")
    if block_length < 1:
        raise ValueError(f"block_length must be >= 1, got {block_length}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1, got {n_resamples}")
    if not (0.0 < ci_level < 1.0):
        raise ValueError(f"ci_level must be in (0, 1), got {ci_level}")
    rng = random.Random(seed)
    mean_estimate = sum(returns) / n
    if n == 1:
        # Degenerate: no resampling possible; CI collapses to the point.
        return BootstrapResult(
            mean_estimate=mean_estimate,
            p_value=1.0 if mean_estimate == 0.0 else 0.0,
            ci_low=mean_estimate,
            ci_high=mean_estimate,
            effective_n=1,
        )
    # Number of blocks per resample (ceil so a resample covers the series).
    n_blocks = math.ceil(n / block_length)
    resample_means: list[float] = []
    for _ in range(n_resamples):
        total = 0.0
        count = 0
        for _ in range(n_blocks):
            if block_length >= n:
                start = 0
                end = n
            else:
                start = rng.randint(0, n - block_length)
                end = start + block_length
            for v in returns[start:end]:
                total += v
                count += 1
        resample_means.append(total / count if count else 0.0)
    # Two-sided p-value: fraction of resample means at least as extreme
    # (in absolute value) as the observed mean, under the null-centered
    # distribution. Center the resample means on zero (null) by subtracting
    # the observed mean, then count how many exceed |observed|.
    abs_observed = abs(mean_estimate)
    extreme = sum(1 for m in resample_means if abs(m - mean_estimate) >= abs_observed)
    p_value = extreme / n_resamples
    sorted_means = sorted(resample_means)
    alpha = 1.0 - ci_level
    lo_idx = max(0, math.floor((alpha / 2.0) * (n_resamples - 1)))
    hi_idx = min(n_resamples - 1, math.ceil((1.0 - alpha / 2.0) * (n_resamples - 1)))
    ci_low = sorted_means[lo_idx]
    ci_high = sorted_means[hi_idx]
    return BootstrapResult(
        mean_estimate=mean_estimate,
        p_value=p_value,
        ci_low=ci_low,
        ci_high=ci_high,
        effective_n=n,
    )


# ---------------------------------------------------------------------
# Clustered aggregate inference (by annual window)
# ---------------------------------------------------------------------


def moving_block_bootstrap_clustered(
    windows: Sequence[Sequence[float]],
    *,
    block_length: int,
    n_resamples: int,
    seed: int,
    ci_level: float = 0.95,
) -> BootstrapResult:
    """Clustered bootstrap: resample WINDOWS (with replacement), each
    contributing its within-window mean.

    This is the §12 line 379 aggregate-inference form: the cluster is the
    ANNUAL BLIND WINDOW, NOT the pooled daily return. Pooled daily returns
    are NEVER treated as independent repetitions. The effective_n is the
    number of windows (clusters), capped at the window count.

    Each resample draws ``len(windows)`` windows with replacement and
    averages their within-window means; the bootstrap p-value and CI are
    derived from that resample distribution.
    """
    n_windows = len(windows)
    if n_windows == 0:
        raise ValueError("moving_block_bootstrap_clustered: windows must be non-empty")
    rng = random.Random(seed)
    within_means = [sum(w) / len(w) for w in windows if len(w) > 0]
    if not within_means:
        raise ValueError("every window must be non-empty")
    overall_mean = sum(within_means) / len(within_means)
    resample_means: list[float] = []
    for _ in range(n_resamples):
        picks = [within_means[rng.randrange(len(within_means))]
                 for _ in range(len(within_means))]
        resample_means.append(sum(picks) / len(picks))
    abs_observed = abs(overall_mean)
    extreme = sum(1 for m in resample_means
                  if abs(m - overall_mean) >= abs_observed)
    p_value = extreme / n_resamples
    sorted_means = sorted(resample_means)
    alpha = 1.0 - ci_level
    lo_idx = max(0, math.floor((alpha / 2.0) * (n_resamples - 1)))
    hi_idx = min(n_resamples - 1, math.ceil((1.0 - alpha / 2.0) * (n_resamples - 1)))
    return BootstrapResult(
        mean_estimate=overall_mean,
        p_value=p_value,
        ci_low=sorted_means[lo_idx],
        ci_high=sorted_means[hi_idx],
        effective_n=n_windows,
    )


# ---------------------------------------------------------------------
# Effective trials (from the return-correlation matrix)
# ---------------------------------------------------------------------


def effective_trials(
    attempted: int, correlation_matrix: Sequence[Sequence[float]]
) -> float:
    """Conservative effective-trial count from a return-correlation matrix.

    Uses the eigenvalue-based "number of independent dimensions": the sum
    of the eigenvalues squared over the sum of the eigenvalues (a form of
    the effective rank). This is conservative: highly correlated trials
    (one large eigenvalue) collapse toward 1; uncorrelated trials
    (equal eigenvalues) approach the matrix dimension. Floored at 1 and
    capped at ``attempted`` (spec §12 line 383: "预注册保守公式").
    """
    if attempted < 1:
        raise ValueError(f"attempted must be >= 1, got {attempted}")
    m = len(correlation_matrix)
    if m == 0:
        return 1.0
    eigs = _symmetric_eigenvalues(correlation_matrix)
    sum_eig = sum(eigs)
    if sum_eig <= 0:
        return 1.0
    sum_sq = sum(e * e for e in eigs)
    if sum_sq <= 0:
        return 1.0
    eff = (sum_eig * sum_eig) / sum_sq
    eff = min(eff, float(m), float(attempted))
    return max(1.0, eff)


def _symmetric_eigenvalues(matrix: Sequence[Sequence[float]]) -> list[float]:
    """Eigenvalues of a symmetric matrix via the Jacobi rotation method.

    A small, dependency-free implementation (no numpy required) suitable
    for the small correlation matrices this proof produces (a handful of
    trials). For a non-symmetric or non-square input, raises ValueError.
    """
    n = len(matrix)
    a = [[float(matrix[i][j]) for j in range(n)] for i in range(n)]
    for i in range(n):
        if len(a[i]) != n:
            raise ValueError("correlation matrix must be square")
        for j in range(i + 1, n):
            if abs(a[i][j] - a[j][i]) > 1e-9:
                raise ValueError("correlation matrix must be symmetric")
    eig = [a[i][i] for i in range(n)]
    off = 1.0
    sweeps = 0
    max_sweeps = 100
    while off > 1e-12 and sweeps < max_sweeps:
        off = 0.0
        for p in range(n):
            for q in range(p + 1, n):
                apq = a[p][q]
                if abs(apq) < 1e-15:
                    continue
                off += apq * apq
                app = eig[p]
                aqq = eig[q]
                phi = 0.5 * math.atan2(2.0 * apq, aqq - app)
                c = math.cos(phi)
                s = math.sin(phi)
                eig[p] = app * c * c + aqq * s * s + 2.0 * apq * s * c
                eig[q] = app * s * s + aqq * c * c - 2.0 * apq * s * c
                a[p][q] = 0.0
                a[q][p] = 0.0
                for i in range(n):
                    if i != p and i != q:
                        aip = a[i][p]
                        aiq = a[i][q]
                        a[i][p] = c * aip - s * aiq
                        a[p][i] = a[i][p]
                        a[i][q] = s * aip + c * aiq
                        a[q][i] = a[i][q]
        sweeps += 1
    return eig


# ---------------------------------------------------------------------
# Leave-one-window-out + paired direction
# ---------------------------------------------------------------------


def leave_one_window_out(deltas: dict[str, float]) -> dict[str, float]:
    """Return ``{window: aggregate_delta_with_that_window_dropped}``.

    Each value is the sum of all deltas EXCEPT the named window. Spec §12
    line 380: report every leave-one-window-out. The caller checks each
    against ``leave_one_window_out_min_delta``.
    """
    total = sum(deltas.values())
    return {window: total - delta for window, delta in deltas.items()}


def window_paired_direction(deltas: dict[str, float]) -> PairedDirection:
    """Count positive / negative / zero per-window deltas (spec §12 line 380)."""
    positive = sum(1 for v in deltas.values() if v > 0)
    negative = sum(1 for v in deltas.values() if v < 0)
    zero = sum(1 for v in deltas.values() if v == 0)
    return PairedDirection(
        positive_count=positive,
        negative_count=negative,
        zero_count=zero,
        majority_positive=positive > negative,
    )


__all__ = [
    "BootstrapResult",
    "PairedDirection",
    "effective_trials",
    "leave_one_window_out",
    "moving_block_bootstrap",
    "moving_block_bootstrap_clustered",
    "window_paired_direction",
]
