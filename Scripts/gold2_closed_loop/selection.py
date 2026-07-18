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

import math
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

# Metric keys the gates / ranking / dominance logic actually read. A
# runner-returned result with a non-finite value (NaN / +Inf / -Inf) in
# ANY of these is unusable evidence — the result is INVALID, not a gate
# failure (see ``_has_non_finite_metrics`` and ``classify_outcome``).
# ``subwindow_sharpes`` is a list; its entries are scanned recursively.
_FINITE_CHECK_METRICS: tuple[str, ...] = (
    "sharpe",
    "net_profit",
    "mdd",
    "trades",
    "dsr",
    "subwindow_sharpes",
)


def _has_non_finite_metrics(attempt: Attempt) -> bool:
    """Return True if any gate/ranking metric is NaN or ±Infinity.

    Scans the metric keys the gates and ranking actually read
    (``sharpe`` / ``net_profit`` / ``mdd`` / ``trades`` / ``dsr`` /
    ``subwindow_sharpes``). For ``subwindow_sharpes`` (a list), each
    entry is scanned; a non-finite value buried in the list is still
    INVALID — a single NaN subwindow sharpe makes the spread
    ``max - min`` NaN, poisoning the stability gate.

    Returns ``True`` if ANY required metric value is non-finite. A
    MISSING key (``None``) is NOT non-finite — absence is the gate's
    problem (PRUNED for missing trades / mdd, defaulted for dsr), not
    INVALID. Only a PRESENT numeric value that fails ``math.isfinite``
    is INVALID.
    """
    metrics = attempt.metrics or {}
    for key in _FINITE_CHECK_METRICS:
        raw = metrics.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            # One level of recursion: subwindow_sharpes is a flat list of
            # floats. A nested list here would be a schema violation
            # elsewhere; we still scan one level deep for robustness.
            for item in raw:
                if _is_non_finite_scalar(item):
                    return True
            continue
        if _is_non_finite_scalar(raw):
            return True
    return False


def _is_non_finite_scalar(value: Any) -> bool:
    """True iff ``value`` is a non-integer, non-bool number that is NaN
    or ±Infinity.

    ``math.isfinite`` rejects NaN / ±Infinity for floats but raises
    ``TypeError`` on non-numbers (str / None / dict). Bools are ints in
    python (``isinstance(True, int)`` is True) and are finite, so we
    accept them. Strings, dicts, and other non-numerics are NOT
    non-finite (they are a different kind of bad data; the gate /
    schema handles them).
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return False
    if isinstance(value, float):
        return not math.isfinite(value)
    # A string / None / dict / etc. is not a non-finite NUMBER. It may
    # be wrong, but it is not NaN/Inf; leave that to the gates / schema.
    return False


def _metrics_unusable(metrics: dict[str, Any]) -> bool:
    """Return True if the metrics dict is unusable evidence — i.e. it
    contains a value that is EITHER a non-finite float (NaN/±Inf) OR a
    non-numeric scalar (str/None/dict) in ANY position, OR a non-numeric
    entry inside a list value.

    This is the BROADER unusable-evidence check (defects 1 + 3 from the
    Task 10 code-quality review). ``_has_non_finite_metrics`` only scans
    the 6 gate/ranking keys for NaN/Inf floats; a non-numeric value
    (e.g. ``mdd="xx"``, ``trades=[]``, ``sortino=NaN`` in a non-gate key)
    sails past it, reaches ``_passes_gate`` (which calls bare ``float()``
    and raises) or ``record_outcome`` (whose schema finite-check rejects
    it), and crashes AFTER ``register()`` but BEFORE ``record_outcome()``
    — leaving a dangling PENDING record that corrupts the hash-chained
    audit trail. It scans the WHOLE metrics tree (not just the 6 keys)
    because a non-finite value in any key fails the candidate-event
    schema's recursive finite-check at persist time regardless of whether
    a gate reads it.

    Treating such results as INVALID (metrics=None, never selected, still
    consuming budget and still closed by a terminal event) keeps the
    journal intact: the candidate is registered, then closed with a
    terminal INVALID event, with no crash in between.
    """
    return _any_unusable(metrics)


def _any_unusable(value: Any) -> bool:
    """Recursive helper: True if ``value`` or any nested value is a
    non-finite float or a non-numeric scalar in a scalar position."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return False
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if _any_unusable(item):
                return True
        return False
    if isinstance(value, dict):
        for item in value.values():
            if _any_unusable(item):
                return True
        return False
    # str / None / anything else in a scalar metric position is unusable:
    # the gates call float() on it and would raise.
    return True


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
    INVALID / DUPLICATE / SUCCEEDED / DOMINATED / PRUNED / REJECTED.

    Order: INVALID FIRST (non-finite metrics), then DUPLICATE (canonical
    hash of params), then gates (PRUNED/REJECTED), then dominance
    (DOMINATED), else SUCCEEDED. INVALID precedes DUPLICATE so a result
    whose metrics are non-finite is recorded INVALID regardless of
    whether its parameters duplicate an earlier accepted candidate —
    the result is unusable evidence either way, and recording it INVALID
    (not DUPLICATE) makes the unusable-result outcome visible in the
    journal. An INVALID result is never re-run as a candidate: it
    consumed budget and the parameters are still hashed into the dedup
    set by the optimizer's loop.

    INVALID reason: a runner-returned result with NaN or ±Infinity in
    any gate/ranking metric (sharpe / net_profit / mdd / trades / dsr /
    subwindow_sharpes entries) is unusable — the gates and ranking
    cannot compare a NaN sharpe. This is NOT a gate failure (PRUNED) —
    the gates never ran. The execution_status stays SUCCEEDED (the
    runner DID return a result); event_type is INVALID (the result is
    unusable). Matches the plan's "invalid attempt consumes budget"
    requirement (plan Step 3, line 542).

    Gates failure reason mapping:
      min_trades / max_mdd / min_dsr / parameter-bounds  -> PRUNED
      subwindow_stability                                -> REJECTED
    """
    # INVALID FIRST: unusable metrics (non-finite float OR non-numeric
    # scalar in any position) are unusable evidence, not a gate failure.
    # The runner returned a result, but the metrics contain a value the
    # gates (``float()``) or the schema (recursive finite-check) cannot
    # accept; we cannot compare, rank, or persist them. This takes
    # precedence over DUPLICATE so a non-numeric/non-finite result is
    # recorded INVALID even if its params match an earlier accepted
    # candidate. The broader ``_metrics_unusable`` scan covers BOTH the
    # 6 gate/ranking keys (defect 1: ``mdd="xx"``) AND non-gate keys
    # (defect 3: ``sortino=NaN``), so a non-finite value anywhere in the
    # metrics tree is INVALID before any ``float()`` call can crash.
    if _metrics_unusable(attempt.metrics or {}):
        return "INVALID"
    # Duplicate check against already-accepted.
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
