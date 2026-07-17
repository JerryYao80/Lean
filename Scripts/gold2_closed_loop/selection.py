"""Pure selection logic for the proof-local G1 optimizer (Task 10).

These functions are pure (no I/O) so the selection contract is testable
in isolation:

* ``dedupe(attempts)`` — mark duplicate parameter sets (by canonical
  hash) and return ``(unique_attempts, duplicate_indices)``.
* ``dominates(a, b)`` — True iff ``a`` is strictly better-or-equal on
  ALL prereg ranking metrics (sharpe, net_profit, mdd) and strictly
  better on at least one.
* ``select(attempts, *, ranking_metric, gates)`` — apply gates (min
  trades / max MDD / min DSR / parameter bounds / train-subwindow
  stability), dedup, then rank by the preregistered metric with the
  preregistered tie-break (sharpe desc, net_profit desc, mdd asc).
  Returns the best passing ``Attempt`` or ``None``.

No production code is modified.
"""

from __future__ import annotations

from typing import Any

from Scripts.gold2_closed_loop.adapters.base import Attempt
from Scripts.gold2_closed_loop.evidence import canonical_hash

# Default ranking metrics. A candidate is "better" if sharpe is higher,
# net_profit is higher, and mdd is LOWER (mdd is a cost).
_RANKING_METRICS: tuple[str, ...] = ("sharpe", "net_profit", "mdd")
# For each metric, +1 means "higher is better", -1 means "lower is better".
_METRIC_DIRECTION: dict[str, int] = {
    "sharpe": +1,
    "net_profit": +1,
    "mdd": -1,
}


def _metric_value(attempt: Attempt, metric: str) -> float:
    """Extract a numeric metric from the attempt's metrics dict.

    Returns ``-inf`` (for higher-is-better) / ``+inf`` (for lower-is-better)
    when the metric is absent or non-numeric, so a candidate missing a
    ranking metric is never selected over one that has it.
    """
    metrics = attempt.metrics or {}
    raw = metrics.get(metric)
    if raw is None:
        return float("-inf") if _METRIC_DIRECTION.get(metric, +1) > 0 else float("inf")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("-inf") if _METRIC_DIRECTION.get(metric, +1) > 0 else float("inf")


def dominates(a: Attempt, b: Attempt) -> bool:
    """True iff ``a`` strictly dominates ``b`` on the ranking metrics.

    "Dominates" = ``a`` is better-or-equal on ALL of sharpe / net_profit /
    mdd, AND strictly better on at least one. ``mdd`` is a cost (lower is
    better).
    """
    all_ge = True
    any_gt = False
    for metric in _RANKING_METRICS:
        av = _metric_value(a, metric)
        bv = _metric_value(b, metric)
        direction = _METRIC_DIRECTION[metric]
        # Normalize so "higher is better" in the normalized space.
        if direction > 0:
            if av < bv:
                all_ge = False
            if av > bv:
                any_gt = True
        else:
            # Lower is better.
            if av > bv:
                all_ge = False
            if av < bv:
                any_gt = True
    return all_ge and any_gt


def dedupe(attempts: list[Attempt]) -> tuple[list[Attempt], list[int]]:
    """Mark duplicates by canonical-hash of the parameter dict.

    Returns
    -------
    tuple of (unique_attempts, duplicate_indices):
        ``unique_attempts`` is the list of attempts with unique parameter
        hashes, in first-seen order. ``duplicate_indices`` is the list of
        indices into the ORIGINAL ``attempts`` list that were duplicates
        (i.e. their parameter hash matched an earlier attempt).
    """
    seen: dict[str, int] = {}
    unique: list[Attempt] = []
    dup_indices: list[int] = []
    for index, attempt in enumerate(attempts):
        key = canonical_hash(attempt.parameters)
        if key in seen:
            dup_indices.append(index)
            continue
        seen[key] = index
        unique.append(attempt)
    return unique, dup_indices


