"""Strict Python loader for the C# formal trace JSONL (Task 8, Phase 1 STOP gate).

Reads the JSONL written by ``FormalJsonlTraceSink`` (C#
Algorithm.CSharp/Gold2ClosedLoop/FormalJsonlTraceSink.cs) and replicates the
order/fill/holdings reconciliation invariants of ``FormalTraceReconciler``
(Algorithm.CSharp/Gold2ClosedLoop/FormalTraceReconciler.cs) so that a
Python-side STOP gate can reject a broken trace deterministically without
re-running the C# engine.

JSONL line schema (snake_case, emitted by FormalTraceEvent.cs
``[JsonProperty(PropertyName=...)]``):
    schema_version   : str (required)
    sequence         : int (1-based, strictly increasing)
    event_type       : str in {DECISION, ORDER_INTENT, FILL, HOLDINGS_SNAPSHOT}
    experiment_id    : str (required, non-empty)
    window_id        : str (required, non-empty)
    stage_id         : str (required, non-empty)
    run_id           : str (required, non-empty)
    candidate_id     : str (required, non-empty)
    event_time_utc   : str ISO 8601 UTC (required)
    payload          : object (required for correlation-bearing types)

Payloads (mirrors TracingExecutionModel.cs emission):
    DECISION          : { "targets": [ {symbol, quantity}, ... ] }
    ORDER_INTENT      : { orderId, symbol, quantity }
    FILL              : { orderId, symbol, fillPrice, fillQuantity, fee,
                          feeCurrency, status, direction }
    HOLDINGS_SNAPSHOT : { orderId, symbol, quantity, averagePrice, price,
                          cash, totalPortfolioValue }
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Closed enum of event types recognized by the reconciler. Matches the C#
# FormalTraceReconciler constants.
_DECISION = "DECISION"
_ORDER_INTENT = "ORDER_INTENT"
_FILL = "FILL"
_HOLDINGS_SNAPSHOT = "HOLDINGS_SNAPSHOT"

_KNOWN_EVENT_TYPES = frozenset({_DECISION, _ORDER_INTENT, _FILL, _HOLDINGS_SNAPSHOT})

# Identity tuple fields — all must be present and non-empty.
_IDENTITY_FIELDS = (
    "experiment_id",
    "window_id",
    "stage_id",
    "run_id",
    "candidate_id",
)

# Top-level required fields for every formal event.
_REQUIRED_FIELDS = (
    "schema_version",
    "sequence",
    "event_type",
    "event_time_utc",
    "payload",
) + _IDENTITY_FIELDS


@dataclass(frozen=True)
class FormalEvent:
    """Immutable Python mirror of the C# ``FormalTraceEvent`` DTO.

    ``payload`` is stored as the parsed JSON object (a plain dict). Numeric
    values that were emitted as JSON numbers (rather than strings) are kept
    as-is; callers that need exact Decimal semantics should normalize via
    ``_payload_decimal`` in :mod:`equivalence`.
    """

    schema_version: str
    sequence: int
    event_type: str
    experiment_id: str
    window_id: str
    stage_id: str
    run_id: str
    candidate_id: str
    event_time_utc: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FormalEvent":
        kwargs: dict[str, Any] = {}
        for field_name in (
            "schema_version",
            "sequence",
            "event_type",
            "experiment_id",
            "window_id",
            "stage_id",
            "run_id",
            "candidate_id",
            "event_time_utc",
        ):
            if field_name not in raw:
                raise ValueError(
                    f"formal event is missing required field {field_name!r}"
                )
            kwargs[field_name] = raw[field_name]
        kwargs["sequence"] = int(kwargs["sequence"])
        kwargs["payload"] = raw.get("payload") or {}
        return cls(**kwargs)


def load_trace(path: Path | str) -> list[FormalEvent]:
    """Read a formal trace JSONL file and parse each line into a FormalEvent.

    Raises
    ------
    FileNotFoundError:
        If ``path`` does not exist.
    ValueError:
        If a line is malformed JSON or missing a required field, with an
        actionable message that includes the 1-based line number.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"formal trace file not found: {p}")

    events: list[FormalEvent] = []
    for lineno, raw_line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"malformed JSON at {p}:{lineno}: {error.msg}"
            ) from error
        if not isinstance(obj, dict):
            raise ValueError(
                f"formal trace line {lineno} is not a JSON object: {stripped[:120]!r}"
            )
        for field_name in _REQUIRED_FIELDS:
            if field_name not in obj:
                raise ValueError(
                    f"formal trace line {lineno} is missing required field "
                    f"{field_name!r}"
                )
        events.append(FormalEvent.from_dict(obj))
    return events


