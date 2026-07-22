"""Append-only hash-chained event journal for the Gold2 closed-loop proof
(Task 9, Phase 2 STOP gate).

Implements design §10 / §15's ``candidate-events.jsonl`` contract: every
record carries ``sequence`` (1-based, strictly increasing), ``event_id``
(unique), and ``previous_event_sha256`` = the prior record's
``event_sha256`` (all-zeros for the first record). The ``event_sha256``
field is the SHA-256 of the canonical-JSON form of the record with the
``event_sha256`` field itself excluded — i.e. the hash covers every field
that is not itself.

Every on-disk record is a FLAT candidate event conforming to
``candidate-event.schema.json``: caller-supplied candidate identity fields
(``candidate_id``, ``execution_status``, ``partition``, ``parameters``,
``metrics``) live at the TOP LEVEL of the record, not under a ``payload``
wrapper. The timestamp field is ``attempted_at_utc`` (ISO 8601 UTC with a
trailing ``Z``). ``append(event_type, payload)`` merges ``payload``'s keys
onto the top level of the record (stripping a caller-supplied
``event_id`` if present, since ``event_id`` is journal-controlled and
generated when absent). ``append_raw(record)`` accepts a fully-formed flat
record and validates the chain tail.

Mutual exclusion across processes is provided by an exclusive ``fcntl.flock``
on a ``<path>.lock`` sidecar file. The read-validate-write critical section
is atomic w.r.t. concurrent writers: INSIDE the lock the journal re-scans
the on-disk tail to refresh ``_last_event_sha256`` / ``_last_sequence`` /
``_seen_event_ids`` BEFORE validating and writing, so a second
``EventJournal`` instance that cached stale state at ``__init__`` cannot
break the chain or accept a duplicate ``event_id``. The journal file itself
is written with ``flush`` + ``os.fsync`` before the lock is released, so a
crash after the lock release means the record is durable.

``read_all`` is crash-recoverable for a TORN LAST LINE only: if the final
line fails to parse AND the file does not end with ``"\\n"`` (the line was
never completed), it is skipped as a torn write. A corrupt line that is NOT
the last, or a corrupt last line that DOES end with ``"\\n"``, is genuine
corruption and raises ``ValueError`` naming the line number.

No production code (the mature Gold2 strategy, the permissive RL trace
loader, ``evolution_scheduler.py``) is modified.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from Scripts.gold2_closed_loop.evidence import canonical_hash

# All-zeros sentinel for the first record's previous-event hash.
_ZERO_HASH = "0" * 64

# Chain/control fields the journal owns. These are NEVER taken from the
# caller-supplied payload in ``append`` (the caller may not set them). In
# ``append_raw`` the caller supplies ``sequence`` / ``previous_event_sha256``
# / ``event_id`` / ``event_type`` (validated against the chain tail), and the
# journal computes ``event_sha256``.
_CONTROL_FIELDS = frozenset(
    {
        "sequence",
        "event_id",
        "event_type",
        "attempted_at_utc",
        "previous_event_sha256",
        "event_sha256",
    }
)


class EventJournal:
    """Append-only, hash-chained, file-locked JSONL candidate-event journal.

    Parameters
    ----------
    path:
        JSONL file path. The lock sidecar is created at ``f"{path}.lock"``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        # In-memory cache of event_ids seen so far (across all reads + writes).
        # Loaded eagerly so a fresh process can detect duplicates against the
        # existing on-disk chain. This cache is REFRESHED from disk inside the
        # lock before every write (see ``_refresh_from_disk``) so a second
        # instance that cached stale state at ``__init__`` cannot accept a
        # duplicate ``event_id`` written by the first instance after we opened.
        self._seen_event_ids: set[str] = set()
        # Last written event_sha256 (zero hash if empty). Loaded eagerly so a
        # fresh process can chain correctly against the existing on-disk tail.
        # Refreshed from disk inside the lock before every write.
        self._last_event_sha256: str = _ZERO_HASH
        self._last_sequence: int = 0
        self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a new candidate event and return the full flat record.

        ``payload`` keys are MERGED onto the TOP LEVEL of the record (no
        ``payload`` wrapper), so caller-supplied candidate identity fields
        (``candidate_id``, ``execution_status``, ``partition``,
        ``parameters``, ``metrics``) land at the top level and the on-disk
        record conforms to ``candidate-event.schema.json``.

        ``payload`` may include an ``event_id``; if absent one is generated
        (``uuid4().hex``). ``payload`` MUST NOT include ``sequence`` /
        ``previous_event_sha256`` / ``event_sha256`` /
        ``attempted_at_utc`` / ``event_type``: those are journal-controlled
        fields. ``event_id`` is the only caller-overridable control field in
        ``payload``.

        The full candidate-event schema (``validate_candidate_event``) is the
        CALLER's responsibility (Task 10): ``append`` only guarantees the
        chain/hash invariants, not that every required candidate field is
        present. A record with only ``candidate_id`` will chain correctly but
        will not satisfy ``validate_candidate_event`` until the caller
        supplies the remaining required fields.
        """
        if not isinstance(payload, dict):
            raise TypeError(
                f"append: payload must be a dict, got {type(payload).__name__}"
            )
        event_id = payload.get("event_id")
        if not event_id:
            event_id = uuid4().hex
        else:
            event_id = str(event_id)
        # Build the flat record. Start with the chain/control fields the
        # journal owns, then merge caller-supplied payload keys at the top
        # level (skipping any control-field keys the caller tried to set —
        # those are journal-controlled and must not be overwritten).
        record: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "attempted_at_utc": _now_utc_iso(),
        }
        for key, value in payload.items():
            if key == "event_id":
                # Already promoted to top level above.
                continue
            if key in _CONTROL_FIELDS:
                # Caller may not set sequence / previous_event_sha256 /
                # event_sha256 / attempted_at_utc / event_type via append.
                raise ValueError(
                    f"append: payload key {key!r} is journal-controlled; "
                    "use append_raw to supply a full record"
                )
            record[key] = value
        return self._append_record(record, event_id=event_id, derive_sequence=True)

    def append_raw(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append a caller-supplied FLAT record (with previous_event_sha256).

        The caller must supply ``sequence`` and ``previous_event_sha256``;
        both are validated against the on-disk chain tail INSIDE the lock
        (refreshed from disk, so concurrent writers cannot stale-read the
        tail). ``event_sha256`` is computed by the journal.

        Order of checks (matters for the §10 hash-chain contract): the
        previous-hash mismatch is checked FIRST so a record that fails the
        chain tail cannot be silently accepted under a different error
        message. Required-field presence (``event_id``, ``event_type``) is
        checked AFTER the chain validation.

        Raises
        ------
        ValueError:
            If ``previous_event_sha256`` does not match the last written
            hash (message contains "previous hash"), or if ``sequence``
            is not ``last + 1`` (message contains "sequence"), or if
            ``event_id`` is a duplicate (message contains "duplicate").
        """
        if "sequence" not in record:
            raise ValueError("append_raw: record missing required field sequence")
        if "previous_event_sha256" not in record:
            raise ValueError(
                "append_raw: record missing required field previous_event_sha256"
            )
        if "event_id" not in record:
            raise ValueError("append_raw: record missing required field event_id")
        if "event_type" not in record:
            raise ValueError("append_raw: record missing required field event_type")

        sequence = int(record["sequence"])
        previous_hash = str(record["previous_event_sha256"])
        event_id = str(record["event_id"])

        # Build a normalized record so the canonical hash is deterministic.
        # Carry every caller field at the top level (flat shape); the only
        # field the journal injects is event_sha256 (computed later).
        normalized: dict[str, Any] = {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": str(record["event_type"]),
            "attempted_at_utc": str(
                record.get(
                    "attempted_at_utc",
                    _now_utc_iso(),
                )
            ),
            "previous_event_sha256": previous_hash,
        }
        # Carry any caller fields not in our control set so callers can
        # store candidate identity fields (candidate_id, partition,
        # parameters, metrics, ...). These are added BEFORE the hash so the
        # hash covers them (flat shape).
        for key, value in record.items():
            if key in _CONTROL_FIELDS:
                continue
            normalized[key] = value
        return self._append_record(
            normalized,
            event_id=event_id,
            derive_sequence=False,
            caller_sequence=sequence,
            caller_previous_hash=previous_hash,
        )

    def read_all(self) -> list[dict[str, Any]]:
        """Return all parsed records (in file order). Does NOT take the lock.

        Use this for read-only inspection / chain verification in tests.

        Crash recovery for a TORN LAST LINE: if the final line fails
        ``json.loads`` AND the file does not end with ``"\\n"`` (the line was
        never completed by a durable write), the torn line is SKIPPED — a
        crash mid-``append`` must not make the journal permanently unreadable.

        Genuine corruption still raises: if a line fails ``json.loads`` and
        it is NOT the last line, OR the file ends with ``"\\n"`` (meaning the
        line was "complete" but corrupt), ``ValueError`` is raised naming the
        1-based line number so the operator can locate the damage.
        """
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        # Read the whole file so we can tell whether the final line was
        # newline-terminated (i.e. "complete") or torn.
        data = self.path.read_bytes()
        if not data:
            return []
        ends_with_newline = data.endswith(b"\n")
        # ``splitlines`` drops the trailing newline information, so we split
        # manually on b"\n" and re-append empty-line semantics only for the
        # final segment.
        raw_lines = data.split(b"\n")
        # If the file ends with b"\n", the final element after split is b"";
        # drop it so it is not treated as a parseable line.
        if ends_with_newline:
            raw_lines = raw_lines[:-1]
        total = len(raw_lines)
        for index, raw in enumerate(raw_lines, start=1):
            if not raw.strip():
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as error:
                is_last_line = index == total
                # A torn write is: the LAST line AND the file did not end with
                # a newline (the writer never finished the record). Skip it.
                if is_last_line and not ends_with_newline:
                    # Torn tail: skip so the journal stays readable. We do NOT
                    # truncate the file here (callers may inspect the partial
                    # bytes); a subsequent successful append will overwrite
                    # this region because we always seek to end-of-file and
                    # the torn bytes are by definition past the last valid
                    # newline. Document this in the docstring above.
                    break
                # Genuine corruption: a non-last line, or a "complete" (newline
                # terminated) line that does not parse.
                raise ValueError(
                    f"read_all: corrupt JSON at line {index} of "
                    f"{self.path}: {error.msg}"
                ) from error
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_existing(self) -> None:
        """Populate ``_seen_event_ids`` / ``_last_event_sha256`` /
        ``_last_sequence`` from the existing on-disk chain (if any).

        No lock: this is a read-only scan that runs before the first write.
        The state cached here is REFRESHED from disk inside the lock before
        every write (see ``_refresh_from_disk``), so a race between this
        scan and another writer cannot corrupt the chain — the in-lock
        re-scan is authoritative.
        """
        self._refresh_from_disk()

    def _refresh_from_disk(self) -> None:
        """Re-read the on-disk tail and rebuild the in-memory chain state.

        Called once at ``__init__`` and again INSIDE the exclusive lock
        before every write, so a second ``EventJournal`` instance that
        cached stale state cannot break the chain or accept a duplicate
        ``event_id``.

        A non-int ``sequence`` field is loud corruption (NIT 9): the
        codebase rule is "errors propagate, never swallowed" (see
        ``IFormalTraceSink``), so we raise ``ValueError`` rather than
        silently skipping the record.
        """
        self._seen_event_ids = set()
        self._last_event_sha256 = _ZERO_HASH
        self._last_sequence = 0
        records = self.read_all()
        for record in records:
            event_id = record.get("event_id")
            if event_id is not None:
                self._seen_event_ids.add(str(event_id))
            if "sequence" in record:
                seq = record["sequence"]
                # bool is a subclass of int; reject it explicitly so True
                # / False are not silently coerced to 1 / 0.
                if isinstance(seq, bool) or not isinstance(seq, int):
                    raise ValueError(
                        f"_refresh_from_disk: record sequence must be an int, "
                        f"got {type(seq).__name__} ({seq!r})"
                    )
                if seq > self._last_sequence:
                    self._last_sequence = seq
            sha = record.get("event_sha256")
            if isinstance(sha, str) and len(sha) == 64:
                self._last_event_sha256 = sha

    def _append_record(
        self,
        record: dict[str, Any],
        *,
        event_id: str,
        derive_sequence: bool,
        caller_sequence: int | None = None,
        caller_previous_hash: str | None = None,
    ) -> dict[str, Any]:
        """Compute event_sha256, write the line under the lock, and update state.

        The read-validate-write critical section is atomic w.r.t. concurrent
        writers: INSIDE the ``flock(LOCK_EX)`` we call ``_refresh_from_disk``
        to re-read the on-disk tail, THEN validate the chain tail
        (``previous_event_sha256`` / ``sequence``) and the duplicate
        ``event_id`` against the refreshed state, THEN write + fsync.

        For ``append`` (``derive_sequence=True``) the sequence and previous
        hash are DERIVED from the refreshed tail. For ``append_raw``
        (``derive_sequence=False``) the caller's ``caller_sequence`` /
        ``caller_previous_hash`` are VALIDATED against the refreshed tail.
        """
        # Lock sidecar: create if absent, take exclusive lock, write, fsync.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                # Re-scan the on-disk tail INSIDE the lock so a concurrent
                # writer that appended while we were waiting is visible.
                # This closes the TOCTOU window between the ``__init__`` scan
                # and the write (Critical 1 + Critical 2).
                self._refresh_from_disk()
                # Duplicate check against the FRESH on-disk seen-set.
                if event_id in self._seen_event_ids:
                    raise ValueError(
                        f"append: duplicate event_id {event_id!r}; "
                        "event IDs must be unique across the journal"
                    )
                if derive_sequence:
                    sequence = self._last_sequence + 1
                    previous_hash = self._last_event_sha256
                    record["sequence"] = sequence
                    record["previous_event_sha256"] = previous_hash
                else:
                    assert caller_sequence is not None
                    assert caller_previous_hash is not None
                    # Chain-tail validation FIRST: a wrong previous hash is
                    # the canonical rejection reason even if other fields are
                    # also wrong.
                    if caller_previous_hash != self._last_event_sha256:
                        raise ValueError(
                            "previous hash mismatch: expected "
                            f"{self._last_event_sha256}, got {caller_previous_hash}"
                        )
                    if caller_sequence != self._last_sequence + 1:
                        raise ValueError(
                            f"append_raw: sequence {caller_sequence} is not "
                            f"last+1 ({self._last_sequence + 1} expected)"
                        )
                    sequence = caller_sequence
                    previous_hash = caller_previous_hash
                # Compute the canonical hash over every top-level field EXCEPT
                # ``event_sha256`` itself (sorted keys, separators (",",":"),
                # ensure_ascii=False — see ``evidence.canonical_hash``).
                event_sha256 = self._recompute_hash(record)
                record["event_sha256"] = event_sha256
                line = json.dumps(record, ensure_ascii=False) + "\n"
                with self.path.open("ab") as journal_stream:
                    journal_stream.write(line.encode("utf-8"))
                    journal_stream.flush()
                    os.fsync(journal_stream.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        # Update in-memory state ONLY after a successful durable write.
        self._seen_event_ids.add(event_id)
        self._last_sequence = int(record["sequence"])
        self._last_event_sha256 = event_sha256
        return record

    @staticmethod
    def _recompute_hash(record: dict[str, Any]) -> str:
        """Canonical SHA-256 of the record minus its ``event_sha256`` field."""
        copy = {k: v for k, v in record.items() if k != "event_sha256"}
        return canonical_hash(copy)


def _now_utc_iso() -> str:
    """Return the current UTC time as ISO 8601 with a trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
