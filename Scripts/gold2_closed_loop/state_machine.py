"""Closed enums, verdict/disposition pairing, and the lifecycle state machine
for the Gold2 closed-loop economic proof (Task 9, Phase 2 STOP gate).

All enum values are frozen to design section 5 (lifecycle / validity /
invalid reason / block reason / execution status / historical verdict /
disposition) and section 7 (candidate event type / candidate state /
convergence status / generation terminal reason / G3 alias reason).
``transition`` enforces the §5 core lifecycle graph; any edge not listed
there raises ``ValueError`` with an actionable message naming both statuses.

No production code (the mature Gold2 strategy, the permissive RL trace loader,
or the existing ``Scripts/auto_optimize/evolution_scheduler.py``) is modified.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class LifecycleStatus(StrEnum):
    """Experiment lifecycle status (design §5 line 192)."""

    NEW = "NEW"
    PREREGISTERED = "PREREGISTERED"
    DATA_VALIDATED = "DATA_VALIDATED"
    RUNNING_CONSTRUCTION = "RUNNING_CONSTRUCTION"
    CANDIDATES_FROZEN = "CANDIDATES_FROZEN"
    RUNNING_BLIND_TEST = "RUNNING_BLIND_TEST"
    BLIND_TEST_COMPLETE = "BLIND_TEST_COMPLETE"
    SEALED = "SEALED"
    BLOCKED = "BLOCKED"
    INVALID = "INVALID"
    FAILED_EXECUTION = "FAILED_EXECUTION"


class ValidityStatus(StrEnum):
    """Whether the experiment is scientifically valid (design §5)."""

    VALID = "VALID"
    INVALID = "INVALID"


class InvalidReason(StrEnum):
    """Reason an experiment is marked INVALID (design §5 line 194)."""

    INFORMATION_LEAKAGE = "INFORMATION_LEAKAGE"
    MISSING_FORMAL_TRACE = "MISSING_FORMAL_TRACE"
    EVIDENCE_CAPTURE_FAILED = "EVIDENCE_CAPTURE_FAILED"
    HASH_MISMATCH = "HASH_MISMATCH"
    POST_BLIND_MUTATION = "POST_BLIND_MUTATION"
    DATA_INVALID = "DATA_INVALID"


class BlockReason(StrEnum):
    """Reason an experiment is BLOCKED (design §5 line 195)."""

    BLOCKED_INSUFFICIENT_TEST_WINDOWS = "BLOCKED_INSUFFICIENT_TEST_WINDOWS"
    BLOCKED_INTERFACE_NOT_PROOF_READY = "BLOCKED_INTERFACE_NOT_PROOF_READY"
    BLOCKED_INSUFFICIENT_DATA = "BLOCKED_INSUFFICIENT_DATA"


class ExecutionStatus(StrEnum):
    """Per-candidate execution status (design §5 line 196)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_STRATEGY = "FAILED_STRATEGY"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"
    FAILED_EVIDENCE_CAPTURE = "FAILED_EVIDENCE_CAPTURE"
    TIMED_OUT = "TIMED_OUT"
    NO_TRADES = "NO_TRADES"


class HistoricalVerdict(StrEnum):
    """Aggregate historical verdict (design §5 line 197)."""

    EFFECTIVE = "EFFECTIVE"
    PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
    NOT_EFFECTIVE = "NOT_EFFECTIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class Disposition(StrEnum):
    """Scientific disposition paired with a verdict (design §5 line 198).

    ``Disposition | None`` is the full type: ``EFFECTIVE`` and
    ``PARTIALLY_EFFECTIVE`` pair with ``None`` (no refutation), so callers
    pass the python ``None`` rather than an enum member.
    """

    UNPROVEN = "UNPROVEN"
    REFUTED = "REFUTED"


class CandidateEventType(StrEnum):
    """Append-only candidate-event type (design §7 / §10)."""

    REGISTERED = "REGISTERED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_STRATEGY = "FAILED_STRATEGY"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"
    FAILED_EVIDENCE_CAPTURE = "FAILED_EVIDENCE_CAPTURE"
    TIMED_OUT = "TIMED_OUT"
    NO_TRADES = "NO_TRADES"
    PRUNED = "PRUNED"
    DUPLICATE = "DUPLICATE"
    DOMINATED = "DOMINATED"
    REJECTED = "REJECTED"
    INVALID = "INVALID"


