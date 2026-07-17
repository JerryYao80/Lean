"""Failing contracts for the append-only hash-chained event journal
(Task 9, Phase 2 STOP gate).

Exercises:
* canonical-JSON hash chain integrity across N appends;
* ``append_raw`` rejects a wrong ``previous_event_sha256`` with "previous hash";
* duplicate ``event_id`` rejection;
* exclusive ``fcntl.flock`` sidecar lock;
* ``read_all`` round-trip for chain verification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.event_journal import EventJournal


def _sha(record: dict) -> str:
    """Re-compute the canonical hash so tests are independent of helpers."""
    copy = {k: v for k, v in record.items() if k != "event_sha256"}
    text = json.dumps(copy, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- anchor tests from the plan (verbatim) --------------------------


def test_previous_hash_mismatch_is_rejected(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    with pytest.raises(ValueError, match="previous hash"):
        journal.append_raw({"sequence": 2, "previous_event_sha256": "0" * 64})


# --- hash-chain integrity ------------------------------------------


def test_journal_hash_chain_integrity(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    records = []
    for i in range(5):
        record = journal.append("CANDIDATE_REGISTERED", {"candidate_id": f"c{i}"})
        records.append(record)

    assert records[0]["sequence"] == 1
    assert records[0]["previous_event_sha256"] == "0" * 64
    for index, record in enumerate(records[1:], start=1):
        assert record["sequence"] == index + 1
        assert record["previous_event_sha256"] == records[index - 1]["event_sha256"]
        assert record["event_sha256"] == _sha(record)

    read = journal.read_all()
    assert [r["sequence"] for r in read] == [1, 2, 3, 4, 5]
    assert read[-1]["event_sha256"] == _sha(read[-1])


def test_journal_appends_events_of_distinct_type(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    journal.append("SUCCEEDED", {"candidate_id": "c1", "run_id": "r1"})
    read = journal.read_all()
    assert [r["event_type"] for r in read] == ["CANDIDATE_REGISTERED", "SUCCEEDED"]


def test_journal_assigns_event_id_when_missing(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    record = journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    assert "event_id" in record
    assert isinstance(record["event_id"], str)
    assert len(record["event_id"]) > 0


def test_journal_rejects_duplicate_event_id(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1", "event_id": "fixed-id-1"})
    with pytest.raises(ValueError, match="duplicate"):
        journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c2", "event_id": "fixed-id-1"})


def test_journal_append_raw_explicit_previous_hash(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    first = journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    second = journal.append_raw(
        {
            "sequence": 2,
            "event_type": "SUCCEEDED",
            "event_id": "ev-2",
            "previous_event_sha256": first["event_sha256"],
            "candidate_id": "c1",
            "payload": {"run_id": "r1"},
        }
    )
    assert second["sequence"] == 2
    assert second["event_sha256"] == _sha(second)
    assert second["previous_event_sha256"] == first["event_sha256"]


def test_journal_append_raw_rejects_wrong_sequence(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    first = journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    with pytest.raises(ValueError, match="sequence"):
        journal.append_raw(
            {
                "sequence": 99,
                "event_type": "SUCCEEDED",
                "event_id": "ev-x",
                "previous_event_sha256": first["event_sha256"],
                "candidate_id": "c1",
                "payload": {"run_id": "r1"},
            }
        )


def test_journal_reloads_existing_chain(tmp_path):
    """A fresh ``EventJournal`` on the existing file must chain correctly."""
    path = tmp_path / "events.jsonl"
    j1 = EventJournal(path)
    a = j1.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    b = j1.append("SUCCEEDED", {"candidate_id": "c1"})
    assert a["event_sha256"] == b["previous_event_sha256"]

    j2 = EventJournal(path)
    existing = j2.read_all()
    assert len(existing) == 2
    assert existing[-1]["event_sha256"] == b["event_sha256"]
    c = j2.append("PRUNED", {"candidate_id": "c1"})
    assert c["sequence"] == 3
    assert c["previous_event_sha256"] == b["event_sha256"]


def test_journal_lock_is_released_after_close(tmp_path):
    """After the first journal is closed, a second journal must succeed."""
    path = tmp_path / "events.jsonl"
    j1 = EventJournal(path)
    a = j1.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    del j1  # release the lock

    j2 = EventJournal(path)
    b = j2.append("SUCCEEDED", {"candidate_id": "c1"})
    assert b["previous_event_sha256"] == a["event_sha256"]


def test_journal_fsync_persists_on_disk(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1"})
    journal.append("SUCCEEDED", {"candidate_id": "c1"})
    # File must contain exactly 2 JSONL lines.
    raw = path.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2
    decoded = [json.loads(line) for line in raw]
    assert decoded[0]["sequence"] == 1
    assert decoded[1]["sequence"] == 2


def test_journal_read_all_empty(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    assert journal.read_all() == []


def test_journal_appends_payload_and_event_time(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    record = journal.append("CANDIDATE_REGISTERED", {"candidate_id": "c1", "foo": [1, 2, 3]})
    assert record["payload"] == {"candidate_id": "c1", "foo": [1, 2, 3]}
    assert "event_time_utc" in record
    assert record["event_time_utc"].endswith("Z") or "+" in record["event_time_utc"]
