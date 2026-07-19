"""Window-local append-only generation journal (Task 13, Phase 3 STOP gate).

Mirrors the design §7 "Generation semantics" contract (lines 264-282): every
generation record is APPEND-ONLY per window, PARENT-LINKED (each generation's
parent is the prior generation's id), collision-rejecting (a duplicate
``generation_id`` raises), and carries every design field:

    generation_id              : str (unique per window)
    generation_index           : int (1-based, strictly increasing)
    parent_generation_id       : str | None (None for the first generation)
    generation_cutoff          : str ISO 8601 UTC cutoff timestamp
    input_evidence_sha256      : str (the FROZEN review-bundle evidence hash)
    candidate_set_sha256       : str (hash of the candidate set produced)
    attribution_gap            : float (>=0)
    shaping_weight             : float (>=0)
    pending_candidate_count    : int (>=0)
    convergence_status        : CONVERGED | NON_CONVERGED | UNOBSERVABLE
    trigger_observation_valid : bool
    terminal_reason            : BUDGET_EXHAUSTED | TRIGGERED | CONVERGED |
                                 EXECUTION_FAILED | INVALID_EVIDENCE

The journal is HASH-CHAINED like the candidate-event journal: each record
carries ``previous_record_sha256`` = the prior record's ``record_sha256``
(all-zeros for the first record), and an exclusive ``fcntl.flock`` provides
mutual exclusion across processes.

"连续三代" (three adjacent valid generations, spec §7 line 268) operate on
the SAME frozen ``input_evidence_sha256`` (spec §6 line 246: generations are
diagnostics over a frozen bundle, not repeated peeks at the review
partition). A generation whose ``input_evidence_sha256`` differs from its
parent's flags ``trigger_observation_valid=False`` so the G3 trigger cannot
fire across a re-frozen bundle.

This module is PROOF-ONLY. It does NOT import the production
``Scripts/inspiration/generations.py`` (which uses overwrite-write and is
non-collision-rejecting — spec §6 line 158 explicitly forbids reuse).
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from Scripts.gold2_closed_loop.evidence import canonical_hash

_ZERO_HASH = "0" * 64

# Design §7 line 282 closed enums.
_VALID_CONVERGENCE = {"CONVERGED", "NON_CONVERGED", "UNOBSERVABLE"}
_VALID_TERMINAL = {
    "BUDGET_EXHAUSTED",
    "TRIGGERED",
    "CONVERGED",
    "EXECUTION_FAILED",
    "INVALID_EVIDENCE",
}


@dataclass(frozen=True)
class GenerationRecord:
    """One window-local generation record (design §7 lines 264-282).

    The ``record_sha256`` / ``previous_record_sha256`` / ``appended_at_utc``
    fields are journal-controlled (computed when the record is appended);
    callers build a record via :func:`generation` (or directly) and the
    journal fills them in.
    """

    generation_id: str
    generation_index: int
    parent_generation_id: str | None
    generation_cutoff: str
    input_evidence_sha256: str
    candidate_set_sha256: str
    attribution_gap: float
    shaping_weight: float
    pending_candidate_count: int
    convergence_status: str
    trigger_observation_valid: bool
    terminal_reason: str
    # Journal-controlled (filled by append):
    previous_record_sha256: str = _ZERO_HASH
    record_sha256: str = ""
    appended_at_utc: str = ""


def generation(
    index: int,
    *,
    gap: float,
    weight: float,
    pending: int,
    evidence_hash: str = "0" * 64,
    parent: str | None = None,
    gen_id: str | None = None,
    candidate_set_sha256: str = "0" * 64,
    cutoff: str = "1970-01-01T00:00:00Z",
    converged: bool = True,
    terminal_reason: str = "BUDGET_EXHAUSTED",
) -> GenerationRecord:
    """Construct a :class:`GenerationRecord` for the journal.

    A convenience factory used by both the trigger tests and the production
    builder. ``trigger_observation_valid`` is True iff this record's
    ``input_evidence_sha256`` is consistent with its parent's (the caller
    derives this in the journal append; here we default True for a
    first-generation or same-evidence record — the journal OVERRIDES it on
    append when the evidence hash changed from the parent).
    """
    if gen_id is None:
        gen_id = f"gen-{index}-{uuid4().hex[:12]}"
    convergence = "CONVERGED" if converged else "NON_CONVERGED"
    return GenerationRecord(
        generation_id=gen_id,
        generation_index=int(index),
        parent_generation_id=parent,
        generation_cutoff=cutoff,
        input_evidence_sha256=evidence_hash,
        candidate_set_sha256=candidate_set_sha256,
        attribution_gap=float(gap),
        shaping_weight=float(weight),
        pending_candidate_count=int(pending),
        convergence_status=convergence,
        trigger_observation_valid=True,
        terminal_reason=terminal_reason,
    )


class GenerationJournal:
    """Append-only hash-chained window-local generation journal.

    Parameters
    ----------
    path:
        JSONL file path. The lock sidecar is at ``f"{path}.lock"``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._seen_ids: set[str] = set()
        self._last_record_sha: str = _ZERO_HASH
        self._last_index: int = 0
        self._last_generation_id: str | None = None
        self._last_evidence_hash: str | None = None
        self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, record: GenerationRecord) -> GenerationRecord:
        """Append a generation record and return the full record (with
        ``record_sha256`` / ``previous_record_sha256`` / ``appended_at_utc``
        filled in by the journal).

        Raises
        ------
        ValueError:
            If ``generation_id`` is a duplicate, if ``generation_index`` is
            not ``last + 1``, or if ``parent_generation_id`` does not match
            the last appended generation's id (None for the first record).

        The duplicate-id check runs FIRST (against the in-memory seen-set)
        so a re-used ``generation_id`` is rejected before the parent/index
        constraints are evaluated. The authoritative duplicate re-check
        runs inside the lock in :meth:`_append_record` (against the
        refreshed on-disk tail) so a concurrent writer cannot sneak in a
        duplicate between the fast-path check and the write.
        """
        if record.generation_id in self._seen_ids:
            raise ValueError(
                f"duplicate generation_id {record.generation_id!r}; "
                "generation ids must be unique per window journal"
            )
        finalized = self._finalize_record(record)
        return self._append_record(finalized)

    def read_all(self) -> list[GenerationRecord]:
        """Return all parsed records (in file order). Does NOT take the lock."""
        if not self.path.is_file():
            return []
        out: list[GenerationRecord] = []
        data = self.path.read_bytes()
        if not data:
            return []
        ends_with_newline = data.endswith(b"\n")
        raw_lines = data.split(b"\n")
        if ends_with_newline:
            raw_lines = raw_lines[:-1]
        total = len(raw_lines)
        for index, raw in enumerate(raw_lines, start=1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as error:
                is_last_line = index == total
                if is_last_line and not ends_with_newline:
                    # Torn tail: skip so the journal stays readable.
                    break
                raise ValueError(
                    f"read_all: corrupt JSON at line {index} of "
                    f"{self.path}: {error.msg}"
                ) from error
            out.append(self._record_from_dict(obj))
        return out

    def observable_valid_count(self) -> int:
        """Return the length of the maximal suffix run of generation records
        that all share the same frozen ``input_evidence_sha256``.

        The tail record always seeds a run of length 1 (it is the start of a
        potential new observable run even when its evidence hash differs
        from its parent's — a hash change breaks the PRIOR run but the tail
        itself begins a fresh one). The run extends backwards through every
        preceding record whose evidence hash equals the next (tailward)
        record's hash.

        This is the basis for the §7 "three adjacent valid generations"
        trigger: the trigger evaluator (:mod:`g3_trigger`) builds the same
        suffix run and applies the gap/weight/pending conditions. Computing
        the run from the evidence hashes directly (rather than from the
        per-record ``trigger_observation_valid`` flag) keeps the run
        well-defined when the tail's evidence differs from its parent: the
        tail still counts as 1 (a new run), not 0.

        Examples
        --------
        * ``[A, A, A]`` -> 3  (all share hash A)
        * ``[A, A, C]`` -> 1  (tail C differs from prior A; new run of 1)
        * ``[A, B, B]`` -> 2  (tail two share B)
        """
        records = self.read_all()
        if not records:
            return 0
        # Walk from the tail backwards. The tail always counts (it seeds a
        # run). Each preceding record counts iff its evidence hash equals
        # the next (tailward) record's hash; the first mismatch stops the
        # run (an earlier record belongs to a different, older run).
        count = 1
        for i in range(len(records) - 1, 0, -1):
            current = records[i]
            preceding = records[i - 1]
            if preceding.input_evidence_sha256 != current.input_evidence_sha256:
                break
            count += 1
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finalize_record(self, record: GenerationRecord) -> GenerationRecord:
        """Derive ``trigger_observation_valid`` against the IN-MEMORY tail.

        The on-disk tail is re-read INSIDE the lock in ``_append_record``;
        this in-memory derivation is the optimistic path used before the
        lock is taken (so the returned record's trigger_valid reflects the
        state at append-call time; the in-lock re-derivation is
        authoritative for the persisted record).
        """
        return self._derive_against(record, self._last_index,
                                    self._last_generation_id,
                                    self._last_evidence_hash,
                                    self._last_record_sha)

    def _derive_against(
        self,
        record: GenerationRecord,
        last_index: int,
        last_generation_id: str | None,
        last_evidence_hash: str | None,
        last_record_sha: str,
    ) -> GenerationRecord:
        """Re-derive trigger_observation_valid + previous_record_sha256 +
        parent/index constraints against a given tail state."""
        index = record.generation_index
        parent = record.parent_generation_id
        if last_index == 0:
            if index != 1:
                raise ValueError(
                    f"generation index must start at 1 for the first record; "
                    f"got {index}"
                )
            if parent is not None:
                raise ValueError(
                    f"first generation must have parent_generation_id=None; "
                    f"got {parent!r}"
                )
            trigger_valid = True
            previous = _ZERO_HASH
        else:
            if index != last_index + 1:
                raise ValueError(
                    f"generation index must be strictly increasing: expected "
                    f"{last_index + 1}, got {index}"
                )
            if parent != last_generation_id:
                raise ValueError(
                    f"parent_generation_id {parent!r} does not match the last "
                    f"appended generation id {last_generation_id!r}; "
                    f"generations must be parent-linked"
                )
            trigger_valid = (
                record.input_evidence_sha256 == last_evidence_hash
            )
            previous = last_record_sha
        # Validate the closed-enum fields.
        if record.convergence_status not in _VALID_CONVERGENCE:
            raise ValueError(
                f"convergence_status {record.convergence_status!r} not in "
                f"{sorted(_VALID_CONVERGENCE)}"
            )
        if record.terminal_reason not in _VALID_TERMINAL:
            raise ValueError(
                f"terminal_reason {record.terminal_reason!r} not in "
                f"{sorted(_VALID_TERMINAL)}"
            )
        return GenerationRecord(
            generation_id=record.generation_id,
            generation_index=index,
            parent_generation_id=parent,
            generation_cutoff=record.generation_cutoff,
            input_evidence_sha256=record.input_evidence_sha256,
            candidate_set_sha256=record.candidate_set_sha256,
            attribution_gap=record.attribution_gap,
            shaping_weight=record.shaping_weight,
            pending_candidate_count=record.pending_candidate_count,
            convergence_status=record.convergence_status,
            trigger_observation_valid=trigger_valid,
            terminal_reason=record.terminal_reason,
            previous_record_sha256=previous,
            record_sha256="",
            appended_at_utc=_now_utc_iso(),
        )

    def _record_from_dict(self, obj: dict[str, Any]) -> GenerationRecord:
        """Reconstruct a GenerationRecord from a JSONL line."""
        return GenerationRecord(
            generation_id=obj["generation_id"],
            generation_index=int(obj["generation_index"]),
            parent_generation_id=obj.get("parent_generation_id"),
            generation_cutoff=obj["generation_cutoff"],
            input_evidence_sha256=obj["input_evidence_sha256"],
            candidate_set_sha256=obj["candidate_set_sha256"],
            attribution_gap=float(obj["attribution_gap"]),
            shaping_weight=float(obj["shaping_weight"]),
            pending_candidate_count=int(obj["pending_candidate_count"]),
            convergence_status=obj["convergence_status"],
            trigger_observation_valid=bool(obj["trigger_observation_valid"]),
            terminal_reason=obj["terminal_reason"],
            previous_record_sha256=obj.get("previous_record_sha256", _ZERO_HASH),
            record_sha256=obj.get("record_sha256", ""),
            appended_at_utc=obj.get("appended_at_utc", ""),
        )

    def _append_record(self, record: GenerationRecord) -> GenerationRecord:
        """Compute record_sha256, write the line under the lock, update state,
        and return the persisted (hash-filled) record."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._refresh_from_disk()
                # Duplicate check against the FRESH on-disk seen-set.
                if record.generation_id in self._seen_ids:
                    raise ValueError(
                        f"duplicate generation_id {record.generation_id!r}; "
                        "generation ids must be unique per window journal"
                    )
                # Re-derive parent/index/trigger_valid against the refreshed
                # tail (the in-memory finalize used the pre-lock state; a
                # concurrent writer may have appended since).
                record = self._derive_against(
                    record, self._last_index, self._last_generation_id,
                    self._last_evidence_hash, self._last_record_sha,
                )
                # Compute record_sha256 over the canonical form EXCLUDING
                # record_sha256 itself.
                sha = self._recompute_hash(record)
                record = GenerationRecord(
                    generation_id=record.generation_id,
                    generation_index=record.generation_index,
                    parent_generation_id=record.parent_generation_id,
                    generation_cutoff=record.generation_cutoff,
                    input_evidence_sha256=record.input_evidence_sha256,
                    candidate_set_sha256=record.candidate_set_sha256,
                    attribution_gap=record.attribution_gap,
                    shaping_weight=record.shaping_weight,
                    pending_candidate_count=record.pending_candidate_count,
                    convergence_status=record.convergence_status,
                    trigger_observation_valid=record.trigger_observation_valid,
                    terminal_reason=record.terminal_reason,
                    previous_record_sha256=record.previous_record_sha256,
                    record_sha256=sha,
                    appended_at_utc=record.appended_at_utc,
                )
                line = json.dumps(asdict(record), ensure_ascii=False) + "\n"
                with self.path.open("ab") as stream:
                    stream.write(line.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                # Update in-memory state ONLY after a durable write.
                self._seen_ids.add(record.generation_id)
                self._last_index = record.generation_index
                self._last_generation_id = record.generation_id
                self._last_evidence_hash = record.input_evidence_sha256
                self._last_record_sha = sha
                return record
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load_existing(self) -> None:
        """Populate in-memory state from the on-disk tail (no lock)."""
        self._refresh_from_disk()

    def _refresh_from_disk(self) -> None:
        """Re-read the on-disk tail and rebuild in-memory state.

        Called once at ``__init__`` and again INSIDE the exclusive lock before
        every write, so a second GenerationJournal instance that cached
        stale state cannot break the chain or accept a duplicate id.
        """
        self._seen_ids = set()
        self._last_record_sha = _ZERO_HASH
        self._last_index = 0
        self._last_generation_id = None
        self._last_evidence_hash = None
        records = self.read_all()
        for record in records:
            self._seen_ids.add(record.generation_id)
            if record.generation_index > self._last_index:
                self._last_index = record.generation_index
            self._last_generation_id = record.generation_id
            self._last_evidence_hash = record.input_evidence_sha256
            sha = record.record_sha256
            if isinstance(sha, str) and len(sha) == 64:
                self._last_record_sha = sha

    @staticmethod
    def _recompute_hash(record: GenerationRecord) -> str:
        """Canonical SHA-256 of the record minus its ``record_sha256`` field."""
        obj = asdict(record)
        obj.pop("record_sha256", None)
        return canonical_hash(obj)


def _now_utc_iso() -> str:
    """Return the current UTC time as ISO 8601 with a trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
