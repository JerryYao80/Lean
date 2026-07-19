"""Failing contracts for the verdict decision (Task 16, Phase 4).

Design invariants (spec §1 lines 18-24, §5 lines 197-198, §13 lines
386-411, §14):

* Verdict/disposition pairing is CLOSED (spec §5 line 198):
    NOT_EVALUATED <-> UNPROVEN
    NOT_EFFECTIVE  <-> REFUTED
    EFFECTIVE / PARTIALLY_EFFECTIVE <-> None
* A VALID experiment with a non-positive aggregate delta -> NOT_EFFECTIVE +
  REFUTED (spec §14 line 411).
* An INVALID experiment (e.g. MISSING_FORMAL_TRACE) -> NOT_EVALUATED +
  UNPROVEN (spec §14 line 411).
* G3 alias reasons cap the verdict: CANDIDATE_REJECTED -> at most
  PARTIALLY_EFFECTIVE; CONSTRUCTION_FAILED -> NOT_EVALUATED; NOT_TRIGGERED
  allows EFFECTIVE but the report must say "redesign not needed", not
  "redesign effective".
"""
from __future__ import annotations

import pytest

from Scripts.gold2_closed_loop.verdict import decide_verdict, VerdictResult
from Scripts.gold2_closed_loop.state_machine import G3AliasReason


# --- anchor tests from the plan (verbatim) ---------------------------


def test_valid_negative_is_refuted():
    result = decide_verdict(validity="VALID", aggregate_delta=-.01)
    assert (result.verdict, result.disposition) == ("NOT_EFFECTIVE", "REFUTED")


def test_missing_trace_is_unproven():
    result = decide_verdict(validity="INVALID", invalid_reason="MISSING_FORMAL_TRACE")
    assert (result.verdict, result.disposition) == ("NOT_EVALUATED", "UNPROVEN")


# --- verdict / disposition pairing ----------------------------------


def test_valid_positive_all_stages_pass_is_effective():
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1", "G2", "G3"},
        g3_alias_reason=None,
    )
    assert result.verdict == "EFFECTIVE"
    assert result.disposition is None


def test_partial_stages_passing_is_partially_effective():
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1"},
        g3_alias_reason=None,
    )
    assert result.verdict == "PARTIALLY_EFFECTIVE"
    assert result.disposition is None


def test_candidate_rejected_caps_at_partially_effective():
    """CANDIDATE_REJECTED caps the verdict at PARTIALLY_EFFECTIVE (spec §14
    line 421), even if all stages would otherwise pass."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.10,
        stages_passing={"G1", "G2", "G3"},
        g3_alias_reason=G3AliasReason.CANDIDATE_REJECTED.value,
    )
    assert result.verdict == "PARTIALLY_EFFECTIVE"


def test_construction_failed_is_not_evaluated():
    """CONSTRUCTION_FAILED is an execution failure -> NOT_EVALUATED +
    UNPROVEN (spec §14 line 420)."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.10,
        stages_passing={"G1", "G2", "G3"},
        g3_alias_reason=G3AliasReason.CONSTRUCTION_FAILED.value,
    )
    assert result.verdict == "NOT_EVALUATED"
    assert result.disposition == "UNPROVEN"


def test_not_triggered_allows_effective():
    """NOT_TRIGGERED means redesign was not needed; spec §14 line 418 says
    it "允许完整闭环 verdict" (allows a complete-loop verdict). With G1+G2
    passing and a positive aggregate delta the loop verdict is EFFECTIVE —
    the REPORT (not the verdict) must say "redesign not needed", not
    "redesign effective"."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1", "G2"},
        g3_alias_reason=G3AliasReason.NOT_TRIGGERED.value,
    )
    assert result.verdict == "EFFECTIVE"
    assert result.disposition is None


def test_not_triggered_with_missing_stage_is_partial():
    """If G1 or G2 did NOT pass, NOT_TRIGGERED still yields PARTIAL (the
    loop is incomplete on the activated stages)."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1"},  # G2 did not pass
        g3_alias_reason=G3AliasReason.NOT_TRIGGERED.value,
    )
    assert result.verdict == "PARTIALLY_EFFECTIVE"


def test_trigger_unobservable_is_not_evaluated():
    """TRIGGER_UNOBSERVABLE -> the window's redesign increment is unproven;
    if >=3 windows are unobservable the aggregate is NOT_EVALUATED +
    UNPROVEN (spec §14 line 419)."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1", "G2"},
        g3_alias_reason=G3AliasReason.TRIGGER_UNOBSERVABLE.value,
    )
    assert result.verdict == "NOT_EVALUATED"
    assert result.disposition == "UNPROVEN"


# --- validity-first ordering -----------------------------------------


def test_invalid_overrides_positive_delta():
    """An INVALID experiment is NOT_EVALUATED + UNPROVEN even if the
    aggregate delta is positive (the evidence is unusable)."""
    result = decide_verdict(
        validity="INVALID",
        invalid_reason="INFORMATION_LEAKAGE",
        aggregate_delta=0.50,
    )
    assert result.verdict == "NOT_EVALUATED"
    assert result.disposition == "UNPROVEN"


def test_invalid_missing_invalid_reason_raises():
    with pytest.raises((ValueError, TypeError)):
        decide_verdict(validity="INVALID")


# --- drawdown-deterioration gate ------------------------------------


def test_drawdown_deterioration_exceeding_limit_blocks_effective():
    """If the aggregate drawdown deterioration exceeds 0.10 the verdict
    cannot be EFFECTIVE (spec §13 line 372)."""
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1", "G2", "G3"},
        g3_alias_reason=None,
        drawdown_deterioration=0.15,
        max_drawdown_degradation=0.10,
    )
    assert result.verdict != "EFFECTIVE"


def test_drawdown_deterioration_within_limit_ok():
    result = decide_verdict(
        validity="VALID",
        aggregate_delta=0.05,
        stages_passing={"G1", "G2", "G3"},
        g3_alias_reason=None,
        drawdown_deterioration=0.08,
        max_drawdown_degradation=0.10,
    )
    assert result.verdict == "EFFECTIVE"
