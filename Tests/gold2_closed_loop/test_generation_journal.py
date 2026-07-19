"""Failing contracts for the window-local generation journal (Task 13, Phase 3).

Design invariants (spec §6 line 242, §7 "Generation semantics" lines 264-282):

* Generation records are APPEND-ONLY per window. Each record carries
  ``generation_id``, ``generation_index``, ``parent_generation_id``,
  ``generation_cutoff``, ``input_evidence_sha256``,
  ``candidate_set_sha256``, ``attribution_gap``, ``shaping_weight``,
  ``pending_candidate_count``, ``convergence_status``,
  ``trigger_observation_valid``, ``terminal_reason``.
* Records are parent-linked (each generation's parent is the prior
  generation's id) and collision-rejecting (a duplicate generation_id
  raises).
* The journal is hash-chained like the candidate-event journal (each
  record carries the prior record's sha256) and uses an exclusive lock.
* "连续三代" (three adjacent valid generations) operate on the same
  frozen ``input_evidence_sha256`` (spec §6 line 246); a generation whose
  input evidence hash differs from its parent flags
  ``trigger_observation_valid=False`` so the trigger cannot fire across a
  re-frozen bundle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.generation_journal import (
    GenerationJournal,
    GenerationRecord,
    generation,
)


_EVIDENCE = "a" * 64


def _rec(index: int, *, evidence_hash: str = _EVIDENCE, gap: float = 0.2,
         weight: float = 3.0, pending: int = 0, parent: str | None = None,
         gen_id: str | None = None) -> GenerationRecord:
    return generation(
        index=index, gap=gap, weight=weight, pending=pending,
        evidence_hash=evidence_hash, parent=parent, gen_id=gen_id,
    )


# --- append-only + parent linking -------------------------------------


def test_append_sets_parent_to_previous_generation(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1))
    second = journal.append(_rec(2, parent=first.generation_id))
    assert second.parent_generation_id == first.generation_id
    assert second.generation_index == 2


def test_append_rejects_duplicate_generation_id(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    journal.append(_rec(1, gen_id="gen-1"))
    with pytest.raises(ValueError, match="duplicate"):
        journal.append(_rec(2, gen_id="gen-1"))


def test_append_rejects_wrong_parent(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1))
    # Parent points at a nonexistent generation.
    with pytest.raises(ValueError, match="parent"):
        journal.append(_rec(2, parent="nonexistent-parent-id"))


def test_append_rejects_non_monotonic_index(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    journal.append(_rec(1))
    with pytest.raises(ValueError, match="index"):
        journal.append(_rec(1, parent="gen-1"))


def test_records_carry_all_design_fields(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    rec = journal.append(_rec(1))
    for field in (
        "generation_id", "generation_index", "parent_generation_id",
        "generation_cutoff", "input_evidence_sha256",
        "candidate_set_sha256", "attribution_gap", "shaping_weight",
        "pending_candidate_count", "convergence_status",
        "trigger_observation_valid", "terminal_reason",
    ):
        assert hasattr(rec, field), f"missing field {field}"


# --- evidence-hash stability across generations -----------------------


def test_changing_evidence_hash_flags_invalid_trigger_observation(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1, evidence_hash=_EVIDENCE))
    second = journal.append(_rec(2, evidence_hash="b" * 64,
                                  parent=first.generation_id))
    assert second.trigger_observation_valid is False


def test_same_evidence_hash_keeps_trigger_observation_valid(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1, evidence_hash=_EVIDENCE))
    second = journal.append(_rec(2, evidence_hash=_EVIDENCE,
                                  parent=first.generation_id))
    assert second.trigger_observation_valid is True


# --- hash chain --------------------------------------------------------


def test_generation_journal_hash_chain(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1))
    second = journal.append(_rec(2, parent=first.generation_id))
    assert first.previous_record_sha256 == "0" * 64
    assert second.previous_record_sha256 == first.record_sha256
    assert first.record_sha256 == second.previous_record_sha256


def test_read_all_round_trips_records(tmp_path):
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    first = journal.append(_rec(1))
    journal.append(_rec(2, parent=first.generation_id))
    records = journal.read_all()
    assert len(records) == 2
    assert records[0].generation_index == 1
    assert records[1].generation_index == 2


def test_observable_valid_count_counts_valid_generations(tmp_path):
    """`observable_valid_count` returns the run of adjacent valid records
    ending at the tail — the basis for the §7 "three adjacent valid
    generations" trigger."""
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    g1 = journal.append(_rec(1))
    g2 = journal.append(_rec(2, parent=g1.generation_id))
    g3 = journal.append(_rec(3, parent=g2.generation_id))
    assert journal.observable_valid_count() == 3


def test_invalid_evidence_resets_observable_run(tmp_path):
    """A generation whose evidence hash differs from its parent breaks the
    adjacent-valid run for trigger purposes."""
    journal = GenerationJournal(tmp_path / "gen.jsonl")
    g1 = journal.append(_rec(1))
    g2 = journal.append(_rec(2, parent=g1.generation_id))
    # Third generation sees a DIFFERENT evidence bundle -> trigger
    # observation invalid -> not part of the adjacent run.
    journal.append(_rec(3, evidence_hash="c" * 64, parent=g2.generation_id))
    # The third record exists but its trigger_observation_valid=False, so the
    # adjacent VALID run from the tail is 1 (only the tail counts as
    # trigger-observable).
    assert journal.observable_valid_count() == 1
