"""Economic verdict decision (Task 16, Phase 4 STOP gate).

Implements the design §1 / §5 / §13 / §14 verdict-decision contract as a
PURE function of (validity, aggregate_delta, stages_passing, g3_alias_reason,
drawdown gates). The verdict is CLOSED:

    EFFECTIVE              <-> disposition None
    PARTIALLY_EFFECTIVE    <-> disposition None
    NOT_EFFECTIVE          <-> REFUTED
    NOT_EVALUATED          <-> UNPROVEN

Decision order (spec §14 line 411):
  1. INVALID (any invalid_reason) -> NOT_EVALUATED + UNPROVEN, regardless of
     the aggregate delta (the evidence is unusable). Validity FIRST.
  2. G3 alias caps:
       CONSTRUCTION_FAILED  -> NOT_EVALUATED + UNPROVEN (execution failure).
       TRIGGER_UNOBSERVABLE  -> NOT_EVALUATED + UNPROVEN (redesign increment
                                unproven; the aggregate cannot be EFFECTIVE).
       CANDIDATE_REJECTED    -> at most PARTIALLY_EFFECTIVE (the rejected
                                stage is a valid negative stage result).
       NOT_TRIGGERED         -> allows a loop verdict on the other stages
                                (redesign was not needed; not a cap).
  3. Aggregate drawdown deterioration > max_drawdown_degradation -> the
     verdict cannot be EFFECTIVE (spec §13 line 372).
  4. Aggregate delta <= 0 (VALID) -> NOT_EFFECTIVE + REFUTED.
  5. All activated stages pass -> EFFECTIVE; a subset -> PARTIALLY_EFFECTIVE.

This module is PROOF-ONLY. It does NOT import the production machinery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from Scripts.gold2_closed_loop.state_machine import (
    Disposition,
    G3AliasReason,
    HistoricalVerdict,
    InvalidReason,
    ValidityStatus,
)

_VALID = ValidityStatus.VALID.value
_INVALID = ValidityStatus.INVALID.value


@dataclass(frozen=True)
class VerdictResult:
    """The aggregate historical verdict + disposition (spec §1 / §5)."""

    verdict: str
    disposition: str | None
    reason: str


def decide_verdict(
    *,
    validity: str,
    aggregate_delta: float | None = None,
    invalid_reason: str | None = None,
    stages_passing: Iterable[str] | None = None,
    g3_alias_reason: str | None = None,
    drawdown_deterioration: float | None = None,
    max_drawdown_degradation: float | None = None,
) -> VerdictResult:
    """Decide the aggregate historical verdict.

    Parameters
    ----------
    validity:
        ``"VALID"`` or ``"INVALID"``.
    aggregate_delta:
        The aggregate loop net increment (ΔLoop = G3 - G0). Required for a
        VALID verdict; ignored for INVALID.
    invalid_reason:
        Required when validity is INVALID (the closed InvalidReason value).
    stages_passing:
        The set of activated stages that passed (subset of {G1, G2, G3}).
        G3 is "activated" only when the trigger fired and the candidate was
        built+accepted (NOT_TRIGGERED means G3 was not needed, so it is not
        counted as a passing stage). Defaults to the empty set.
    g3_alias_reason:
        The G3 alias reason (NOT_TRIGGERED / TRIGGER_UNOBSERVABLE /
        CONSTRUCTION_FAILED / CANDIDATE_REJECTED), or None if G3 fired and
        was accepted.
    drawdown_deterioration / max_drawdown_degradation:
        The aggregate drawdown deterioration and its preregistered cap.
        If the deterioration exceeds the cap, the verdict cannot be
        EFFECTIVE (spec §13 line 372).
    """
    # 1. Validity FIRST: an INVALID experiment is NOT_EVALUATED + UNPROVEN
    # regardless of the aggregate delta.
    if validity == _INVALID:
        if not invalid_reason:
            raise ValueError(
                "decide_verdict: invalid_reason is required when validity=INVALID"
            )
        _check_invalid_reason(invalid_reason)
        return VerdictResult(
            verdict=HistoricalVerdict.NOT_EVALUATED.value,
            disposition=Disposition.UNPROVEN.value,
            reason=f"invalid evidence ({invalid_reason})",
        )
    if validity != _VALID:
        raise ValueError(
            f"decide_verdict: validity must be VALID or INVALID, got {validity!r}"
        )
    if aggregate_delta is None:
        raise ValueError(
            "decide_verdict: aggregate_delta is required for a VALID verdict"
        )
    passing = set(stages_passing or ())
    # 2. G3 alias caps.
    if g3_alias_reason == G3AliasReason.CONSTRUCTION_FAILED.value:
        return VerdictResult(
            verdict=HistoricalVerdict.NOT_EVALUATED.value,
            disposition=Disposition.UNPROVEN.value,
            reason="G3 construction failed (execution failure)",
        )
    if g3_alias_reason == G3AliasReason.TRIGGER_UNOBSERVABLE.value:
        return VerdictResult(
            verdict=HistoricalVerdict.NOT_EVALUATED.value,
            disposition=Disposition.UNPROVEN.value,
            reason="G3 trigger unobservable (redesign increment unproven)",
        )
    # 3. Drawdown-deterioration gate: if it exceeds the cap, EFFECTIVE is
    # blocked (the verdict falls through to the delta-based decision).
    dd_gate_blocks_effective = False
    if (drawdown_deterioration is not None
            and max_drawdown_degradation is not None
            and drawdown_deterioration > max_drawdown_degradation):
        dd_gate_blocks_effective = True
    # 4. Non-positive aggregate delta -> NOT_EFFECTIVE + REFUTED.
    if aggregate_delta <= 0:
        return VerdictResult(
            verdict=HistoricalVerdict.NOT_EFFECTIVE.value,
            disposition=Disposition.REFUTED.value,
            reason="aggregate loop delta non-positive",
        )
    # 5. All activated stages pass -> EFFECTIVE; a subset -> PARTIALLY.
    # G3 is "activated" only if it fired and was accepted (no alias). When
    # NOT_TRIGGERED, G3 was not needed -> the loop verdict is on G1/G2 only,
    # so a {G1, G2} passing set is "all activated stages".
    g3_activated = g3_alias_reason is None
    activated = {"G1", "G2"}
    if g3_activated:
        activated.add("G3")
    all_activated_pass = passing >= activated
    # CANDIDATE_REJECTED caps at PARTIALLY_EFFECTIVE (the rejected G3 is a
    # valid negative stage result; the loop cannot be fully EFFECTIVE).
    if g3_alias_reason == G3AliasReason.CANDIDATE_REJECTED.value:
        return VerdictResult(
            verdict=HistoricalVerdict.PARTIALLY_EFFECTIVE.value,
            disposition=None,
            reason="G3 candidate rejected (capped at PARTIALLY_EFFECTIVE)",
        )
    if dd_gate_blocks_effective or not all_activated_pass:
        return VerdictResult(
            verdict=HistoricalVerdict.PARTIALLY_EFFECTIVE.value,
            disposition=None,
            reason=(
                "drawdown-deterioration gate blocked EFFECTIVE"
                if dd_gate_blocks_effective
                else "not all activated stages passed"
            ),
        )
    return VerdictResult(
        verdict=HistoricalVerdict.EFFECTIVE.value,
        disposition=None,
        reason="all activated stages passed with positive aggregate delta",
    )


def _check_invalid_reason(reason: str) -> None:
    valid = {member.value for member in InvalidReason}
    if reason not in valid:
        raise ValueError(
            f"decide_verdict: invalid_reason {reason!r} not in InvalidReason "
            f"(valid: {sorted(valid)})"
        )


__all__ = ["VerdictResult", "decide_verdict"]