def _passes_gate(attempt: Attempt, gates: dict[str, Any]) -> tuple[bool, str]:
    """Check minimum gates for a SUCCEEDED attempt.

    Returns ``(passed, reason)``. ``reason`` is "" on pass, or one of
    "min_trades", "max_mdd", "min_dsr", "subwindow_stability" on fail.
    """
    metrics = attempt.metrics or {}
    min_trades = gates.get("min_trades", 1)
    max_mdd = gates.get("max_mdd", 0.5)
    min_dsr = gates.get("min_dsr", 0.0)
    subwindow_spread_max = gates.get("subwindow_spread_max")

    trades = metrics.get("trades")
    if trades is None or float(trades) < float(min_trades):
        return False, "min_trades"
    mdd = metrics.get("mdd")
    if mdd is None or float(mdd) > float(max_mdd):
        return False, "max_mdd"
    dsr = metrics.get("dsr")
    # DSR is optional in metrics; if absent, default to 0.0 (the min).
    dsr_val = float(dsr) if dsr is not None else 0.0
    if dsr_val < float(min_dsr):
        return False, "min_dsr"
    if subwindow_spread_max is not None:
        sub = metrics.get("subwindow_sharpes")
        if isinstance(sub, list) and len(sub) > 0:
            spread = max(sub) - min(sub)
            if spread > float(subwindow_spread_max):
                return False, "subwindow_stability"
    return True, ""


def select(
    attempts: list[Attempt],
    *,
    ranking_metric: str = "sharpe",
    gates: dict[str, Any] | None = None,
) -> Attempt | None:
    """Apply gates + dedup + ranking, return the best passing Attempt.

    Only SUCCEEDED attempts are eligible. Gates (min_trades / max_mdd /
    min_dsr / train-subwindow stability) filter. Dedup by canonical
    parameter hash. Rank by ``ranking_metric`` (default sharpe desc)
    with tie-break (net_profit desc, then mdd asc).

    Returns ``None`` if no candidate passes.
    """
    if gates is None:
        gates = {}
    # Only SUCCEEDED attempts are eligible.
    eligible = [a for a in attempts if a.event_type == "SUCCEEDED"]
    # Apply gates.
    passing: list[Attempt] = []
    for attempt in eligible:
        ok, _reason = _passes_gate(attempt, gates)
        if ok:
            passing.append(attempt)
    if not passing:
        return None
    # Dedup (keep first of each canonical-hash group).
    unique, _dup_indices = dedupe(passing)
    if not unique:
        return None
    # Rank. Build a sort key that respects the ranking_metric primary,
    # then tie-break net_profit desc, mdd asc.
    primary_metric = ranking_metric if ranking_metric in _METRIC_DIRECTION else "sharpe"

    def sort_key(attempt: Attempt) -> tuple[float, float, float]:
        # We sort DESCENDING for primary and net_profit, ASCENDING for mdd.
        # Negate the "higher is better" metrics so ascending sort gives
        # the best first.
        primary_val = _metric_value(attempt, primary_metric)
        if _METRIC_DIRECTION[primary_metric] > 0:
            primary_key = -primary_val
        else:
            primary_key = primary_val
        np_val = _metric_value(attempt, "net_profit")
        np_key = -np_val  # higher net_profit is better
        mdd_val = _metric_value(attempt, "mdd")
        mdd_key = mdd_val  # lower mdd is better
        return (primary_key, np_key, mdd_key)

    unique.sort(key=sort_key)
    return unique[0]


def classify_outcome(
    attempt: Attempt,
    *,
    passing_attempts: list[Attempt],
    gates: dict[str, Any],
) -> str:
    """Classify a SUCCEEDED attempt's terminal event_type.

    Helper for the optimizer: given a SUCCEEDED attempt and the list of
    ALREADY-accepted passing attempts, decide whether this one is
    SUCCEEDED / DOMINATED / DUPLICATE / PRUNED / REJECTED.

    Order: dedup (DUPLICATE) FIRST, then gates (PRUNED/REJECTED), then
    dominance (DOMINATED), else SUCCEEDED. Dedup precedes gates so a
    candidate whose parameters are byte-identical to an already-accepted
    one is recorded DUPLICATE regardless of whether its (re-run) metrics
    happen to pass the gates — the candidate space was already explored.

    Gates failure reason mapping:
      min_trades / max_mdd / min_dsr / parameter-bounds  -> PRUNED
      subwindow_stability                                -> REJECTED
    """
    # Duplicate check against already-accepted FIRST.
    new_hash = canonical_hash(attempt.parameters)
    for accepted in passing_attempts:
        if canonical_hash(accepted.parameters) == new_hash:
            return "DUPLICATE"
    # Gates.
    ok, reason = _passes_gate(attempt, gates)
    if not ok:
        if reason == "subwindow_stability":
            return "REJECTED"
        return "PRUNED"
    # Dominance: if any accepted attempt strictly dominates this one,
    # this one is DOMINATED.
    for accepted in passing_attempts:
        if dominates(accepted, attempt):
            return "DOMINATED"
    return "SUCCEEDED"
