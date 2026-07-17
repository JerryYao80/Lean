"""Failing contracts for the proof-local G1 parameter optimizer adapter
(Task 10, Phase 2 STOP gate).

Exercises:
* the anchor test from the plan verbatim — a ``TimeoutError`` AND a success
  both consume budget and are recorded as attempts with statuses
  ``TIMED_OUT`` then ``SUCCEEDED``;
* every failure mode (strategy exception, infrastructure exception,
  timeout, no-trade) consumes budget and is recorded with the right
  ``event_type`` / ``execution_status``;
* a REGISTERED candidate event (``execution_status=PENDING``) is written
  BEFORE the runner is invoked, then a terminal event after;
* budget exhaustion stops further trials;
* parameter types (bool / range / choice) produce in-bounds values;
* selection applies minimum gates (min trades / max MDD / DSR / bounds /
  train-subwindow stability) — failing candidates are NOT selected
  (PRUNED / REJECTED / DOMINATED recorded);
* duplicate parameter sets are recorded DUPLICATE and consume budget but
  are not selected;
* G1 never aliases to G0 (``selected_candidate is None`` and
  ``aliased_to_g0 is False`` when nothing passes);
* journal integrity: after all trials the hash chain is intact and every
  record passes ``validate_candidate_event``.

The ``fake_runner`` fixture is a callable ``fake_runner(request) ->
result`` where ``result`` is either a raised exception (``TimeoutError``
/ ``RuntimeError`` / ``InfrastructureError``) or a metrics dict. The
fake_runner records its calls in a ``calls`` list of ``RunnerCall``
namedtuples (at least ``.partition`` and ``.parameters``) so a later
Task 11 review test can assert on partition separation.
"""

from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

from Scripts.gold2_closed_loop.adapters.base import (
    OptimizationRequest,
    TrialResult,
)
from Scripts.gold2_closed_loop.adapters.parameter_optimizer import (
    ParameterOptimizerAdapter,
)
from Scripts.gold2_closed_loop.candidate_registry import CandidateRegistry
from Scripts.gold2_closed_loop.event_journal import EventJournal
from Scripts.gold2_closed_loop.schemas import validate_candidate_event
from Scripts.gold2_closed_loop.selection import dedupe, dominates, select

# A recorded runner call. Mirrors the shape Task 11's
# ``test_review_partition_never_ranks_g2`` needs: at least ``partition``
# and ``parameters``.
RunnerCall = namedtuple("RunnerCall", ["partition", "parameters", "candidate_id"])


class FakeRunner:
    """Callable fake runner.

    ``fake_runner.results`` is a list whose i-th element drives the i-th
    call. If the element is an exception INSTANCE, it is raised; if it is
    an exception CLASS, it is instantiated then raised; if it is a dict,
    a ``TrialResult`` with status ``SUCCEEDED`` and those metrics is
    returned. Each call is recorded in ``self.calls``.
    """

    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = list(results) if results is not None else []
        self.calls: list[RunnerCall] = []
        self._idx = 0

    def __call__(self, request: OptimizationRequest) -> TrialResult:
        if self._idx >= len(self.results):
            raise IndexError(
                f"FakeRunner exhausted: results has {len(self.results)} "
                f"entries but call #{self._idx + 1} was made"
            )
        item = self.results[self._idx]
        self._idx += 1
        self.calls.append(
            RunnerCall(
                partition=request.partition,
                parameters=dict(request.parameters),
                candidate_id=request.candidate_id,
            )
        )
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        if isinstance(item, dict):
            return TrialResult(
                status="SUCCEEDED",
                metrics=dict(item),
                error=None,
            )
        # A TrialResult already
        return item


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


# --- anchor test (verbatim from plan) -------------------------------