class CandidateState(StrEnum):
    """Candidate terminal / interim state (design §10)."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PRUNED = "PRUNED"
    DOMINATED = "DOMINATED"
    REJECTED = "REJECTED"
    INVALID = "INVALID"


class ConvergenceStatus(StrEnum):
    """Generation convergence status (design §7 line 282)."""

    CONVERGED = "CONVERGED"
    NON_CONVERGED = "NON_CONVERGED"
    UNOBSERVABLE = "UNOBSERVABLE"


class GenerationTerminalReason(StrEnum):
    """Why a generation sequence terminated (design §7 line 282)."""

    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TRIGGERED = "TRIGGERED"
    CONVERGED = "CONVERGED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class G3AliasReason(StrEnum):
    """Why G3 did not produce a distinct redesign (design §7 lines 276-280)."""

    NOT_TRIGGERED = "NOT_TRIGGERED"
    TRIGGER_UNOBSERVABLE = "TRIGGER_UNOBSERVABLE"
    CONSTRUCTION_FAILED = "CONSTRUCTION_FAILED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"


# Legal transitions from the §5 core lifecycle graph (lines 213-221):
#   NEW -> PREREGISTERED
#   PREREGISTERED -> DATA_VALIDATED | BLOCKED | INVALID
#   DATA_VALIDATED -> RUNNING_CONSTRUCTION
#   RUNNING_CONSTRUCTION -> CANDIDATES_FROZEN | FAILED_EXECUTION | INVALID
#   CANDIDATES_FROZEN -> RUNNING_BLIND_TEST
#   RUNNING_BLIND_TEST -> BLIND_TEST_COMPLETE | FAILED_EXECUTION | INVALID
#   BLIND_TEST_COMPLETE -> SEALED | INVALID
# Edges not listed are illegal. BLOCKED, INVALID, FAILED_EXECUTION, and SEALED
# are terminal (no outgoing edges defined); transitions FROM them raise.
_LEGAL_TRANSITIONS: dict[LifecycleStatus, frozenset[LifecycleStatus]] = {
    LifecycleStatus.NEW: frozenset({LifecycleStatus.PREREGISTERED}),
    LifecycleStatus.PREREGISTERED: frozenset(
        {
            LifecycleStatus.DATA_VALIDATED,
            LifecycleStatus.BLOCKED,
            LifecycleStatus.INVALID,
        }
    ),
    LifecycleStatus.DATA_VALIDATED: frozenset({LifecycleStatus.RUNNING_CONSTRUCTION}),
    LifecycleStatus.RUNNING_CONSTRUCTION: frozenset(
        {
            LifecycleStatus.CANDIDATES_FROZEN,
            LifecycleStatus.FAILED_EXECUTION,
            LifecycleStatus.INVALID,
        }
    ),
    LifecycleStatus.CANDIDATES_FROZEN: frozenset({LifecycleStatus.RUNNING_BLIND_TEST}),
    LifecycleStatus.RUNNING_BLIND_TEST: frozenset(
        {
            LifecycleStatus.BLIND_TEST_COMPLETE,
            LifecycleStatus.FAILED_EXECUTION,
            LifecycleStatus.INVALID,
        }
    ),
    LifecycleStatus.BLIND_TEST_COMPLETE: frozenset(
        {LifecycleStatus.SEALED, LifecycleStatus.INVALID}
    ),
    # Terminal statuses have no outgoing edges.
    LifecycleStatus.SEALED: frozenset(),
    LifecycleStatus.BLOCKED: frozenset(),
    LifecycleStatus.INVALID: frozenset(),
    LifecycleStatus.FAILED_EXECUTION: frozenset(),
}

# Precomputed reverse lookup so callers can pass either a StrEnum member or
# its string value. ``StrEnum`` already makes ``LifecycleStatus("NEW")``
# work, but this avoids repeated constructor calls in hot paths.
_STATUS_BY_VALUE: dict[str, LifecycleStatus] = {
    member.value: member for member in LifecycleStatus
}


def _coerce_status(value: Any, label: str) -> LifecycleStatus:
    """Coerce a string or LifecycleStatus into a LifecycleStatus member.

    Raises
    ------
    ValueError:
        If ``value`` is not a recognized lifecycle status.
    """
    if isinstance(value, LifecycleStatus):
        return value
    if isinstance(value, str):
        member = _STATUS_BY_VALUE.get(value)
        if member is not None:
            return member
    valid = sorted(member.value for member in LifecycleStatus)
    raise ValueError(
        f"unknown {label} {value!r}; expected one of {valid}"
    )


def transition(current: LifecycleStatus | str, target: LifecycleStatus | str) -> LifecycleStatus:
    """Validate and apply a lifecycle transition.

    Parameters
    ----------
    current:
        The current lifecycle status (enum member or string value).
    target:
        The intended next lifecycle status.

    Returns
    -------
    LifecycleStatus
        The new status (identical to ``target``).

    Raises
    ------
    ValueError:
        If the edge is not in the §5 core lifecycle graph, or if either
        status is unrecognized. The message names both statuses so the
        failure is actionable.
    """
    cur = _coerce_status(current, "current lifecycle status")
    tgt = _coerce_status(target, "target lifecycle status")
    allowed = _LEGAL_TRANSITIONS.get(cur, frozenset())
    if tgt not in allowed:
        if cur == tgt:
            raise ValueError(
                f"illegal lifecycle transition: self-transition {cur.value} -> {cur.value} "
                "is not listed in the §5 core lifecycle graph"
            )
        if not allowed:
            raise ValueError(
                f"illegal lifecycle transition: {cur.value} is terminal and has no "
                f"outgoing edges (attempted {cur.value} -> {tgt.value})"
            )
        legal = sorted(member.value for member in allowed)
        raise ValueError(
            f"illegal lifecycle transition {cur.value} -> {tgt.value}; "
            f"legal targets from {cur.value}: {legal}"
        )
    return tgt