def validate_formal_events(events: list[dict[str, Any] | FormalEvent]) -> None:
    """Validate a sequence of formal trace events.

    Replicates the C# ``FormalTraceReconciler.Reconcile`` invariants plus the
    schema/identity/time/finite-value checks the C# sink assumes. Raises
    ``ValueError`` with an actionable message (including sequence + orderId
    when relevant) on the FIRST violation; returns None on success.

    Rules
    -----
    * Every event must carry ``schema_version`` and a known ``event_type``
      from the closed enum. An event WITHOUT ``event_type`` /
      ``schema_version`` (e.g. the mature Gold2 RL-state dict
      ``{"time","tpv"}``) is rejected as an "unknown formal event".
    * Full identity tuple (experiment/window/stage/run/candidate id) must be
      present and non-empty.
    * ``sequence`` is strictly increasing starting at 1, no gaps/duplicates.
    * ``event_time_utc`` is parseable ISO 8601 UTC and nondecreasing
      (equal allowed).
    * All numeric payload values are finite (reject NaN/Inf).
    * Reconciliation (intent-before-fill, snapshot-after-fill, strict
      one-to-one per orderId via a per-orderId pending queue): every FILL
      references an earlier ORDER_INTENT with matching integer orderId;
      every FILL has a following correlated HOLDINGS_SNAPSHOT with the same
      orderId; no spurious snapshots, no wrong orderId, no duplicate open
      intent.
    """
    if events is None:
        raise ValueError("events list is None")

    normalized: list[FormalEvent] = []
    for idx, ev in enumerate(events):
        if isinstance(ev, FormalEvent):
            normalized.append(ev)
            continue
        if not isinstance(ev, dict):
            raise ValueError(
                f"event at index {idx} is not a dict (got {type(ev).__name__})"
            )
        # Unknown formal event: missing event_type or schema_version entirely.
        # This catches the RL-state dict {"time","tpv"} which has neither.
        if "event_type" not in ev or "schema_version" not in ev:
            raise ValueError(
                f"unknown formal event at index {idx}: missing event_type / "
                f"schema_version (event keys={sorted(ev.keys())!r})"
            )
        try:
            normalized.append(FormalEvent.from_dict(ev))
        except ValueError as error:
            # from_dict raises ValueError for any missing identity field;
            # re-raise with the index so the failure is actionable.
            raise ValueError(
                f"event at index {idx}: {error}"
            ) from error

    if not normalized:
        return

    _validate_schema_and_identity(normalized)
    _validate_sequence(normalized)
    _validate_time(normalized)
    _validate_payload_finite(normalized)
    _reconcile(normalized)


def _validate_schema_and_identity(events: list[FormalEvent]) -> None:
    for ev in events:
        if ev.event_type not in _KNOWN_EVENT_TYPES:
            raise ValueError(
                f"unknown formal event type {ev.event_type!r} at sequence "
                f"{ev.sequence}; expected one of {sorted(_KNOWN_EVENT_TYPES)}"
            )
        if not ev.schema_version:
            raise ValueError(
                f"schema_version is empty at sequence {ev.sequence}"
            )
        for name in _IDENTITY_FIELDS:
            value = getattr(ev, name)
            if not value or not str(value).strip():
                raise ValueError(
                    f"{name} is empty at sequence {ev.sequence}"
                )
        if not isinstance(ev.payload, dict):
            raise ValueError(
                f"payload must be a JSON object at sequence {ev.sequence} "
                f"(got {type(ev.payload).__name__})"
            )


