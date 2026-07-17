"""Typed request / result dataclasses and the abstract ``Adapter`` base
for the proof-local G1 parameter optimizer (Task 10, Phase 2 STOP gate).

Per the plan's Step 3 the typed request carries ALL proof identity fields
(experiment/window/stage/candidate/partition/snapshot/trace/run-dir/
observation/shaping/seed/budget). The runner protocol returns a
``TrialResult``; the adapter catches and classifies exceptions raised by
the runner.

This module is PROOF-ONLY and has NO dependency on the production
``evolution_scheduler`` / ``layer_state`` / ``inspiration`` machinery.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from Scripts.gold2_closed_loop.state_machine import (
    CandidateEventType,
    ExecutionStatus,
)


@dataclass(frozen=True)
class OptimizationRequest:
    """Typed request carrying ALL proof identity fields.

    Every field the downstream stages (formal review / feedback / G3)
    need to attribute a trial lives here, so a downstream reviewer cannot
    fabricate an identity it was never given (design §14 anti-p-hacking).

    Fields
    ------
    experiment_id:
        The preregistration experiment id.
    window_id:
        e.g. ``"W1"``.
    stage_id:
        e.g. ``"G1"``.
    candidate_id:
        Unique per trial within the partition.
    partition:
        e.g. ``"W1/train"``.
    parameter_space:
        The declared parameter space (spec dict per param name). Carried
        so the runner / reviewer can verify the trial only varied
        declared params.
    parameters:
        The concrete parameter dict for THIS trial.
    seed:
        The deterministic seed used to generate this candidate.
    budget:
        The remaining budget at the time of this request.
    run_dir:
        Absolute, unique per-run directory for the LEAN result packet.
    trace_path:
        Absolute path to the formal-trace JSONL, or None if not configured.
    snapshot_paths:
        Map of snapshot name -> absolute path, or None. Captures the
        frozen input data / config snapshots for hash-identity.
    observation_window:
        ``(start_date, end_date)`` tuple for the observation window, or
        None.
    shaping:
        Optional shaping dict (G2/G3 concern; None for pure G1).
    preregistration:
        The validated preregistration dict (for thresholds/gates), or
        None for unit tests using explicit ``gates``.
    """

    experiment_id: str
    window_id: str
    stage_id: str
    candidate_id: str
    partition: str
    parameter_space: dict[str, Any]
    parameters: dict[str, Any]
    seed: int
    budget: int
    run_dir: Path
    trace_path: Path | None = None
    snapshot_paths: dict[str, Path] | None = None
    observation_window: tuple[str, str] | None = None
    shaping: dict[str, Any] | None = None
    preregistration: dict[str, Any] | None = None


@dataclass(frozen=True)
class TrialResult:
    """Runner return value.

    ``status`` is the ``ExecutionStatus`` VALUE (string). The adapter
    may override this status (e.g. a runner returning ``SUCCEEDED`` with
    ``trades == 0`` becomes ``NO_TRADES``).

    Fields
    ------
    status:
        One of ``ExecutionStatus`` values. ``SUCCEEDED`` means the runner
        completed; the adapter still applies gates.
    metrics:
        The metrics dict (sharpe, net_profit, mdd, trades, ...), or None
        on failure.
    error:
        Human-readable error tail, or None.
    """

    status: str
    metrics: dict[str, Any] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        # Validate the status is a known ExecutionStatus value.
        valid = {member.value for member in ExecutionStatus}
        if self.status not in valid:
            raise ValueError(
                f"TrialResult.status {self.status!r} not in ExecutionStatus "
                f"(valid: {sorted(valid)})"
            )


@dataclass(frozen=True)
class Attempt:
    """One candidate attempt recorded by the optimizer.

    ``status`` is the ``ExecutionStatus`` VALUE (string) and
    ``event_type`` is the ``CandidateEventType`` VALUE (string), so tests
    can compare against plain strings (the plan's anchor test does
    ``a.status == "SUCCEEDED"``).
    """

    candidate_id: str
    parameters: dict[str, Any]
    status: str
    event_type: str
    metrics: dict[str, Any] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        exec_valid = {member.value for member in ExecutionStatus}
        evt_valid = {member.value for member in CandidateEventType}
        if self.status not in exec_valid:
            raise ValueError(
                f"Attempt.status {self.status!r} not in ExecutionStatus"
            )
        if self.event_type not in evt_valid:
            raise ValueError(
                f"Attempt.event_type {self.event_type!r} not in "
                f"CandidateEventType"
            )


@dataclass(frozen=True)
class OptimizationResult:
    """The optimizer's return value.

    Fields
    ------
    attempts:
        List of ``Attempt`` in trial order. Includes EVERY trial
        (success, failure, timeout, no-trade, prune, duplicate,
        dominated, rejected) — the budget consumed.
    attempted_trial_count:
        ``len(attempts)``. Includes ALL failures — the budget consumed.
    selected_candidate:
        The best passing ``Attempt`` (or None if nothing passed gates).
    aliased_to_g0:
        Always False for the optimizer itself. The G1-alias-G0 failure
        handling from design §14 is a SEPARATE downstream concern; the
        optimizer never fabricates a G0 alias.
    """

    attempts: list[Attempt] = field(default_factory=list)
    attempted_trial_count: int = 0
    selected_candidate: Attempt | None = None
    aliased_to_g0: bool = False


class Runner(Protocol):
    """Callable protocol for the runner the adapter wraps.

    The real runner wraps ``Scripts.gold2_closed_loop.lean_runner.run_lean``
    and returns a ``TrialResult``. Tests pass a ``FakeRunner`` callable.
    The runner MAY raise (``TimeoutError`` / ``InfrastructureError`` /
    generic ``Exception``); the adapter catches and classifies.
    """

    def __call__(self, request: OptimizationRequest) -> TrialResult: ...


class Adapter(ABC):
    """Abstract base for proof-local adapters (G1 optimizer, G2 review, ...).

    Subclasses implement ``run`` returning an ``OptimizationResult``.
    """

    @abstractmethod
    def run(
        self, parameter_space: dict[str, Any], partition: str
    ) -> OptimizationResult:
        """Run the adapter over the declared parameter space."""
        raise NotImplementedError


# Re-export the callable type alias for type hints in the optimizer.
RunnerCallable = Callable[[OptimizationRequest], TrialResult]
