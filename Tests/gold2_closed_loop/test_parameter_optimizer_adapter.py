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


# --- INVALID: non-finite metrics (Task 10 spec gap) -----------------
#
# Plan Step 3 (line 542) lists "invalid attempt consumes budget" as one
# of the 10 outcome types. A runner-returned result with NaN/±Infinity
# in any gate/ranking metric (sharpe / net_profit / mdd / trades / dsr /
# subwindow_sharpes) is unusable evidence, NOT a gate failure.
# classify_outcome checks _has_non_finite_metrics FIRST (before
# DUPLICATE / gates / dominance) and records INVALID, consuming budget,
# never selected.


def test_non_finite_metrics_classified_invalid(fake_runner, tmp_path):
    """A NaN sharpe -> INVALID (not PRUNED / SUCCEEDED). Consumes budget,
    is not selected, metrics are not stored (the schema rejects non-finite
    numeric values so the journal records metrics=None for INVALID)."""
    fake_runner.results = [
        {"sharpe": float("nan"), "net_profit": 0.1, "mdd": 0.08, "trades": 5},
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 1
    assert [a.event_type for a in result.attempts] == ["INVALID"]
    # Convention: execution_status reflects whether the runner ran
    # (SUCCEEDED — the runner returned metrics); event_type carries the
    # classification (INVALID — the metrics are unusable).
    assert [a.status for a in result.attempts] == ["SUCCEEDED"]
    assert result.selected_candidate is None
    assert result.aliased_to_g0 is False
    # The journal record must be schema-valid; non-finite metrics cannot
    # be persisted (the §13 finite-value check rejects them), so the
    # recorded metrics for an INVALID attempt are None.
    records = EventJournal(tmp_path / "events.jsonl").read_all()
    term = [r for r in records if r["event_type"] != "REGISTERED"]
    assert len(term) == 1
    assert term[0]["event_type"] == "INVALID"
    assert term[0]["execution_status"] == "SUCCEEDED"
    assert term[0]["metrics"] is None


def test_infinity_metrics_classified_invalid(fake_runner, tmp_path):
    """A +Inf sharpe -> INVALID (a gate-buster infinite sharpe is unusable
    evidence, not a SUCCEEDED outlier)."""
    fake_runner.results = [
        {"sharpe": float("inf"), "net_profit": 0.1, "mdd": 0.08, "trades": 5},
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 1
    assert [a.event_type for a in result.attempts] == ["INVALID"]
    assert result.selected_candidate is None


def test_non_finite_in_subwindow_sharpes_classified_invalid(fake_runner, tmp_path):
    """A NaN buried in ``subwindow_sharpes`` -> INVALID. The non-finite
    scan must recurse into list values, not just top-level scalars."""
    fake_runner.results = [
        {
            "sharpe": 1.0,
            "net_profit": 0.1,
            "mdd": 0.08,
            "trades": 5,
            "subwindow_sharpes": [1.0, float("nan"), 0.9],
        },
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["INVALID"]
    assert result.selected_candidate is None


def test_invalid_takes_precedence_over_duplicate(fake_runner, tmp_path):
    """A candidate with non-finite metrics AND identical params to an
    earlier accepted candidate -> INVALID (not DUPLICATE). Verifies the
    classify_outcome order: INVALID BEFORE DUPLICATE."""
    # A choice space with two identical values forces a duplicate
    # parameter set regardless of shuffle order. seed 17 -> order [0,1]
    # for a 2-element grid, so both attempts use the same parameters.
    fake_runner.results = [
        {"sharpe": 1.0, "net_profit": 0.1, "mdd": 0.05, "trades": 50},  # SUCCEEDED
        {"sharpe": float("nan"), "net_profit": 0.1, "mdd": 0.05, "trades": 50},  # INVALID
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "choice", "values": [True, True]}}, "W1/train")
    assert result.attempted_trial_count == 2
    # The second attempt has the SAME params as the accepted first, BUT
    # its metrics are non-finite, so it is INVALID — not DUPLICATE.
    assert [a.event_type for a in result.attempts] == ["SUCCEEDED", "INVALID"]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["flag"] is True


def test_valid_finite_metrics_still_classify_normally(fake_runner, tmp_path):
    """Regression guard: finite metrics still classify SUCCEEDED / PRUNED
    / DOMINATED / DUPLICATE / REJECTED — the INVALID branch did not
    swallow the normal path."""
    fake_runner.results = [
        {"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.05, "trades": 50},  # SUCCEEDED
        {"sharpe": 2.0, "net_profit": 0.3, "mdd": 0.04, "trades": 1},  # PRUNED (trades)
    ]
    gates = {"min_trades": 10, "max_mdd": 0.5, "min_dsr": 0.0}
    result = ParameterOptimizerAdapter(
        fake_runner, 2, 17, tmp_path / "events.jsonl", gates=gates
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["SUCCEEDED", "PRUNED"]
    assert result.selected_candidate is not None
    assert result.selected_candidate.parameters["flag"] is False


# --- Task 10 code-quality review fixes --------------------------------
#
# Four adversarially-verified defects (7-lens workflow, confirmed real):
#   1. _passes_gate crashed on non-numeric metric values (string/list/dict)
#      AFTER register() but BEFORE record_outcome() -> dangling PENDING.
#      Fix: classify such results INVALID (metrics=None) before the gate.
#   2. _resolve_gates fed the cross-generation max_drawdown_degradation
#      DELTA into the absolute max_mdd gate (design §13 conflation).
#      Fix: read the absolute cap from candidate_gates.max_mdd, not the
#      degradation delta.
#   3. A non-finite value in a NON-gate metric key (e.g. sortino) slipped
#      past _has_non_finite_metrics as SUCCEEDED, then crashed the schema's
#      finite-check in record_outcome -> dangling PENDING. Fix: scan the
#      whole metrics tree for non-finite (INVALID, metrics=None).
#   4. A runner returning None crashed at result.status (AttributeError)
#      -> dangling PENDING. Fix: classify None as FAILED_INFRASTRUCTURE.


def test_non_numeric_metric_classified_invalid_not_crash(fake_runner, tmp_path):
    """Defect 1: a string mdd is INVALID (not a _passes_gate crash), and
    no dangling PENDING is left - the journal has REGISTERED then INVALID."""
    fake_runner.results = [
        {"sharpe": 0.1, "net_profit": 0.01, "mdd": "xx", "trades": 10},
    ]
    journal_path = tmp_path / "events.jsonl"
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, journal_path
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["INVALID"]
    assert result.selected_candidate is None
    records = EventJournal(journal_path).read_all()
    assert [r["event_type"] for r in records] == ["REGISTERED", "INVALID"]
    # No dangling PENDING: a terminal INVALID event closes the candidate.
    assert records[-1]["execution_status"] == "SUCCEEDED"
    assert records[-1]["metrics"] is None


def test_non_numeric_trades_and_dsr_classified_invalid(fake_runner, tmp_path):
    """Defect 1: trades=[] and dsr='bad' are INVALID (float() would raise)."""
    fake_runner.results = [
        {"sharpe": 0.1, "net_profit": 0.01, "mdd": 0.08, "trades": [], "dsr": "bad"},
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["INVALID"]


def test_non_numeric_subwindow_entry_classified_invalid(fake_runner, tmp_path):
    """Defect 1: a string entry in subwindow_sharpes is INVALID."""
    fake_runner.results = [
        {
            "sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 5,
            "subwindow_sharpes": ["x", 1.0],
        },
    ]
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl"
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["INVALID"]


def test_non_finite_in_non_gate_metric_classified_invalid(fake_runner, tmp_path):
    """Defect 3: a NaN in sortino (NOT a gate/ranking key) is INVALID,
    not SUCCEEDED. Without the fix, record_outcome's schema finite-check
    would reject it and leave a dangling PENDING."""
    fake_runner.results = [
        {
            "sharpe": 1.0, "net_profit": 0.1, "mdd": 0.08, "trades": 50,
            "sortino": float("nan"),
        },
    ]
    journal_path = tmp_path / "events.jsonl"
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, journal_path
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert [a.event_type for a in result.attempts] == ["INVALID"]
    records = EventJournal(journal_path).read_all()
    assert [r["event_type"] for r in records] == ["REGISTERED", "INVALID"]
    assert records[-1]["metrics"] is None


def test_preregistration_degradation_delta_not_absolute_mdd_cap(fake_runner, tmp_path):
    """Defect 2: success_thresholds.max_drawdown_degradation is a
    CROSS-GENERATION DELTA (design §13), NOT an absolute MDD cap. A G1
    candidate with mdd=0.15 must NOT be pruned when the preregistration's
    max_drawdown_degradation=0.10 (the old conflation pruned it, falsely
    forcing G1->G0 alias on a viable 15%-drawdown strategy)."""
    fake_runner.results = [
        {"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.15, "trades": 50},
    ]
    prereg = {"success_thresholds": {"max_drawdown_degradation": 0.10}}
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl", preregistration=prereg
    ).run({"flag": {"type": "bool"}}, "W1/train")
    # mdd=0.15 is below the default absolute cap (0.5); the degradation
    # delta 0.10 is NOT applied as an absolute cap at G1.
    assert [a.event_type for a in result.attempts] == ["SUCCEEDED"]
    assert result.selected_candidate is not None


def test_preregistration_candidate_gates_max_mdd_honored(fake_runner, tmp_path):
    """Defect 2 follow-up: an explicit candidate_gates.max_mdd IS honored
    as the absolute cap (the correct source for the absolute gate)."""
    fake_runner.results = [
        {"sharpe": 1.5, "net_profit": 0.2, "mdd": 0.15, "trades": 50},  # > 0.10 cap
    ]
    prereg = {
        "success_thresholds": {"max_drawdown_degradation": 0.50},
        "candidate_gates": {"max_mdd": 0.10},
    }
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, tmp_path / "events.jsonl", preregistration=prereg
    ).run({"flag": {"type": "bool"}}, "W1/train")
    # max_mdd=0.10 (absolute, from candidate_gates) prunes mdd=0.15.
    assert [a.event_type for a in result.attempts] == ["PRUNED"]
    assert result.selected_candidate is None


def test_runner_returning_none_is_failed_infrastructure_no_dangling(fake_runner, tmp_path):
    """Defect 4: a runner returning None (contract violation) is
    FAILED_INFRASTRUCTURE, not an AttributeError crash. No dangling
    PENDING - the journal has REGISTERED then FAILED_INFRASTRUCTURE."""
    fake_runner.results = [None]
    journal_path = tmp_path / "events.jsonl"
    result = ParameterOptimizerAdapter(
        fake_runner, 1, 17, journal_path
    ).run({"flag": {"type": "bool"}}, "W1/train")
    assert result.attempted_trial_count == 1
    assert [a.status for a in result.attempts] == ["FAILED_INFRASTRUCTURE"]
    assert [a.event_type for a in result.attempts] == ["FAILED_INFRASTRUCTURE"]
    assert result.selected_candidate is None
    records = EventJournal(journal_path).read_all()
    assert [r["event_type"] for r in records] == [
        "REGISTERED",
        "FAILED_INFRASTRUCTURE",
    ]


def test_candidate_id_has_no_path_separator(tmp_path):
    """C1: candidate_id must never contain '/' so run_id (and thus the LEAN
    result-packet path <run_dir>/<run_id>.json) cannot nest into a missing
    subdir. partition may carry 'W1/train' as a string field; the candidate
    id must sanitize it."""
    calls = []

    def runner(req):
        calls.append(req)
        return TrialResult(status="SUCCEEDED",
                          metrics={"sharpe": 1.0, "net_profit": 0.1,
                                   "mdd": 0.05, "trades": 5, "dsr": 0.5},
                          error=None)

    opt = ParameterOptimizerAdapter(
        runner, budget=4, seed=17,
        journal_path=tmp_path / "g1.jsonl",
        stage_id="G1", window_id="W1",
    )
    opt.run(
        {"trend-ma-short": {"type": "choice", "values": ["20", "30"]},
         "vol-target": {"type": "choice", "values": ["0.11", "0.15"]}},
        "W1/train",  # partition carries a slash (the real P2 wiring passes this)
    )
    for req in calls:
        assert "/" not in req.candidate_id, (
            f"candidate_id {req.candidate_id!r} contains '/'; run_id would "
            f"nest the LEAN packet into a missing subdir")
        assert "\\" not in req.candidate_id
        assert req.partition == "W1/train", (
            f"partition field {req.partition!r} was sanitized; only candidate_id "
            f"may be sanitized — partition must stay original in the journal")
