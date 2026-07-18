"""Four-window proof runner (Task 12, Phase 2 STOP gate).

Constructs W1-W4 G0/G1/one-review/G2 with the blind partition ABSENT
throughout construction (spec §3, §6 line 118: "construction 进程只挂载
train/review partition 的只读快照，blind partition 不可见"). All four windows
construct BEFORE any blind access opens; ``BLIND_ACCESS_OPENED`` must not
appear during ``construct``.

The runner is PROOF-ONLY: it delegates to the proof-local adapters
(G0/G1 candidate builders, formal review, G2 feedback) and never imports
the production daemon/evolution/inspiration machinery.

Information leakage (a blind metric/report/trade/attribution/verdict
entering a later window's candidate construction) sets
``validity_status=INVALID, invalid_reason=INFORMATION_LEAKAGE`` (spec §6
line 118).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from Scripts.gold2_closed_loop.adapters.base import (
    OptimizationRequest,
    TrialResult,
)


class ConstructionAdapters(Protocol):
    """The four construction-stage adapters the runner delegates to.

    Each G0/G1/G2 adapter is a callable ``runner(request) -> TrialResult``
    over the TRAIN partition. The review adapter builds the frozen review
    bundle from the train/review partition (its signature is intentionally
    loose here; the real review adapter is FormalReviewAdapter)."""

    def g0(self, request: OptimizationRequest) -> TrialResult: ...
    def g1(self, request: OptimizationRequest) -> TrialResult: ...
    def review(self, *args: Any, **kwargs: Any) -> Any: ...
    def g2(self, request: OptimizationRequest) -> TrialResult: ...


@dataclass
class RunnerEvents:
    """Synthetic event log the runner appends to (mirrors the lifecycle
    event vocabulary). Tests assert on membership."""

    events: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConstructionResult:
    """Result of :meth:`ProofRunner.construct`.

    Fields
    ------
    window_count:
        Number of windows constructed.
    blind_opened:
        Always False after construct (blind is opened only by a separate
        explicit ``open_blind`` step AFTER all windows are frozen).
    windows:
        Per-window construction record (window_id -> stages built).
    """

    window_count: int
    blind_opened: bool
    windows: dict[str, list[str]] = field(default_factory=dict)


class ProofRunner:
    """Construct W1-W4 G0/G1/one-review/G2 with blind absent.

    The runner is given a :class:`ConstructionAdapters` whose methods the
    runner invokes over the TRAIN (and review) partition only. The blind
    partition is never mounted during construction.
    """

    def __init__(self, adapters: ConstructionAdapters) -> None:
        self._adapters = adapters
        # Each FakeAdapters-like object carries an ``events`` list for the
        # synthetic lifecycle vocabulary; if absent, the runner keeps its
        # own (production adapters would record into the candidate journal).
        self._events: list[str] = getattr(adapters, "events", [])
        self._leakage: list[tuple[str, str]] = []
        self._validity: str = "VALID"
        self._invalid_reason: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def construct(
        self, window_ids: list[str]
    ) -> ConstructionResult:
        """Construct G0/G1/review/G2 for each window over the TRAIN (and
        review) partition only. The blind partition is NOT opened."""
        if not window_ids:
            raise ValueError("construct: window_ids must be non-empty")
        windows: dict[str, list[str]] = {}
        for window_id in window_ids:
            train_partition = f"{window_id}/train"
            review_partition = f"{window_id}/review"
            # G0 over train.
            self._adapters.g0(self._request(window_id, "G0", train_partition))
            # G1 over train.
            self._adapters.g1(self._request(window_id, "G1", train_partition))
            # Formal review over the review partition (frozen bundle).
            self._adapters.review(train_partition, review_partition)
            self._events.append("REVIEW")
            # G2 over train (driven by the frozen review bundle).
            self._adapters.g2(self._request(window_id, "G2", train_partition))
            windows[window_id] = ["G0", "G1", "REVIEW", "G2"]
        # Blind is NOT opened during construct. It is opened only by an
        # explicit ``open_blind`` call AFTER all windows are frozen.
        return ConstructionResult(
            window_count=len(window_ids),
            blind_opened=False,
            windows=windows,
        )

    def open_blind(self, window_ids: list[str]) -> None:
        """Open the blind partition for ALL windows at once (spec §6 line
        118: global one-shot freeze then open the four blind years). This
        is the ONLY place ``BLIND_ACCESS_OPENED`` is appended."""
        self._events.append("BLIND_ACCESS_OPENED")

    def record_leakage(self, window_id: str, detail: str) -> None:
        """Record an information-leakage event: a blind metric/report/
        trade/attribution/verdict entered a later window's candidate
        construction. Invalidates the experiment with INFORMATION_LEAKAGE."""
        self._leakage.append((window_id, detail))
        self._validity = "INVALID"
        self._invalid_reason = "INFORMATION_LEAKAGE"

    def validity_status(self) -> str:
        return self._validity

    def invalid_reason(self) -> str | None:
        return self._invalid_reason

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _request(
        window_id: str, stage_id: str, partition: str
    ) -> OptimizationRequest:
        """Build a minimal typed request for a construction stage.

        The full request (snapshot_paths, trace_path, observation_window,
        shaping, preregistration) is assembled by the production wiring
        (run_experiment execute); the runner only needs the identity +
        partition fields to drive the adapters and assert partition
        separation.
        """
        from pathlib import Path

        return OptimizationRequest(
            experiment_id="gold2-proof",
            window_id=window_id,
            stage_id=stage_id,
            candidate_id=f"{stage_id}-{partition}",
            partition=partition,
            parameter_space={},
            parameters={},
            seed=0,
            budget=0,
            run_dir=Path("."),
        )
