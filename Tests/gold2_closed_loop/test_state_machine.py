"""Failing contracts for the lifecycle state machine (Task 9, Phase 2 STOP gate).

Enforces the closed enums and the legal transition edges defined in design §5
``NEW -> PREREGISTERED -> ... -> SEALED``. Any edge not listed in §5 must raise.
"""

from __future__ import annotations

import pytest

from Scripts.gold2_closed_loop.state_machine import (
    BlockReason,
    CandidateEventType,
    CandidateState,
    ConvergenceStatus,
    Disposition,
    ExecutionStatus,
    G3AliasReason,
    GenerationTerminalReason,
    HistoricalVerdict,
    InvalidReason,
    LifecycleStatus,
    ValidityStatus,
    transition,
)


# --- enum surface (design §5 + §7) ----------------------------------


def test_lifecycle_status_enum_values():
    assert set(LifecycleStatus) == {
        LifecycleStatus.NEW,
        LifecycleStatus.PREREGISTERED,
        LifecycleStatus.DATA_VALIDATED,
        LifecycleStatus.RUNNING_CONSTRUCTION,
        LifecycleStatus.CANDIDATES_FROZEN,
        LifecycleStatus.RUNNING_BLIND_TEST,
        LifecycleStatus.BLIND_TEST_COMPLETE,
        LifecycleStatus.SEALED,
        LifecycleStatus.BLOCKED,
        LifecycleStatus.INVALID,
        LifecycleStatus.FAILED_EXECUTION,
    }


def test_lifecycle_status_string_values_exact():
    expected = {
        "NEW",
        "PREREGISTERED",
        "DATA_VALIDATED",
        "RUNNING_CONSTRUCTION",
        "CANDIDATES_FROZEN",
        "RUNNING_BLIND_TEST",
        "BLIND_TEST_COMPLETE",
        "SEALED",
        "BLOCKED",
        "INVALID",
        "FAILED_EXECUTION",
    }
    assert {member.value for member in LifecycleStatus} == expected


def test_validity_status_enum():
    assert {member.value for member in ValidityStatus} == {"VALID", "INVALID"}


def test_invalid_reason_enum():
    assert {member.value for member in InvalidReason} == {
        "INFORMATION_LEAKAGE",
        "MISSING_FORMAL_TRACE",
        "EVIDENCE_CAPTURE_FAILED",
        "HASH_MISMATCH",
        "POST_BLIND_MUTATION",
        "DATA_INVALID",
    }


def test_block_reason_enum():
    assert {member.value for member in BlockReason} == {
        "BLOCKED_INSUFFICIENT_TEST_WINDOWS",
        "BLOCKED_INTERFACE_NOT_PROOF_READY",
        "BLOCKED_INSUFFICIENT_DATA",
    }


def test_execution_status_enum():
    assert {member.value for member in ExecutionStatus} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED_STRATEGY",
        "FAILED_INFRASTRUCTURE",
        "FAILED_EVIDENCE_CAPTURE",
        "TIMED_OUT",
        "NO_TRADES",
    }


def test_historical_verdict_enum():
    assert {member.value for member in HistoricalVerdict} == {
        "EFFECTIVE",
        "PARTIALLY_EFFECTIVE",
        "NOT_EFFECTIVE",
        "NOT_EVALUATED",
    }


def test_disposition_enum():
    assert {member.value for member in Disposition} == {"UNPROVEN", "REFUTED"}


def test_candidate_event_type_enum():
    assert {member.value for member in CandidateEventType} == {
        "REGISTERED",
        "SUCCEEDED",
        "FAILED_STRATEGY",
        "FAILED_INFRASTRUCTURE",
        "FAILED_EVIDENCE_CAPTURE",
        "TIMED_OUT",
        "NO_TRADES",
        "PRUNED",
        "DUPLICATE",
        "DOMINATED",
        "REJECTED",
        "INVALID",
    }


def test_candidate_state_enum():
    assert {member.value for member in CandidateState} == {
        "PENDING",
        "SUCCEEDED",
        "FAILED",
        "PRUNED",
        "DOMINATED",
        "REJECTED",
        "INVALID",
    }


def test_convergence_status_enum():
    assert {member.value for member in ConvergenceStatus} == {
        "CONVERGED",
        "NON_CONVERGED",
        "UNOBSERVABLE",
    }


def test_generation_terminal_reason_enum():
    assert {member.value for member in GenerationTerminalReason} == {
        "BUDGET_EXHAUSTED",
        "TRIGGERED",
        "CONVERGED",
        "EXECUTION_FAILED",
        "INVALID_EVIDENCE",
    }


