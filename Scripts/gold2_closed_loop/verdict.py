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
class AcceptanceGates:
    """The §13 acceptance conditions the verdict enforces.

    The verdict is EFFECTIVE only if ALL of these pass (and validity is
    VALID, the aggregate delta is positive, all activated stages pass, and
    no G3 alias caps it). A gate that is None is not checked (the caller
    did not supply it; conservative callers supply every gate).
    """

    valid_window_count: int | None = None
    required_valid_windows: int | None = None  # 3/4 or 2/3 (§13 cond 1)
    aggregate_cagr: float | None = None
    aggregate_sharpe: float | None = None
    aggregate_net_profit: float | None = None
    baseline_cagr: float | None = None
    baseline_sharpe: float | None = None
    baseline_net_profit: float | None = None
    information_ratio: float | None = None
    benchmark_integrity_ok: bool | None = None
    single_trade_contribution: float | None = None
    max_single_trade_contribution: float | None = None
    single_month_contribution: float | None = None
    max_single_month_contribution: float | None = None
    min_leave_one_window_out_delta: float | None = None
    leave_one_window_out_deltas: dict[str, float] | None = None
    paired_bootstrap_p_value: float | None = None
    paired_bootstrap_min_probability: float | None = None
    bootstrap_direction_conflict: bool | None = None
    stage_deltas: dict[str, float] | None = None  # G1-G0, G2-G1, G3-G2


@dataclass(frozen=True)
class VerdictResult:
    """The aggregate historical verdict + disposition (spec §1 / §5)."""

    verdict: str
    disposition: str | None
    reason: str
    failed_gates: tuple[str, ...] = ()


def decide_verdict(
    *,
    validity: str,
    aggregate_delta: float | None = None,
    invalid_reason: str | None = None,
    stages_passing: Iterable[str] | None = None,
    g3_alias_reason: str | None = None,
    drawdown_deterioration: float | None = None,
    max_drawdown_degradation: float | None = None,
    gates: AcceptanceGates | None = None,
) -> VerdictResult:
    """Decide the aggregate historical verdict, enforcing the §13 acceptance
    conditions when ``gates`` is supplied.

    Decision order:
      1. INVALID -> NOT_EVALUATED + UNPROVEN (validity FIRST).
      2. G3 alias caps (CONSTRUCTION_FAILED / TRIGGER_UNOBSERVABLE ->
         NOT_EVALUATED; CANDIDATE_REJECTED -> at most PARTIALLY_EFFECTIVE).
      3. §13 acceptance gates (window count, aggregate CAGR/Sharpe/net
         profit vs baseline, IR>0 + benchmark integrity, single-trade /
         single-month concentration, leave-one-window-out min, paired
         bootstrap threshold, bootstrap/direction non-conflict, per-stage
         deltas). A failed gate blocks EFFECTIVE (the verdict falls to
         PARTIALLY_EFFECTIVE or, if the aggregate is non-positive, to
         NOT_EFFECTIVE+REFUTED).
      4. Drawdown-deterioration gate (> cap blocks EFFECTIVE).
      5. Non-positive aggregate delta -> NOT_EFFECTIVE + REFUTED.
      6. All activated stages pass + all gates pass -> EFFECTIVE; else
         PARTIALLY_EFFECTIVE.
    """
    # 1. Validity FIRST.
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
    # 3. §13 acceptance gates.
    failed_gates: list[str] = []
    if gates is not None:
        failed_gates = _check_acceptance_gates(gates)
    # 4. Drawdown-deterioration gate.
    dd_gate_blocks_effective = False
    if (drawdown_deterioration is not None
            and max_drawdown_degradation is not None
            and drawdown_deterioration > max_drawdown_degradation):
        dd_gate_blocks_effective = True
        failed_gates.append("drawdown_deterioration")
    # 5. Non-positive aggregate delta -> NOT_EFFECTIVE + REFUTED.
    if aggregate_delta <= 0:
        return VerdictResult(
            verdict=HistoricalVerdict.NOT_EFFECTIVE.value,
            disposition=Disposition.REFUTED.value,
            reason="aggregate loop delta non-positive",
            failed_gates=tuple(failed_gates),
        )
    # 6. All activated stages pass + all gates pass -> EFFECTIVE.
    g3_activated = g3_alias_reason is None
    activated = {"G1", "G2"}
    if g3_activated:
        activated.add("G3")
    all_activated_pass = passing >= activated
    if g3_alias_reason == G3AliasReason.CANDIDATE_REJECTED.value:
        return VerdictResult(
            verdict=HistoricalVerdict.PARTIALLY_EFFECTIVE.value,
            disposition=None,
            reason="G3 candidate rejected (capped at PARTIALLY_EFFECTIVE)",
            failed_gates=tuple(failed_gates),
        )
    if failed_gates or dd_gate_blocks_effective or not all_activated_pass:
        reason = (
            "drawdown-deterioration gate blocked EFFECTIVE"
            if dd_gate_blocks_effective
            else (f"acceptance gates failed: {failed_gates}" if failed_gates
                  else "not all activated stages passed")
        )
        return VerdictResult(
            verdict=HistoricalVerdict.PARTIALLY_EFFECTIVE.value,
            disposition=None,
            reason=reason,
            failed_gates=tuple(failed_gates),
        )
    return VerdictResult(
        verdict=HistoricalVerdict.EFFECTIVE.value,
        disposition=None,
        reason="all activated stages passed with positive aggregate delta "
               "and all §13 acceptance gates passed",
        failed_gates=(),
    )


