"""Failing contracts for the four-window proof runner (Task 12, Phase 2
STOP gate).

The ProofRunner constructs W1-W4 G0/G1/one-review/G2 with the blind
partition ABSENT throughout construction. Invariants (spec §3, §6):

* All four windows construct BEFORE any blind access opens
  (``BLIND_ACCESS_OPENED`` must NOT appear during construct).
* The runner only mounts train/review partitions during construction;
  the blind partition is never visible to a candidate builder.
* Information leakage (blind metrics/reports/trades/attribution/verdict
  entering a later window's candidate construction) sets
  ``invalid_reason=INFORMATION_LEAKAGE``.
"""
from __future__ import annotations

from collections import namedtuple
from typing import Any

import pytest

from Scripts.gold2_closed_loop.adapters.base import (
    OptimizationRequest,
    TrialResult,
)
from Scripts.gold2_closed_loop.proof_runner import (
    ProofRunner,
    RunnerEvents,
)

RunnerCall = namedtuple("RunnerCall", ["partition", "window_id", "stage_id"])


class FakeAdapters:
    """Records every adapter invocation + synthetic events."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.calls: list[RunnerCall] = []
        self.partitions_seen: set[str] = set()

    def _record(self, partition: str, window_id: str, stage_id: str) -> TrialResult:
        self.calls.append(RunnerCall(partition, window_id, stage_id))
        self.partitions_seen.add(partition)
        return TrialResult(
            status="SUCCEEDED",
            metrics={"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 10},
            error=None,
        )

    def g0(self, request: OptimizationRequest) -> TrialResult:
        return self._record(request.partition, request.window_id, "G0")

    def g1(self, request: OptimizationRequest) -> TrialResult:
        return self._record(request.partition, request.window_id, "G1")

    def review(self, *args: Any, **kwargs: Any) -> Any:
        self.events.append("REVIEW")
        return None

    def g2(self, request: OptimizationRequest) -> TrialResult:
        return self._record(request.partition, request.window_id, "G2")


@pytest.fixture
def fake_adapters() -> FakeAdapters:
    return FakeAdapters()


# --- all windows construct before blind -------------------------------


def test_all_windows_construct_before_blind(fake_adapters):
    ProofRunner(fake_adapters).construct(["W1", "W2", "W3", "W4"])
    assert "BLIND_ACCESS_OPENED" not in fake_adapters.events


def test_construction_never_mounts_blind_partition(fake_adapters):
    """During construction, only train/review partitions are mounted."""
    ProofRunner(fake_adapters).construct(["W1", "W2", "W3", "W4"])
    for p in fake_adapters.partitions_seen:
        assert "/blind" not in p, f"blind partition mounted during construct: {p}"
        assert p.endswith("/train") or p.endswith("/review"), (
            f"non-train/review partition mounted during construct: {p}"
        )


def test_information_leakage_invalidates(fake_adapters):
    """If a blind metric/report/trade/verdict enters a later window's
    candidate construction, the experiment is invalidated with
    INFORMATION_LEAKAGE."""
    runner = ProofRunner(fake_adapters)
    # Simulate leakage: a later window's G1 sees an earlier blind verdict.
    runner.record_leakage("W2", "blind verdict entered W2 G1 construction")
    validity = runner.validity_status()
    assert validity == "INVALID"
    assert runner.invalid_reason() == "INFORMATION_LEAKAGE"


def test_construct_returns_typed_result_with_window_count(fake_adapters):
    result = ProofRunner(fake_adapters).construct(["W1", "W2", "W3", "W4"])
    assert result.window_count == 4
    assert result.blind_opened is False