def test_g3_alias_reason_enum():
    assert {member.value for member in G3AliasReason} == {
        "NOT_TRIGGERED",
        "TRIGGER_UNOBSERVABLE",
        "CONSTRUCTION_FAILED",
        "CANDIDATE_REJECTED",
    }


# --- legal transitions (design §5 lines 213-221) --------------------


def test_legal_transitions_core_lifecycle():
    assert transition(LifecycleStatus.NEW, LifecycleStatus.PREREGISTERED) == LifecycleStatus.PREREGISTERED
    assert transition(LifecycleStatus.PREREGISTERED, LifecycleStatus.DATA_VALIDATED) == LifecycleStatus.DATA_VALIDATED
    assert transition(LifecycleStatus.PREREGISTERED, LifecycleStatus.BLOCKED) == LifecycleStatus.BLOCKED
    assert transition(LifecycleStatus.PREREGISTERED, LifecycleStatus.INVALID) == LifecycleStatus.INVALID
    assert transition(LifecycleStatus.DATA_VALIDATED, LifecycleStatus.RUNNING_CONSTRUCTION) == LifecycleStatus.RUNNING_CONSTRUCTION
    assert transition(LifecycleStatus.RUNNING_CONSTRUCTION, LifecycleStatus.CANDIDATES_FROZEN) == LifecycleStatus.CANDIDATES_FROZEN
    assert transition(LifecycleStatus.RUNNING_CONSTRUCTION, LifecycleStatus.FAILED_EXECUTION) == LifecycleStatus.FAILED_EXECUTION
    assert transition(LifecycleStatus.RUNNING_CONSTRUCTION, LifecycleStatus.INVALID) == LifecycleStatus.INVALID
    assert transition(LifecycleStatus.CANDIDATES_FROZEN, LifecycleStatus.RUNNING_BLIND_TEST) == LifecycleStatus.RUNNING_BLIND_TEST
    assert transition(LifecycleStatus.RUNNING_BLIND_TEST, LifecycleStatus.BLIND_TEST_COMPLETE) == LifecycleStatus.BLIND_TEST_COMPLETE
    assert transition(LifecycleStatus.RUNNING_BLIND_TEST, LifecycleStatus.FAILED_EXECUTION) == LifecycleStatus.FAILED_EXECUTION
    assert transition(LifecycleStatus.RUNNING_BLIND_TEST, LifecycleStatus.INVALID) == LifecycleStatus.INVALID
    assert transition(LifecycleStatus.BLIND_TEST_COMPLETE, LifecycleStatus.SEALED) == LifecycleStatus.SEALED
    assert transition(LifecycleStatus.BLIND_TEST_COMPLETE, LifecycleStatus.INVALID) == LifecycleStatus.INVALID


def test_illegal_transition_new_to_sealed_raises():
    with pytest.raises(ValueError) as exc:
        transition(LifecycleStatus.NEW, LifecycleStatus.SEALED)
    msg = str(exc.value)
    assert "NEW" in msg
    assert "SEALED" in msg


def test_illegal_transition_running_construction_to_preregistered_raises():
    with pytest.raises(ValueError) as exc:
        transition(LifecycleStatus.RUNNING_CONSTRUCTION, LifecycleStatus.PREREGISTERED)
    msg = str(exc.value)
    assert "RUNNING_CONSTRUCTION" in msg
    assert "PREREGISTERED" in msg


def test_terminal_status_blocked_has_no_outgoing_edges():
    for target in LifecycleStatus:
        with pytest.raises(ValueError):
            transition(LifecycleStatus.BLOCKED, target)


def test_terminal_status_invalid_has_no_outgoing_edges():
    for target in LifecycleStatus:
        with pytest.raises(ValueError):
            transition(LifecycleStatus.INVALID, target)


def test_terminal_status_failed_execution_has_no_outgoing_edges():
    for target in LifecycleStatus:
        with pytest.raises(ValueError):
            transition(LifecycleStatus.FAILED_EXECUTION, target)


def test_terminal_status_sealed_has_no_outgoing_edges():
    for target in LifecycleStatus:
        with pytest.raises(ValueError):
            transition(LifecycleStatus.SEALED, target)


def test_transition_accepts_str_and_returns_str_enum():
    result = transition("NEW", "PREREGISTERED")
    assert result == LifecycleStatus.PREREGISTERED
    assert isinstance(result, LifecycleStatus)


def test_transition_rejects_unknown_status():
    with pytest.raises((ValueError, KeyError)):
        transition("WAT", LifecycleStatus.NEW)
    with pytest.raises((ValueError, KeyError)):
        transition(LifecycleStatus.NEW, "WAT")


def test_self_transition_not_listed_is_illegal():
    for current in LifecycleStatus:
        with pytest.raises(ValueError):
            transition(current, current)