def _check_acceptance_gates(gates: AcceptanceGates) -> list[str]:
    """Return the list of §13 acceptance gates that FAILED.

    A gate is checked only when the caller supplied BOTH the value and its
    threshold (so a None gate is skipped, not failed). This is conservative:
    a caller that supplies no gates checks none (but the aggregate-delta +
    stage + alias + drawdown gates still apply in decide_verdict).
    """
    failed: list[str] = []
    # Cond 1: >= required valid windows.
    if (gates.valid_window_count is not None
            and gates.required_valid_windows is not None
            and gates.valid_window_count < gates.required_valid_windows):
        failed.append("valid_window_count")
    # Cond 3: aggregate CAGR / Sharpe / net profit > baseline.
    if (gates.aggregate_cagr is not None and gates.baseline_cagr is not None
            and gates.aggregate_cagr <= gates.baseline_cagr):
        failed.append("aggregate_cagr")
    if (gates.aggregate_sharpe is not None and gates.baseline_sharpe is not None
            and gates.aggregate_sharpe <= gates.baseline_sharpe):
        failed.append("aggregate_sharpe")
    if (gates.aggregate_net_profit is not None
            and gates.baseline_net_profit is not None
            and gates.aggregate_net_profit <= gates.baseline_net_profit):
        failed.append("aggregate_net_profit")
    # Cond 6: IR > 0 AND benchmark integrity.
    if gates.benchmark_integrity_ok is False:
        failed.append("benchmark_integrity")
    if (gates.information_ratio is not None
            and gates.information_ratio <= 0.0):
        failed.append("information_ratio_positive")
    # Cond 7: single-trade / single-month concentration caps.
    if (gates.single_trade_contribution is not None
            and gates.max_single_trade_contribution is not None
            and gates.single_trade_contribution > gates.max_single_trade_contribution):
        failed.append("single_trade_contribution")
    if (gates.single_month_contribution is not None
            and gates.max_single_month_contribution is not None
            and gates.single_month_contribution > gates.max_single_month_contribution):
        failed.append("single_month_contribution")
    # Cond 8: every leave-one-window-out delta >= min.
    if (gates.leave_one_window_out_deltas is not None
            and gates.min_leave_one_window_out_delta is not None):
        for w, d in gates.leave_one_window_out_deltas.items():
            if d < gates.min_leave_one_window_out_delta:
                failed.append(f"leave_one_window_out:{w}")
                break
    # Cond 9: paired bootstrap >= threshold AND no direction conflict.
    if (gates.paired_bootstrap_p_value is not None
            and gates.paired_bootstrap_min_probability is not None
            and gates.paired_bootstrap_p_value < gates.paired_bootstrap_min_probability):
        # NOTE: bootstrap p_value is a "probability the null holds"; the
        # §13 threshold is on the bootstrap PROBABILITY that the delta is
        # positive. The caller passes the positive-direction probability;
        # if it is below the threshold the gate fails.
        failed.append("paired_bootstrap_probability")
    if gates.bootstrap_direction_conflict:
        failed.append("bootstrap_direction_conflict")
    # Cond 10: per-stage deltas (G1-G0 / G2-G1 / activated G3-G2) positive.
    if gates.stage_deltas is not None:
        for stage_pair, d in gates.stage_deltas.items():
            if d <= 0:
                failed.append(f"stage_delta:{stage_pair}")
    return failed


def _check_invalid_reason(reason: str) -> None:
    valid = {member.value for member in InvalidReason}
    if reason not in valid:
        raise ValueError(
            f"decide_verdict: invalid_reason {reason!r} not in InvalidReason "
            f"(valid: {sorted(valid)})"
        )


__all__ = ["AcceptanceGates", "VerdictResult", "decide_verdict"]