def _validate_sequence(events: list[FormalEvent]) -> None:
    expected = 1
    seen: set[int] = set()
    for ev in events:
        if ev.sequence <= 0:
            raise ValueError(
                f"formal trace sequence must be strictly positive; got "
                f"{ev.sequence}"
            )
        if ev.sequence != expected:
            if ev.sequence in seen:
                raise ValueError(
                    f"duplicate formal trace sequence {ev.sequence}; "
                    f"sequences must be strictly unique"
                )
            raise ValueError(
                f"formal trace sequence out of order: expected {expected} "
                f"but received {ev.sequence}"
            )
        seen.add(ev.sequence)
        expected += 1


def _validate_time(events: list[FormalEvent]) -> None:
    prev_dt: datetime | None = None
    for ev in events:
        try:
            dt = _parse_utc(ev.event_time_utc)
        except ValueError as error:
            raise ValueError(
                f"event_time_utc at sequence {ev.sequence} is not parseable "
                f"ISO 8601 UTC: {ev.event_time_utc!r} ({error})"
            ) from error
        if prev_dt is not None and dt < prev_dt:
            raise ValueError(
                f"event_time_utc at sequence {ev.sequence} ({ev.event_time_utc}) "
                f"is before the previous event time; times must be nondecreasing"
            )
        prev_dt = dt


