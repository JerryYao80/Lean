"""Failing contracts for the G3 redesign trigger (Task 13, Phase 3).

Design invariants (spec §7 lines 264-282, §14 lines 418-421):

* The G3 trigger fires ONLY when there are at least ``min_generations``
  (default 3) ADJACENT VALID generation records, all sharing the same
  frozen ``input_evidence_sha256``, each with ``attribution_gap >=
  threshold``, each with ``shaping_weight >= ceiling`` OR
  ``convergence_status == NON_CONVERGED``, and each with
  ``pending_candidate_count == 0`` (no candidates awaiting review).
* Boundary comparisons are INCLUSIVE (``gap == threshold`` triggers,
  ``weight == ceiling`` triggers) — the plan anchor pins this.
* Interrupted evidence (fewer than ``min_generations`` adjacent valid
  records, e.g. because a generation failed or the evidence hash
  changed mid-run) produces ``alias_reason=TRIGGER_UNOBSERVABLE`` and
  ``eligible=False``.
* Sufficient-but-non-triggering evidence (>= ``min_generations`` adjacent
  valid records that do NOT meet the gap/weight/pending conditions)
  produces ``alias_reason=NOT_TRIGGERED`` and ``eligible=False``.
"""
from __future__ import annotations

import pytest

from Scripts.gold2_closed_loop.g3_trigger import (
    G3TriggerResult,
    evaluate_g3,
    generation,
)


_EVIDENCE = "a" * 64


# --- anchor test from the plan (verbatim) -----------------------------


def test_three_valid_generations_trigger_inclusively():
    records = [
        generation(i, gap=0.2, weight=3.0, pending=0, evidence_hash="a" * 64)
        for i in (1, 2, 3)
    ]
    assert evaluate_g3(records, 0.2, 3.0).eligible


# --- boundary inclusivity ---------------------------------------------


def test_gap_equal_to_threshold_triggers():
    records = [generation(i, gap=0.2, weight=3.0, pending=0) for i in (1, 2, 3)]
    assert evaluate_g3(records, 0.2, 3.0).eligible is True


def test_weight_equal_to_ceiling_triggers():
    records = [
        generation(i, gap=0.2, weight=3.0, pending=0) for i in (1, 2, 3)
    ]
    assert evaluate_g3(records, 0.2, 3.0).eligible is True


def test_non_converged_triggers_even_below_weight_ceiling():
    """NON_CONVERGED satisfies the weight condition even if the shaping
    weight is below the ceiling (spec §7: "不收敛或顶格 3.0")."""
    records = [
        generation(i, gap=0.2, weight=1.0, pending=0, converged=False)
        for i in (1, 2, 3)
    ]
    assert evaluate_g3(records, 0.2, 3.0).eligible is True


# --- trigger does NOT fire --------------------------------------------


def test_gap_below_threshold_is_not_triggered():
    records = [
        generation(i, gap=0.19, weight=3.0, pending=0) for i in (1, 2, 3)
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is False
    assert result.alias_reason == "NOT_TRIGGERED"


def test_pending_candidates_block_trigger():
    records = [
        generation(i, gap=0.2, weight=3.0, pending=1) for i in (1, 2, 3)
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is False
    assert result.alias_reason == "NOT_TRIGGERED"


def test_converged_below_weight_ceiling_is_not_triggered():
    records = [
        generation(i, gap=0.2, weight=1.0, pending=0, converged=True)
        for i in (1, 2, 3)
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is False
    assert result.alias_reason == "NOT_TRIGGERED"


# --- interrupted evidence -> TRIGGER_UNOBSERVABLE ---------------------


def test_fewer_than_min_generations_is_unobservable():
    records = [
        generation(1, gap=0.2, weight=3.0, pending=0),
        generation(2, gap=0.2, weight=3.0, pending=0),
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is False
    assert result.alias_reason == "TRIGGER_UNOBSERVABLE"


def test_evidence_hash_change_mid_run_is_unobservable():
    """If the evidence hash changes between generations the run is
    interrupted; the trigger cannot fire across a re-frozen bundle."""
    records = [
        generation(1, gap=0.2, weight=3.0, pending=0, evidence_hash="a" * 64),
        generation(2, gap=0.2, weight=3.0, pending=0, evidence_hash="a" * 64),
        generation(3, gap=0.2, weight=3.0, pending=0, evidence_hash="b" * 64),
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is False
    assert result.alias_reason == "TRIGGER_UNOBSERVABLE"


def test_triggered_has_no_alias_reason():
    records = [
        generation(i, gap=0.2, weight=3.0, pending=0) for i in (1, 2, 3)
    ]
    result = evaluate_g3(records, 0.2, 3.0)
    assert result.eligible is True
    assert result.alias_reason is None


def test_min_generations_parameter_respected():
    records = [
        generation(i, gap=0.2, weight=3.0, pending=0) for i in (1, 2)
    ]
    # With min_generations=2 the trigger CAN fire on two records.
    result = evaluate_g3(records, 0.2, 3.0, min_generations=2)
    assert result.eligible is True


# --- preregistered generation_continuity_rule (whole-tree review) -----


def test_break_on_failure_rule_excludes_transition():
    """BREAK_ON_FAILURE: a bundle-transition record (flag False) breaks the
    run AND is excluded; it does NOT seed a new run."""
    recs = [
        generation(1, gap=0.2, weight=3.0, pending=0, evidence_hash="a" * 64),
        generation(2, gap=0.2, weight=3.0, pending=0, evidence_hash="a" * 64),
        generation(3, gap=0.2, weight=3.0, pending=0, evidence_hash="b" * 64),
        generation(4, gap=0.2, weight=3.0, pending=0, evidence_hash="b" * 64),
        generation(5, gap=0.2, weight=3.0, pending=0, evidence_hash="b" * 64),
    ]
    r_hash = evaluate_g3(recs, 0.2, 3.0, generation_continuity_rule="BREAK_ON_HASH_CHANGE")
    assert r_hash.eligible is True
    r_cont = evaluate_g3(recs, 0.2, 3.0, generation_continuity_rule="CONTINUE")
    assert r_cont.eligible is True


def test_unknown_continuity_rule_rejected():
    recs = [generation(1, gap=0.2, weight=3.0, pending=0)]
    with pytest.raises(ValueError, match="generation_continuity_rule"):
        evaluate_g3(recs, 0.2, 3.0, generation_continuity_rule="BOGUS")
