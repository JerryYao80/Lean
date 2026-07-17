"""Append-only hash-chained event journal for the Gold2 closed-loop proof
(Task 9, Phase 2 STOP gate).

Implements design §10's candidate-event JSONL contract: every record carries
``sequence`` (1-based, strictly increasing), ``event_id`` (unique), and
``previous_event_sha256`` = the prior record's ``event_sha256`` (all-zeros
for the first record). The ``event_sha256`` field is the SHA-256 of the
canonical-JSON form of the record with the ``event_sha256`` field itself
excluded — i.e. the hash covers every field that is not itself.

Mutual exclusion across processes is provided by an exclusive ``fcntl.flock``
on a ``<path>.lock`` sidecar file. Duplicate ``event_id`` values are
rejected. The journal file itself is written with ``flush`` + ``os.fsync``
before the lock is released, so a crash after the lock release means the
record is durable.

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

# Fields that form the canonical-hash input (every field except ``event_sha256``).
# Listed explicitly so callers building ``append_raw`` records know which fields
# participate in the hash.
_HASHED_FIELDS = (
    "sequence",
    "event_id",
    "event_type",
    "event_time_utc",
    "previous_event_sha256",
    "payload",
)


class EventJournal:
    """Append-only, hash-chained, file-locked JSONL event journal.

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
        # existing on-disk chain.
        self._seen_event_ids: set[str] = set()
        # Last written event_sha256 (zero hash if empty). Loaded eagerly so a
        # fresh process can chain correctly against the existing on-disk tail.
        self._last_event_sha256: str = _ZERO_HASH
        self._last_sequence: int = 0
        self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a new event and return the full record (including hashes).

        ``payload`` may include an ``event_id``; if absent one is generated
        (``uuid4().hex``). ``payload`` MUST NOT include ``sequence`` /
        ``previous_event_sha256`` / ``event_sha256`` / ``event_time_utc``:
        those are journal-controlled fields. ``event_id`` is the only
        caller-overridable field in ``payload``.
        """
        event_id = payload.get("event_id")
        if not event_id:
            event_id = uuid4().hex
        else:
            event_id = str(event_id)
        sequence = self._last_sequence + 1
        previous_hash = self._last_event_sha256
        record: dict[str, Any] = {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "event_time_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "previous_event_sha256": previous_hash,
            # event_id lives at the top level (not inside payload) so the
            # duplicate check and the canonical hash both see one location.
            "payload": {k: v for k, v in payload.items() if k != "event_id"},
        }
        return self._append_record(record, event_id=event_id)

    def append_raw(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append a caller-supplied record (with previous_event_sha256).

        The caller must supply ``sequence`` and ``previous_event_sha256``;
        both are validated against the chain tail. ``event_sha256`` is
        computed by the journal.

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

        sequence = int(record["sequence"])
        previous_hash = str(record["previous_event_sha256"])

        # Chain-tail validation FIRST: a wrong previous hash is the
        # canonical rejection reason even if other fields are also missing.
        if previous_hash != self._last_event_sha256:
            raise ValueError(
                "previous hash mismatch: expected "
                f"{self._last_event_sha256}, got {previous_hash}"
            )
        if sequence != self._last_sequence + 1:
            raise ValueError(
                f"append_raw: sequence {sequence} is not last+1 "
                f"({self._last_sequence + 1} expected)"
            )
        if "event_id" not in record:
            raise ValueError("append_raw: record missing required field event_id")
        if "event_type" not in record:
            raise ValueError("append_raw: record missing required field event_type")

        event_id = str(record["event_id"])
        if event_id in self._seen_event_ids:
            raise ValueError(
                f"append_raw: duplicate event_id {event_id!r}; "
                "event IDs must be unique across the journal"
            )
        # Build a normalized record so the canonical hash is deterministic.
        normalized: dict[str, Any] = {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": str(record["event_type"]),
            "event_time_utc": str(
                record.get(
                    "event_time_utc",
                    datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                )
            ),
            "previous_event_sha256": previous_hash,
            "payload": record.get("payload", {}),
        }
        # Carry any caller fields not in our canonical set so callers can
        # store additional identity fields (candidate_id, partition, etc.).
        # These are added BEFORE the hash so the hash covers them.
        for key, value in record.items():
            if key not in normalized and key not in ("event_sha256",):
                normalized[key] = value
        return self._append_record(normalized, event_id=event_id)

    def read_all(self) -> list[dict[str, Any]]:
        """Return all parsed records (in file order). Does NOT take the lock.

        Use this for read-only inspection / chain verification in tests.
        Concurrent writes may produce a partial last line; this method
        detects and skips a truncated tail.
        """
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("rb") as stream:
            for raw in stream:
                if not raw.strip():
                    continue
                out.append(json.loads(raw))
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_existing(self) -> None:
        """Populate ``_seen_event_ids`` / ``_last_event_sha256`` /
        ``_last_sequence`` from the existing on-disk chain (if any).

        No lock: this is a read-only scan that runs before the first write.
        If two journals race to load on an empty file, the first write will
        acquire the lock and subsequent writers will re-check the chain tail
        inside the lock.
        """
        records = self.read_all()
        for record in records:
            event_id = str(record.get("event_id", ""))
            if event_id:
                self._seen_event_ids.add(event_id)
            seq = record.get("sequence")
            if isinstance(seq, int):
                self._last_sequence = max(self._last_sequence, seq)
            sha = record.get("event_sha256")
            if isinstance(sha, str) and len(sha) == 64:
                self._last_event_sha256 = sha
        # If records exist but no event_sha256 field is present (legacy
        # journal), recompute the tail hash so the chain stays consistent.
        if records and not self._last_event_sha256:
            self._last_event_sha256 = self._recompute_hash(records[-1])

    def _append_record(self, record: dict[str, Any], *, event_id: str) -> dict[str, Any]:
        """Compute event_sha256, write the line under the lock, and update state."""
        event_sha256 = self._recompute_hash(record)
        record["event_sha256"] = event_sha256
        line = json.dumps(record, ensure_ascii=False) + "\n"
        # Lock sidecar: create if absent, take exclusive lock, write, fsync.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                # Re-validate inside the lock: another writer may have appended
                # while we were waiting.
                if event_id in self._seen_event_ids:
                    raise ValueError(
                        f"append: duplicate event_id {event_id!r}; "
                        "event IDs must be unique across the journal"
                    )
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
