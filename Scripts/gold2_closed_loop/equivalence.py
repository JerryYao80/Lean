"""Economic-event equivalence comparator for the Gold2 closed-loop proof
(Task 8, Phase 1 STOP gate).

Compares two economic-event sequences (formal trace events OR LEAN-native
result-packet trades normalized to the same shape) and reports whether they
are economically equivalent.

Only the following differences are IGNORED:
  * proof IDs: experiment_id, run_id, candidate_id (and window_id/stage_id
    which are proof-cohort labels, not economic facts)
  * output paths: any ``trace_path`` / ``output_path`` payload field
  * serialization formatting: whitespace, key order, JSON-string vs JSON-number
    decimals that denote the same Decimal value.

Everything else — Decimal quantities, prices, fees, cash, holdings, TPV,
direction (sign semantics), event multiplicity, event order, event time —
is compared EXACTLY. A ~1e-12 ``decimal_tol`` guards only against binary
float representation drift when a value was emitted as a JSON number rather
than a string; it is NOT a tolerance for economic disagreement (3.50 vs 3.51
must fail).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Event types compared by the equivalence check.
_DECISION = "DECISION"
_ORDER_INTENT = "ORDER_INTENT"
_FILL = "FILL"
_HOLDINGS_SNAPSHOT = "HOLDINGS_SNAPSHOT"

# Payload fields that are IGNORED (proof plumbing, not economic facts).
_IGNORED_PAYLOAD_FIELDS = frozenset(
    {
        "trace_path",
        "output_path",
        "results_destination_folder",
    }
)

# Proof-cohort identity fields — ignored for equivalence.
_IGNORED_IDENTITY_FIELDS = frozenset(
    {
        "experiment_id",
        "run_id",
        "candidate_id",
        "window_id",
        "stage_id",
        "schema_version",
    }
)


@dataclass(frozen=True)
class EquivalenceResult:
    """Immutable result of :func:`compare_economic_events`.

    Attributes
    ----------
    equivalent:
        True iff ``mismatches`` is empty.
    mismatches:
        List of human-readable mismatch strings; each cites the event index,
        event_time_utc, and the offending field so a failure is actionable.
    """

    equivalent: bool
    mismatches: list[str] = field(default_factory=list)


def compare_economic_events(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    decimal_tol: float,
    weight_tol: float,
) -> EquivalenceResult:
    """Compare two economic-event lists for behavior-preservation.

    Parameters
    ----------
    left, right:
        Lists of economic-event dicts. Each dict may be either a full
        formal-trace event (with ``schema_version``/``sequence``/... and a
        ``payload``) OR a minimal normalized form ``{"event_type", "time",
        "quantity", "price", ...}`` (the shape ``test_decimal_price_mismatch``
        in the plan contract uses). Both forms are normalized to a canonical
        internal representation.
    decimal_tol:
        Tolerance (~1e-12) guarding only binary-float representation drift
        for values emitted as JSON numbers. Decimal string values are
        compared exactly; a 3.50 vs 3.51 disagreement always fails.
    weight_tol:
        Tolerance reserved for a dedicated weight-typed field. No such field
        exists in the current payloads (DECISION target ``quantity`` is
        compared as an exact Decimal, conservatively); the parameter is kept
        for forward-compatibility and documented here.

    Returns
    -------
    EquivalenceResult
    """
    norm_left = [_normalize_event(e) for e in (left or [])]
    norm_right = [_normalize_event(e) for e in (right or [])]
    mismatches: list[str] = []

    # --- Multiplicity (same count of each event type) --------------------
    left_types = [e["event_type"] for e in norm_left]
    right_types = [e["event_type"] for e in norm_right]
    if len(left_types) != len(right_types):
        mismatches.append(
            f"event count mismatch: left has {len(left_types)} events, "
            f"right has {len(right_types)} events"
        )
        return EquivalenceResult(equivalent=False, mismatches=mismatches)

    # --- Order (same sequence of event types) ----------------------------
    for idx, (lt, rt) in enumerate(zip(left_types, right_types)):
        if lt != rt:
            mismatches.append(
                f"event order mismatch at position {idx}: left event_type "
                f"{lt!r} != right event_type {rt!r}"
            )

    # --- Time (matching event_time_utc, allow equal) ---------------------
    for idx, (le, re) in enumerate(zip(norm_left, norm_right)):
        lt = le.get("time")
        rt = re.get("time")
        if lt is None or rt is None:
            # time is optional in the minimal form; only compare when present.
            continue
        if lt != rt:
            mismatches.append(
                f"event time mismatch at position {idx} ({le.get('event_type')}): "
                f"left {lt!r} != right {rt!r}"
            )

    # --- Per-event payload comparison -------------------------------------
    for idx, (le, re) in enumerate(zip(norm_left, norm_right)):
        lp = le.get("payload", {})
        rp = re.get("payload", {})
        _compare_payload(
            le.get("event_type", "UNKNOWN"),
            le.get("time"),
            lp,
            rp,
            idx,
            decimal_tol,
            mismatches,
        )

    return EquivalenceResult(equivalent=not mismatches, mismatches=mismatches)


def _normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw event dict to the canonical internal form.

    Accepts both:
      * full formal-trace event: {"schema_version", "sequence", "event_type",
        "experiment_id", ..., "event_time_utc", "payload": {...}}
      * minimal form: {"event_type": "FILL", "time": "...", "quantity": "100",
        "price": "3.50"}  (plan contract shape)

    Both are normalized to {"event_type", "time", "payload"} where payload is
    a flat dict of the economic fields. Proof IDs, output paths, and
    formatting are stripped here.
    """
    if not isinstance(ev, dict):
        raise TypeError(f"event must be a dict, got {type(ev).__name__}")

    event_type = ev.get("event_type")
    if event_type is None:
        # Maybe the minimal form uses a different key; treat absence as an
        # equivalence-breaking anomaly surfaced by the caller.
        event_type = "UNKNOWN"

    # Determine the time field. Formal events use event_time_utc; the minimal
    # plan-contract form uses "time".
    time_value = ev.get("event_time_utc") or ev.get("time")

    # Determine the payload. Formal events nest under "payload"; the minimal
    # form flattens economic fields at the top level.
    payload = ev.get("payload")
    if payload is None:
        # Minimal form: take all keys EXCEPT the structural/identity ones as
        # the payload.
        structural = {
            "event_type",
            "time",
            "event_time_utc",
            "sequence",
        } | _IGNORED_IDENTITY_FIELDS
        payload = {
            k: v
            for k, v in ev.items()
            if k not in structural and k not in _IGNORED_PAYLOAD_FIELDS
        }
    elif not isinstance(payload, dict):
        payload = {}

    # Strip ignored payload fields (trace_path, output_path, ...).
    cleaned_payload = {
        k: v for k, v in payload.items() if k not in _IGNORED_PAYLOAD_FIELDS
    }

    return {
        "event_type": event_type,
        "time": time_value,
        "payload": cleaned_payload,
    }