def test_bool_and_failure_both_consume_budget(fake_runner, tmp_path):
    fake_runner.results = [TimeoutError(), {"sharpe": .7, "net_profit": .1, "mdd": .08}]
    result = ParameterOptimizerAdapter(fake_runner, 2, 17, tmp_path / "events.jsonl").run(
        {"trend-disable": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 2
    assert isinstance(result.attempts[1].parameters["trend-disable"], bool)
    assert [a.status for a in result.attempts] == ["TIMED_OUT", "SUCCEEDED"]


# --- failure modes all consume budget -------------------------------


def test_every_failure_mode_consumes_budget(fake_runner, tmp_path):
    from Scripts.gold2_closed_loop.adapters.parameter_optimizer import (
        InfrastructureError,
    )

    fake_runner.results = [
        RuntimeError("strategy crash: bad fill"),  # FAILED_STRATEGY
        InfrastructureError("infrastructure: dotnet not found"),  # FAILED_INFRASTRUCTURE
        TimeoutError(),  # TIMED_OUT
        {"sharpe": 0.0, "net_profit": 0.0, "mdd": 0.0, "trades": 0},  # NO_TRADES
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 4, 17, tmp_path / "events.jsonl"
    ).run({"x": {"type": "range", "min": 1, "max": 4, "step": 1}}, "W2/train")
    assert result.attempted_trial_count == 4
    statuses = [a.status for a in result.attempts]
    assert statuses == [
        "FAILED_STRATEGY",
        "FAILED_INFRASTRUCTURE",
        "TIMED_OUT",
        "NO_TRADES",
    ]
    event_types = [a.event_type for a in result.attempts]
    assert event_types == [
        "FAILED_STRATEGY",
        "FAILED_INFRASTRUCTURE",
        "TIMED_OUT",
        "NO_TRADES",
    ]
    assert result.selected_candidate is None
    assert result.aliased_to_g0 is False


# --- register before execution --------------------------------------


def test_register_before_execution(fake_runner, tmp_path):
    """Every attempt writes a REGISTERED (PENDING) event BEFORE the runner
    is invoked, then a terminal event after. Assert the journal sequence
    is REGISTERED+terminal pairs."""
    fake_runner.results = [
        TimeoutError(),
        {"sharpe": .5, "net_profit": .05, "mdd": .1, "trades": 12},
    ]
    journal_path = tmp_path / "events.jsonl"
    adapter = ParameterOptimizerAdapter(fake_runner, 2, 17, journal_path)
    # Wrap to inspect calls between register and run.
    run_calls_before_terminal = []
    real_runner = adapter._runner
    state = {"calls": 0}

    def counting_runner(request):
        # Before the runner is called, the REGISTERED event must already
        # be in the journal.
        recs = EventJournal(journal_path).read_all()
        # Number of REGISTERED events should exceed number of runner
        # calls so far.
        registered = [r for r in recs if r["event_type"] == "REGISTERED"]
        assert len(registered) == state["calls"] + 1, (
            f"REGISTERED not written before runner call; "
            f"registered={len(registered)} calls_so_far={state['calls']}"
        )
        state["calls"] += 1
        run_calls_before_terminal.append(request.candidate_id)
        return real_runner(request)

    adapter._runner = counting_runner
    result = adapter.run({"flag": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 2
    # Journal has REGISTERED+terminal pairs.
    recs = EventJournal(journal_path).read_all()
    event_types = [r["event_type"] for r in recs]
    assert event_types == [
        "REGISTERED",
        "TIMED_OUT",
        "REGISTERED",
        "SUCCEEDED",
    ]
    # All REGISTERED events have execution_status PENDING.
    for r in recs:
        if r["event_type"] == "REGISTERED":
            assert r["execution_status"] == "PENDING"
    # Terminal events have matching execution_status.
    for r in recs:
        if r["event_type"] != "REGISTERED":
            assert r["execution_status"] == r["event_type"]


# --- budget exhaustion stops trials ---------------------------------


def test_budget_exhaustion_stops_trials(fake_runner, tmp_path):
    fake_runner.results = [
        {"sharpe": .3 * i, "net_profit": .01 * i, "mdd": .05, "trades": 10}
        for i in range(1, 4)
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl"
    ).run({"x": {"type": "range", "min": 1, "max": 3, "step": 1}}, "W1/train")
    assert result.attempted_trial_count == 2
    assert len(result.attempts) == 2
    # Only 2 runner calls.
    assert len(fake_runner.calls) == 2


# --- parameter types ------------------------------------------------


def test_parameter_types_bool_range_choice(fake_runner, tmp_path):
    fake_runner.results = [
        {"sharpe": .1 * i, "net_profit": .01, "mdd": .05, "trades": 5}
        for i in range(8)
    ]
    space = {
        "flag": {"type": "bool"},
        "val": {"type": "range", "min": 0.0, "max": 1.0, "step": 0.5},
        "kind": {"type": "choice", "values": ["a", "b"]},
    }
    result = ParameterOptimizerAdapter(
        fake_runner, 8, 17, tmp_path / "events.jsonl"
    ).run(space, "W1/train")
    assert result.attempted_trial_count == 8
    for attempt in result.attempts:
        assert isinstance(attempt.parameters["flag"], bool)
        assert 0.0 <= attempt.parameters["val"] <= 1.0
        assert attempt.parameters["kind"] in ("a", "b")


def test_range_count_uses_linspace(fake_runner, tmp_path):
    fake_runner.results = [
        {"sharpe": .1, "net_profit": .01, "mdd": .05, "trades": 5}
        for _ in range(3)
    ]
    space = {"x": {"type": "range", "min": 0.0, "max": 1.0, "count": 3}}
    result = ParameterOptimizerAdapter(
        fake_runner, 3, 17, tmp_path / "events.jsonl"
    ).run(space, "W1/train")
    xs = sorted(a.parameters["x"] for a in result.attempts)
    assert xs == [0.0, 0.5, 1.0]


# --- selection applies minimum gates --------------------------------


def test_selection_prunes_failed_gates(fake_runner, tmp_path):
    """A candidate failing min-trades is NOT selected (PRUNED recorded)."""
    fake_runner.results = [
        {"sharpe": 2.0, "net_profit": 0.5, "mdd": 0.05, "trades": 1},  # too few
        {"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.9, "trades": 50},  # MDD too high
        {"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 50},  # passes
    ]
    gates = {"min_trades": 10, "max_mdd": 0.5, "min_dsr": 0.0}
    result = ParameterOptimizerAdapter(
        fake_runner, 3, 17, tmp_path / "events.jsonl", gates=gates
    ).run({"x": {"type": "range", "min": 1, "max": 3, "step": 1}}, "W1/train")
    # Two pruned + one SUCCEEDED.
    assert [a.event_type for a in result.attempts] == [
        "PRUNED",
        "PRUNED",
        "SUCCEEDED",
    ]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["x"] == 3


def test_selection_rejects_unstable_subwindow(fake_runner, tmp_path):
    """A candidate failing train-subwindow stability is REJECTED."""
    fake_runner.results = [
        {  # unstable: subwindow sharpes spread too large
            "sharpe": 1.0,
            "net_profit": 0.1,
            "mdd": 0.05,
            "trades": 50,
            "subwindow_sharpes": [3.0, -1.0, 2.0],
        },
        {  # stable enough
            "sharpe": 0.8,
            "net_profit": 0.08,
            "mdd": 0.06,
            "trades": 50,
            "subwindow_sharpes": [0.7, 0.9, 0.8],
        },
    ]
    gates = {
        "min_trades": 10,
        "max_mdd": 0.5,
        "min_dsr": 0.0,
        "subwindow_spread_max": 1.0,
    }
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl", gates=gates
    ).run({"x": {"type": "range", "min": 1, "max": 2, "step": 1}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["REJECTED", "SUCCEEDED"]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["x"] == 2


def test_selection_dominated(fake_runner, tmp_path):
    """A candidate strictly worse on all ranking metrics is DOMINATED."""
    fake_runner.results = [
        {"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.05, "trades": 50},  # good
        {  # strictly worse on sharpe AND net_profit AND mdd
            "sharpe": 1.0,
            "net_profit": 0.1,
            "mdd": 0.08,
            "trades": 50,
        },
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl"
    ).run({"x": {"type": "range", "min": 1, "max": 2, "step": 1}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["SUCCEEDED", "DOMINATED"]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["x"] == 1


# --- duplicate detection -------------------------------------------


def test_duplicate_detection(fake_runner, tmp_path):
    """Two identical parameter sets -> second is DUPLICATE and consumes
    budget but is not selected."""
    # A choice space with two identical values forces a duplicate
    # parameter set regardless of shuffle order. seed 17 -> order [0,1]
    # for a 2-element grid, so both attempts use the same parameters.
    fake_runner.results = [
        {"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.05, "trades": 50},
        {"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.05, "trades": 50},
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "choice", "values": [True, True]}}, "W1/train")
    # First is SUCCEEDED, second is DUPLICATE.
    assert result.attempted_trial_count == 2
    assert [a.event_type for a in result.attempts] == ["SUCCEEDED", "DUPLICATE"]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["flag"] is True


# --- no G1 -> G0 alias ----------------------------------------------


def test_no_g1_to_g0_alias_when_nothing_passes(fake_runner, tmp_path):
    fake_runner.results = [
        {"sharpe": -0.5, "net_profit": -0.1, "mdd": 0.9, "trades": 1},
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert result.selected_candidate is None
    assert result.aliased_to_g0 is False


# --- journal integrity ---------------------------------------------


def test_journal_intact_and_validates(fake_runner, tmp_path):
    fake_runner.results = [
        TimeoutError(),
        {"sharpe": .7, "net_profit": .1, "mdd": .08, "trades": 30},
        {"sharpe": .5, "net_profit": .05, "mdd": .1, "trades": 20},
    ]
    journal_path = tmp_path / "events.jsonl"
    ParameterOptimizerAdapter(
        fake_runner, 3, 17, journal_path
    ).run({"flag": {"type": "bool"}, "x": {"type": "range", "min": 1, "max": 2, "step": 1}}, "W1/train")
    records = EventJournal(journal_path).read_all()
    assert len(records) >= 6  # REGISTERED + terminal for each of 3 trials
    # Chain intact: every previous_event_sha256 chains.
    prev = "0" * 64
    for rec in records:
        assert rec["previous_event_sha256"] == prev, (
            f"chain break at sequence {rec['sequence']}: "
            f"expected {prev[:8]}..., got {rec['previous_event_sha256'][:8]}..."
        )
        assert rec["event_sha256"]
        prev = rec["event_sha256"]
    # Every record schema-valid.
    for rec in records:
        validate_candidate_event(rec)


# --- candidate_registry thin wrapper -------------------------------


def test_candidate_registry_wraps_journal(tmp_path):
    journal_path = tmp_path / "events.jsonl"
    reg = CandidateRegistry(journal_path)
    rec = reg.register("c1", "W1/train", {"flag": True}, execution_status="PENDING")
    validate_candidate_event(rec)
    assert rec["event_type"] == "REGISTERED"
    assert rec["execution_status"] == "PENDING"
    assert rec["candidate_id"] == "c1"
    rec2 = reg.record_outcome(
        "c1",
        event_type="SUCCEEDED",
        execution_status="SUCCEEDED",
        partition="W1/train",
        parameters={"flag": True},
        metrics={"sharpe": 1.0},
    )
    validate_candidate_event(rec2)
    assert rec2["event_type"] == "SUCCEEDED"
    records = EventJournal(journal_path).read_all()
    assert len(records) == 2
    assert records[0]["event_type"] == "REGISTERED"
    assert records[1]["event_type"] == "SUCCEEDED"


# --- selection.py pure functions -----------------------------------


def test_select_pure_function():
    from Scripts.gold2_closed_loop.adapters.base import Attempt

    a = Attempt(
        candidate_id="c1",
        parameters={"x": 1},
        status="SUCCEEDED",
        event_type="SUCCEEDED",
        metrics={"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.05, "trades": 50},
        error=None,
    )
    b = Attempt(
        candidate_id="c2",
        parameters={"x": 2},
        status="SUCCEEDED",
        event_type="SUCCEEDED",
        metrics={"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 50},
        error=None,
    )
    gates = {"min_trades": 10, "max_mdd": 0.5, "min_dsr": 0.0}
    selected = select([a, b], gates=gates)
    assert selected is not None
    assert selected.candidate_id == "c1"
    # Dedupe marks duplicates.
    dup = Attempt(
        candidate_id="c3",
        parameters={"x": 1},
        status="SUCCEEDED",
        event_type="SUCCEEDED",
        metrics={"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.05, "trades": 50},
        error=None,
    )
    deduped, dup_indices = dedupe([a, b, dup])
    assert len(deduped) == 2
    assert dup_indices == [2]
    # dominates: a strictly better-or-equal on all, strictly better on one.
    assert dominates(a, b) is True
    assert dominates(b, a) is False


def test_select_none_when_all_fail_gates():
    from Scripts.gold2_closed_loop.adapters.base import Attempt

    a = Attempt(
        candidate_id="c1",
        parameters={"x": 1},
        status="SUCCEEDED",
        event_type="SUCCEEDED",
        metrics={"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.05, "trades": 2},
        error=None,
    )
    gates = {"min_trades": 10, "max_mdd": 0.5, "min_dsr": 0.0}
    assert select([a], gates=gates) is None
