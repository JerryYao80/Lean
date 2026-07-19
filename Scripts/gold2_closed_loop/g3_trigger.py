"""G3 redesign trigger evaluator (Task 13, Phase 3 STOP gate).

A PURE function over a list of :class:`GenerationRecord` (design §7 lines
264-282, §14 lines 418-421). It decides whether the G3 redesign trigger fires
and, if not, which G3 alias reason applies.

Trigger conditions (all required, boundary comparisons INCLUSIVE):
  * at least ``min_generations`` (default 3) ADJACENT VALID generation
    records ending at the tail;
  * every record in that adjacent run shares the same frozen
    ``input_evidence_sha256`` (the run is "observable" — evidence did not
    change mid-run);
  * every record in the run has ``attribution_gap >= threshold``;
  * every record in the run has ``shaping_weight >= ceiling`` OR
    ``convergence_status == NON_CONVERGED`` (spec §7: "不收敛或顶格 3.0");
  * every record in the run has ``pending_candidate_count == 0``.

If the adjacent-valid run is shorter than ``min_generations`` (a generation
failed, or the evidence hash changed mid-run — the journal flags
``trigger_observation_valid=False``), the trigger CANNOT fire and the alias
reason is ``TRIGGER_UNOBSERVABLE`` (spec §14 line 419).

If there are >= ``min_generations`` adjacent valid records but the gap/weight/
pending conditions are not met, the alias reason is ``NOT_TRIGGERED`` (spec
§14 line 418: "有至少三代有效 generation 观测，但条件未持续满足").

This module is PROOF-ONLY. It does NOT import the production inspiration
trigger machinery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from Scripts.gold2_closed_loop.generation_journal import (
    GenerationRecord,
    generation,
)
from Scripts.gold2_closed_loop.state_machine import G3AliasReason

# The default minimum number of adjacent valid generations required to
# fire the trigger (spec §7 line 268: "连续至少三代"). The preregistration
# schema enforces ``min_generations_for_trigger >= 1``; the trigger
# evaluator accepts a caller override so tests can pin boundary cases.
_DEFAULT_MIN_GENERATIONS = 3


@dataclass(frozen=True)
class G3TriggerResult:
    """Result of :func:`evaluate_g3`.

    Attributes
    ----------
    eligible:
        True iff the trigger fired (the construction path may build a G3
        candidate). False otherwise.
    alias_reason:
        One of ``G3AliasReason`` values when ``eligible`` is False, else
        None. ``NOT_TRIGGERED`` means redesign was not needed (valid §14
        outcome); ``TRIGGER_UNOBSERVABLE`` means the window's redesign
        increment could not be observed (the overall experiment caps at
        ``NOT_EVALUATED + UNPROVEN`` if >= 3 windows are unobservable).
    """

    eligible: bool
    alias_reason: str | None


def evaluate_g3(
    records: Sequence[GenerationRecord],
    gap_threshold: float,
    weight_ceiling: float,
    *,
    min_generations: int = _DEFAULT_MIN_GENERATIONS,
) -> G3TriggerResult:
    """Evaluate the G3 trigger over a sequence of generation records.

    Parameters
    ----------
    records:
        The window's generation records (in file order). The trigger
        considers the adjacent-valid run ending at the tail; records before
        a break (a generation whose ``trigger_observation_valid`` is False)
        are NOT part of the run.
    gap_threshold:
        The minimum ``attribution_gap`` for the trigger (inclusive).
    weight_ceiling:
        The minimum ``shaping_weight`` for the trigger (inclusive), UNLESS
        the record is NON_CONVERGED.
    min_generations:
        The minimum number of adjacent valid records required (default 3).
    """
    if min_generations < 1:
        raise ValueError(
            f"min_generations must be >= 1, got {min_generations}"
        )
    # Build the maximal suffix run of records that all share the same frozen
    # ``input_evidence_sha256``. The tail always seeds a run of length 1; the
    # run extends backwards through every preceding record whose evidence
    # hash equals the next (tailward) record's hash. This is consistent with
    # ``GenerationJournal.observable_valid_count`` so the trigger evaluator
    # and the journal agree on what "adjacent valid generations" means
    # (spec §7 line 268 / §6 line 246: generations are diagnostics over a
    # SINGLE frozen evidence bundle).
    run = _suffix_run(records)
    if len(run) < min_generations:
        return G3TriggerResult(
            eligible=False,
            alias_reason=G3AliasReason.TRIGGER_UNOBSERVABLE.value,
        )
    # Gap / weight / pending conditions on EVERY record in the run.
    for record in run:
        if record.attribution_gap < gap_threshold:
            return G3TriggerResult(
                eligible=False,
                alias_reason=G3AliasReason.NOT_TRIGGERED.value,
            )
        if record.pending_candidate_count != 0:
            return G3TriggerResult(
                eligible=False,
                alias_reason=G3AliasReason.NOT_TRIGGERED.value,
            )
        # Weight condition: weight >= ceiling OR NON_CONVERGED.
        if record.convergence_status == "NON_CONVERGED":
            continue
        if record.shaping_weight < weight_ceiling:
            return G3TriggerResult(
                eligible=False,
                alias_reason=G3AliasReason.NOT_TRIGGERED.value,
            )
    return G3TriggerResult(eligible=True, alias_reason=None)


def _suffix_run(records: Sequence[GenerationRecord]) -> list[GenerationRecord]:
    """Return the maximal suffix of ``records`` whose members all share the
    same frozen ``input_evidence_sha256``. The tail always seeds a run of
    length 1; the run extends backwards through every preceding record whose
    evidence hash equals the next (tailward) record's hash.

    Examples
    --------
    * ``[A, A, A]`` -> ``[A, A, A]``  (length 3)
    * ``[A, A, C]`` -> ``[C]``        (length 1; tail C differs from prior A)
    * ``[A, B, B]`` -> ``[B, B]``     (length 2)
    """
    if not records:
        return []
    run: list[GenerationRecord] = [records[-1]]
    for i in range(len(records) - 1, 0, -1):
        current = records[i]
        preceding = records[i - 1]
        if preceding.input_evidence_sha256 != current.input_evidence_sha256:
            break
        run.append(preceding)
    run.reverse()  # back to file order
    return run


__all__ = [
    "G3TriggerResult",
    "evaluate_g3",
    "generation",
]