def _parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 UTC timestamp, accepting a trailing Z."""
    if not isinstance(value, str):
        raise ValueError(f"not a string: {value!r}")
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    # Python's fromisoformat (3.11+) accepts a trailing 'Z'; older versions
    # do not. Normalize for portability.
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        # Treat naive timestamps as UTC (C# DateTimeZoneHandling.Utc).
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_payload_finite(events: list[FormalEvent]) -> None:
    for ev in events:
        _check_finite(ev.payload, ev.sequence, path="payload")


def _check_finite(value: Any, sequence: int, path: str) -> None:
    if isinstance(value, bool):
        # bool is a subclass of int; treat as finite (no NaN/Inf concern).
        return
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise ValueError(
                f"non-finite numeric value {value!r} at sequence {sequence} "
                f"field {path!r}; NaN/Inf are forbidden in formal payloads"
            )
        return
    if isinstance(value, str):
        # Strings are decimal text in the C# trace; do not numericize here.
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            _check_finite(sub, sequence, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for idx, sub in enumerate(value):
            _check_finite(sub, sequence, f"{path}[{idx}]")
        return


def _reconcile(events: list[FormalEvent]) -> None:
    """Replicate C# FormalTraceReconciler.Reconcile.

    Invariants:
      * Every FILL references an earlier ORDER_INTENT with matching integer
        orderId.
      * Every FILL has a following correlated HOLDINGS_SNAPSHOT with the same
        orderId (strict one-to-one via a per-orderId pending queue).
      * No spurious snapshot, no wrong orderId, no duplicate open intent.
    """
    open_intents: dict[int, int] = {}  # orderId -> intent sequence
    pending_snapshots: dict[int, deque[int]] = {}  # orderId -> fill seqs awaiting snapshot

    for ev in events:
        if ev.event_type == _ORDER_INTENT:
            order_id = _extract_order_id(ev, required=True)
            if order_id in open_intents:
                raise ValueError(
                    f"duplicate ORDER_INTENT for orderId {order_id} at sequence "
                    f"{ev.sequence}; an earlier intent for the same order is "
                    f"still open (opened at sequence {open_intents[order_id]})"
                )
            open_intents[order_id] = ev.sequence
        elif ev.event_type == _FILL:
            order_id = _extract_order_id(ev, required=True)
            if order_id not in open_intents:
                raise ValueError(
                    f"FILL at sequence {ev.sequence} references orderId "
                    f"{order_id} but no earlier ORDER_INTENT for that order "
                    f"exists; ordering requires intent before fill"
                )
            pending_snapshots.setdefault(order_id, deque()).append(ev.sequence)
        elif ev.event_type == _HOLDINGS_SNAPSHOT:
            order_id = _extract_order_id(ev, required=True)
            if order_id not in open_intents:
                raise ValueError(
                    f"HOLDINGS_SNAPSHOT at sequence {ev.sequence} references "
                    f"orderId {order_id} but no ORDER_INTENT for that order "
                    f"exists"
                )
            queue = pending_snapshots.get(order_id)
            if not queue:
                raise ValueError(
                    f"HOLDINGS_SNAPSHOT at sequence {ev.sequence} for orderId "
                    f"{order_id} has no preceding FILL awaiting a snapshot; "
                    f"correlation is spurious"
                )
            queue.popleft()
        # DECISION and unknown event types pass through without correlation.

    # Any leftover pending snapshots are missing post-fill evidence.
    for order_id, queue in pending_snapshots.items():
        if queue:
            first_missing = queue[0]
            raise ValueError(
                f"FILL at sequence {first_missing} for orderId {order_id} has "
                f"no following correlated HOLDINGS_SNAPSHOT "
                f"({len(queue)} missing snapshot(s) for this order). Every fill "
                f"must be followed by a correlated snapshot"
            )


def _extract_order_id(ev: FormalEvent, *, required: bool) -> int:
    """Extract and validate the integer orderId from the payload.

    Accepts integer tokens directly; accepts float/double tokens only when
    they represent whole numbers (mirrors the C# JTokenType.Float branch);
    accepts integer-valued strings. Rejects non-positive order ids.
    """
    payload = ev.payload
    if not isinstance(payload, dict):
        if required:
            raise ValueError(
                f"{ev.event_type} at sequence {ev.sequence} has no payload "
                f"object; required field 'orderId' is missing"
            )
        return 0
    token = payload.get("orderId")
    if token is None:
        if required:
            raise ValueError(
                f"{ev.event_type} at sequence {ev.sequence} is missing required "
                f"correlation field 'orderId'; correlation must use the native "
                f"order id, not event count"
            )
        return 0
    if isinstance(token, bool):
        raise ValueError(
            f"{ev.event_type} at sequence {ev.sequence} has a boolean orderId "
            f"{token!r}; order ids must be integers"
        )
    if isinstance(token, int):
        order_id = token
    elif isinstance(token, float):
        if token != int(token):
            raise ValueError(
                f"{ev.event_type} at sequence {ev.sequence} has a non-integer "
                f"'orderId' value {token!r}; order ids must be integers"
            )
        order_id = int(token)
    elif isinstance(token, str):
        try:
            order_id = int(token)
        except ValueError as error:
            raise ValueError(
                f"{ev.event_type} at sequence {ev.sequence} has a non-integer "
                f"'orderId' string {token!r}; order ids must be integers"
            ) from error
    else:
        raise ValueError(
            f"{ev.event_type} at sequence {ev.sequence} has an unsupported "
            f"type {type(token).__name__} for field 'orderId'; order ids must "
            f"be integers"
        )
    if order_id <= 0:
        raise ValueError(
            f"{ev.event_type} at sequence {ev.sequence} has a non-positive "
            f"orderId {order_id}; order ids must be positive"
        )
    return order_id


def canonical_hash(events: list[FormalEvent]) -> str:
    """Compute a stable SHA-256 over the canonical (sorted-key) JSON of a
    validated event list. Useful for asserting byte-stability of a trace.

    Only the economic+identity content is hashed; this is NOT used for
    equivalence (see :mod:`equivalence`) but is provided as a helper for
    artifact-pinning.
    """
    import hashlib

    digest = hashlib.sha256()
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        canonical = json.dumps(
            {
                "schema_version": ev.schema_version,
                "sequence": ev.sequence,
                "event_type": ev.event_type,
                "experiment_id": ev.experiment_id,
                "window_id": ev.window_id,
                "stage_id": ev.stage_id,
                "run_id": ev.run_id,
                "candidate_id": ev.candidate_id,
                "event_time_utc": ev.event_time_utc,
                "payload": payload,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        digest.update(canonical.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
