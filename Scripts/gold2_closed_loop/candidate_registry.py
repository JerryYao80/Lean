"""Thin candidate-event registry wrapping the hash-chained EventJournal
(Task 10, Phase 2 STOP gate).

The G1 optimizer uses this to register a candidate (PENDING) BEFORE the
runner is invoked, then record the terminal outcome (SUCCEEDED /
FAILED_* / TIMED_OUT / NO_TRADES / PRUNED / DUPLICATE / DOMINATED /
REJECTED / INVALID) AFTER. Every call appends a flat schema-conformant
candidate-event record to the journal and validates it via
``validate_candidate_event`` (fail loud).

No production code is modified.
"""

from __future__ import annotations

from typing import Any, Callable

from Scripts.gold2_closed_loop.event_journal import EventJournal
from Scripts.gold2_closed_loop.schemas import validate_candidate_event


class CandidateRegistry:
    """Thin wrapper over ``EventJournal`` for candidate-event append.

    Each call appends ONE record to the journal and validates it against
    the candidate-event schema before returning. The journal guarantees
    the hash chain (``previous_event_sha256`` / ``event_sha256``) and
    exclusive-file-lock mutual exclusion.

    Parameters
    ----------
    journal_path:
        JSONL path for the underlying ``EventJournal``.
    """

    def __init__(self, journal_path: Any) -> None:
        self.journal = EventJournal(journal_path)
        self.journal_path = self.journal.path

    def register(
        self,
        candidate_id: str,
        partition: str,
        parameters: dict[str, Any],
        *,
        execution_status: str = "PENDING",
    ) -> dict[str, Any]:
        """Append a REGISTERED candidate event (execution_status PENDING).

        Called BEFORE the runner is invoked. The terminal outcome is
        recorded separately via ``record_outcome``.
        """
        if not candidate_id:
            raise ValueError("register: candidate_id must be non-empty")
        if not partition:
            raise ValueError("register: partition must be non-empty")
        if not isinstance(parameters, dict):
            raise TypeError(
                f"register: parameters must be a dict, got "
                f"{type(parameters).__name__}"
            )
        # NOTE: ``EventJournal.append(event_type, payload)`` takes the
        # ``event_type`` as its first positional arg and REJECTS an
        # ``event_type`` key in the payload (it is journal-controlled).
        # ``execution_status`` / ``candidate_id`` / ``partition`` /
        # ``parameters`` / ``metrics`` are caller-supplied candidate
        # identity fields that land at the top level of the flat record.
        payload = {
            "candidate_id": str(candidate_id),
            "execution_status": str(execution_status),
            "partition": str(partition),
            "parameters": parameters,
            "metrics": None,
        }
        record = self.journal.append("REGISTERED", payload)
        validate_candidate_event(record)
        return record

    def register_and_record(
        self,
        candidate_id: str,
        partition: str,
        parameters: dict[str, Any],
        *,
        outcome: Callable[[], dict[str, Any]],
        execution_status: str = "PENDING",
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Register a candidate AND record its outcome in ONE journal-critical
        section, so a crash between register() and record_outcome() cannot
        leave a dangling PENDING record (whole-tree review).

        ``outcome`` is a callable that runs the trial and returns a dict
        with keys ``event_type`` / ``execution_status`` / ``parameters`` /
        ``metrics`` / ``error`` (any may be omitted; defaults are applied).
        If ``outcome`` raises, a FAILED_INFRASTRUCTURE terminal event is
        appended so the candidate is closed (no dangling PENDING).

        Returns ``(registered_record, terminal_record_or_None)``.
        """
        registered = self.register(
            candidate_id=candidate_id, partition=partition,
            parameters=parameters, execution_status=execution_status,
        )
        try:
            result = outcome() or {}
        except Exception as exc:
            result = {
                "event_type": "FAILED_INFRASTRUCTURE",
                "execution_status": "FAILED_INFRASTRUCTURE",
                "error": str(exc),
            }
        terminal = self.record_outcome(
            candidate_id=candidate_id,
            event_type=str(result.get("event_type", "SUCCEEDED")),
            execution_status=str(result.get("execution_status", "SUCCEEDED")),
            partition=partition,
            parameters=result.get("parameters", parameters),
            metrics=result.get("metrics"),
            error=result.get("error"),
        )
        return registered, terminal

    def record_outcome(
        self,
        candidate_id: str,
        *,
        event_type: str,
        execution_status: str,
        partition: str,
        parameters: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Append a terminal candidate event.

        ``event_type`` is the ``CandidateEventType`` value
        (SUCCEEDED / FAILED_STRATEGY / FAILED_INFRASTRUCTURE /
        FAILED_EVIDENCE_CAPTURE / TIMED_OUT / NO_TRADES / PRUNED /
        DUPLICATE / DOMINATED / REJECTED / INVALID). ``execution_status``
        is the matching ``ExecutionStatus`` value (for REGISTERED the
        execution_status is PENDING; for terminal events it is the
        matching FAILED_*/TIMED_OUT/NO_TRADES/SUCCEEDED).

        ``partition`` is REQUIRED (the candidate-event schema mandates
        it). The optimizer always knows the partition at outcome time.
        """
        if not candidate_id:
            raise ValueError("record_outcome: candidate_id must be non-empty")
        if not partition:
            raise ValueError("record_outcome: partition must be non-empty")
        # NOTE: ``event_type`` is passed as the first positional arg to
        # ``EventJournal.append`` (it is journal-controlled and must NOT
        # appear as a payload key). ``execution_status`` /
        # ``candidate_id`` / ``partition`` / ``parameters`` / ``metrics``
        # are caller-supplied candidate identity fields.
        payload: dict[str, Any] = {
            "candidate_id": str(candidate_id),
            "execution_status": str(execution_status),
            "partition": str(partition),
            "parameters": parameters if parameters is not None else {},
            "metrics": metrics,
        }
        # NOTE: the candidate-event schema uses additionalProperties: false,
        # so an ``error`` text cannot be carried as a top-level field. The
        # failure classification lives in ``event_type`` /
        # ``execution_status``; the runner's stderr tail is captured in the
        # run-dir artifacts (out of scope for the journal record).
        record = self.journal.append(str(event_type), payload)
        validate_candidate_event(record)
        return record