def _compare_payload(
    event_type: str,
    time: Any,
    left: dict[str, Any],
    right: dict[str, Any],
    idx: int,
    decimal_tol: float,
    mismatches: list[str],
) -> None:
    """Compare two cleaned payloads field-by-field.

    Decimal economic fields are compared EXACTLY (modulo decimal_tol against
    binary-float repr drift). Missing keys are a mismatch. Extra keys on one
    side are a mismatch unless they are in the ignored set (already stripped).
    """
    left_keys = set(left.keys())
    right_keys = set(right.keys())
    only_left = left_keys - right_keys
    only_right = right_keys - left_keys
    if only_left:
        mismatches.append(
            f"payload keys only on left at position {idx} ({event_type}, "
            f"time={time}): {sorted(only_left)!r}"
        )
    if only_right:
        mismatches.append(
            f"payload keys only on right at position {idx} ({event_type}, "
            f"time={time}): {sorted(only_right)!r}"
        )

    # Compare shared keys.
    for key in sorted(left_keys & right_keys):
        lv = left[key]
        rv = right[key]
        if _values_equal(key, lv, rv, decimal_tol):
            continue
        mismatches.append(
            f"field {key!r} mismatch at position {idx} ({event_type}, "
            f"time={time}): left={lv!r} != right={rv!r}"
        )


def _values_equal(key: str, left: Any, right: Any, decimal_tol: float) -> bool:
    """Compare two payload values for economic equivalence.

    Strings/numbers that denote the same Decimal value are equal (handles
    JSON-string vs JSON-number formatting drift). Direction (and any other
    string) is compared strictly. Lists/dicts recurse. bool is strict.
    """
    # bool first (bool is a subclass of int).
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right and isinstance(left, bool) == isinstance(right, bool)

    # Numeric (int/float): compare as Decimal-exact when both are numeric
    # OR when one is numeric and the other is a decimal string.
    ln = _maybe_decimal(left)
    rn = _maybe_decimal(right)
    if ln is not None and rn is not None:
        # Both denote a number. Compare with a tiny tolerance that ONLY
        # guards binary-float repr drift (e.g. 3.5 emitted as 3.5 vs 3.5000).
        # A genuine economic disagreement (3.50 vs 3.51) is far larger than
        # decimal_tol (~1e-12) and therefore fails.
        diff = abs(ln - rn)
        if diff == Decimal("0"):
            return True
        return diff <= Decimal(str(decimal_tol))

    # Lists: compare element-wise.
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        return all(
            _values_equal(f"{key}[{i}]", a, b, decimal_tol)
            for i, (a, b) in enumerate(zip(left, right))
        )

    # Dicts: recurse (after stripping ignored keys upstream).
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left.keys()) != set(right.keys()):
            return False
        return all(
            _values_equal(f"{key}.{k}", left[k], right[k], decimal_tol)
            for k in left.keys()
        )

    # Strings (including direction): strict equality. Direction affects sign
    # semantics, so "Buy" != "Sell".
    return left == right


def _maybe_decimal(value: Any) -> Decimal | None:
    """Coerce a value to Decimal if it denotes a number, else None.

    Accepts int, float, and decimal-valued strings. Rejects strings that
    are not decimal (e.g. "Buy", "Filled") by returning None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Use str() to preserve the JSON float's repr as closely as possible.
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    return None
