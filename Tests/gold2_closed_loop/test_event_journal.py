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
from Scripts.gold2_closed_loop.schemas import validate_candidate_event


def _sha(record: dict) -> str:
    """Re-compute the canonical hash so tests are independent of helpers."""
    copy = {k: v for k, v in record.items() if k != "event_sha256"}
    text = json.dumps(copy, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- anchor tests from the plan (verbatim) --------------------------


def test_previous_hash_mismatch_is_rejected(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    first = journal.append("REGISTERED", {"candidate_id": "c1"})
    # Supply sequence + previous_event_sha256 (wrong) + the other required
    # fields so we reach the chain-tail validation step.
    with pytest.raises(ValueError, match="previous hash"):
        journal.append_raw(
            {
                "sequence": 2,
                "event_id": "ev-x",
                "event_type": "SUCCEEDED",
                "previous_event_sha256": "0" * 64,  # wrong (should be first's hash)
                "candidate_id": "c1",
                "execution_status": "SUCCEEDED",
                "partition": "W1/train",
                "parameters": {},
                "metrics": None,
                "attempted_at_utc": "2026-07-14T00:00:00Z",
            }
        )


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
    first = journal.append("REGISTERED", {"candidate_id": "c1"})
    second = journal.append_raw(
        {
            "sequence": 2,
            "event_type": "SUCCEEDED",
            "event_id": "ev-2",
            "previous_event_sha256": first["event_sha256"],
            "candidate_id": "c1",
            "execution_status": "SUCCEEDED",
            "partition": "W1/train",
            "parameters": {"run_id": "r1"},
            "metrics": None,
            "attempted_at_utc": "2026-07-14T00:00:00Z",
        }
    )
    assert second["sequence"] == 2
    assert second["event_sha256"] == _sha(second)
    assert second["previous_event_sha256"] == first["event_sha256"]


def test_journal_append_raw_rejects_wrong_sequence(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    first = journal.append("REGISTERED", {"candidate_id": "c1"})
    with pytest.raises(ValueError, match="sequence"):
        journal.append_raw(
            {
                "sequence": 99,
                "event_type": "SUCCEEDED",
                "event_id": "ev-x",
                "previous_event_sha256": first["event_sha256"],
                "candidate_id": "c1",
                "execution_status": "SUCCEEDED",
                "partition": "W1/train",
                "parameters": {"run_id": "r1"},
                "metrics": None,
                "attempted_at_utc": "2026-07-14T00:00:00Z",
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
    # Important 4: payload keys are promoted to the top level (flat record),
    # and the timestamp field is ``attempted_at_utc`` (not ``event_time_utc``).
    assert record["candidate_id"] == "c1"
    assert record["foo"] == [1, 2, 3]
    assert "payload" not in record
    assert "event_time_utc" not in record
    assert "attempted_at_utc" in record
    assert record["attempted_at_utc"].endswith("Z")


# --- Critical 1: concurrent chain integrity --------------------------
# Two EventJournal instances both cache the empty-tail state BEFORE either
# appends. Without the in-lock re-scan, both would write sequence=1 with
# previous_event_sha256 = 0*64 and the on-disk chain would break silently.


def test_concurrent_writers_preserve_chain(tmp_path):
    """Two instances that opened before either wrote must not break the chain."""
    path = tmp_path / "events.jsonl"
    j1 = EventJournal(path)
    j2 = EventJournal(path)  # both cache the empty-tail state
    a = j1.append("REGISTERED", {"candidate_id": "c1"})
    b = j2.append("REGISTERED", {"candidate_id": "c2"})
    # Sequences must be a contiguous 1..N set with no duplicates.
    assert {a["sequence"], b["sequence"]} == {1, 2}
    # The second writer's previous hash must equal the first writer's hash.
    later = a if a["sequence"] > b["sequence"] else b
    earlier = b if later is a else a
    assert later["previous_event_sha256"] == earlier["event_sha256"]
    # Re-open and verify the on-disk chain end-to-end.
    j3 = EventJournal(path)
    records = j3.read_all()
    assert [r["sequence"] for r in records] == [1, 2]
    assert records[0]["previous_event_sha256"] == "0" * 64
    assert records[1]["previous_event_sha256"] == records[0]["event_sha256"]
    for r in records:
        assert r["event_sha256"] == _sha(r)


def test_concurrent_writers_three_way_chain(tmp_path):
    """Three instances constructed before any write must still chain."""
    path = tmp_path / "events.jsonl"
    js = [EventJournal(path) for _ in range(3)]
    out = [j.append("REGISTERED", {"candidate_id": f"c{i}"}) for i, j in enumerate(js)]
    seqs = sorted(r["sequence"] for r in out)
    assert seqs == [1, 2, 3]
    jv = EventJournal(path)
    records = jv.read_all()
    assert [r["sequence"] for r in records] == [1, 2, 3]
    for index, r in enumerate(records[1:], start=1):
        assert r["previous_event_sha256"] == records[index - 1]["event_sha256"]


# --- Critical 2: cross-instance duplicate event_id rejection ------------


def test_cross_instance_duplicate_event_id_rejected(tmp_path):
    """A second instance with a stale seen-set must still reject an on-disk dup."""
    path = tmp_path / "events.jsonl"
    j1 = EventJournal(path)
    j2 = EventJournal(path)  # caches empty seen-set before j1 writes
    j1.append("REGISTERED", {"candidate_id": "c1", "event_id": "dup-id"})
    with pytest.raises(ValueError, match="duplicate"):
        j2.append("REGISTERED", {"candidate_id": "c2", "event_id": "dup-id"})


# --- Important 3: torn-tail recovery ----------------------------------


def test_read_all_skips_torn_last_line(tmp_path):
    """A partial last line (no trailing newline) is skipped, not fatal."""
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append("REGISTERED", {"candidate_id": "c1"})
    # Append a torn (partial) JSON line with NO trailing newline. Simulates a
    # crash mid-write.
    with path.open("ab") as fh:
        fh.write(b'{"sequence":2,"event_id":"torn"')  # no newline, incomplete
    # read_all must skip the torn line and return only the valid record.
    fresh = EventJournal(path)
    records = fresh.read_all()
    assert len(records) == 1
    assert records[0]["sequence"] == 1
    # And a brand-new EventJournal must construct without raising.
    again = EventJournal(path)
    assert again.read_all()[0]["sequence"] == 1


def test_read_all_raises_on_corrupt_middle_line(tmp_path):
    """A corrupt line that WAS completed (has a newline) is genuine corruption."""
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append("REGISTERED", {"candidate_id": "c1"})
    # Write a corrupt but COMPLETE (newline-terminated) line, then a valid one.
    with path.open("ab") as fh:
        fh.write(b'{"bogus":not-json}\n')  # corrupt but newline-terminated
    with pytest.raises(ValueError, match="line"):
        EventJournal(path).read_all()


def test_read_all_raises_on_corrupt_last_line_with_newline(tmp_path):
    """A corrupt last line that ends with a newline is NOT a torn write."""
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append("REGISTERED", {"candidate_id": "c1"})
    with path.open("ab") as fh:
        fh.write(b'{"bogus":not-json}\n')  # corrupt + newline => genuine corruption
    with pytest.raises(ValueError, match="line"):
        EventJournal(path).read_all()


# --- Important 4: fully-formed candidate record validates --------------


def _full_candidate_payload(candidate_id="c1"):
    """A payload dict with every candidate-event schema field populated."""
    return {
        "candidate_id": candidate_id,
        "execution_status": "PENDING",
        "partition": "W1/train",
        "parameters": {"p": 1},
        "metrics": None,
    }


def test_fully_formed_candidate_record_validates(tmp_path):
    """After append, a fully-formed record must satisfy validate_candidate_event."""
    journal = EventJournal(tmp_path / "events.jsonl")
    record = journal.append("REGISTERED", _full_candidate_payload("c9"))
    # The on-disk record must pass the candidate-event schema (Important 4 fix).
    validate_candidate_event(record)


# --- Minor 6 / nested NaN in preregistration --------------------------


def test_preregistration_nested_nan_rejected(tmp_path):
    """A NaN nested inside success_thresholds is rejected by validate_preregistration."""
    from Scripts.gold2_closed_loop.schemas import validate_preregistration

    draft = {
        "experiment_id": "gold2-proof-2026-001",
        "session_timezone": "Asia/Shanghai",
        "session_close_time": "15:00:00",
        "windows": {
            "W1": {
                "train": ["2018-01-02", "2020-12-31"],
                "review": ["2021-01-01", "2021-12-31"],
                "blind": ["2022-01-01", "2022-12-31"],
            },
            "W2": {
                "train": ["2019-01-01", "2021-12-31"],
                "review": ["2022-01-01", "2022-12-31"],
                "blind": ["2023-01-01", "2023-12-31"],
            },
            "W3": {
                "train": ["2020-01-01", "2022-12-31"],
                "review": ["2023-01-01", "2023-12-31"],
                "blind": ["2024-01-01", "2024-12-31"],
            },
            "W4": {
                "train": ["2021-01-01", "2023-12-31"],
                "review": ["2024-01-01", "2024-12-31"],
                "blind": ["2025-01-01", "2025-12-31"],
            },
        },
        "random_seed_set": [17, 23, 31, 47],
        "candidate_budget": 256,
        "retry_policy": {
            "max_infrastructure_retries": 2,
            "retry_only_when_results_unobserved": True,
        },
        "metric_paths": {"total_net_profit": "statistics.TotalNetProfit"},
        "as_of_utc": "2026-07-14T00:00:00Z",
        "maximum_staleness_days": 1,
        "equivalence_tolerances": {"decimal": 0.0, "double": 1e-12},
        "success_thresholds": {
            "max_single_trade_contribution_fraction": 0.25,
            "max_single_month_contribution_fraction": 0.40,
            "leave_one_window_out_min_delta": 0.0001,
            "paired_bootstrap_min_probability": 0.95,
            "paired_bootstrap_ci_level": 0.90,
            "max_drawdown_degradation": 0.10,
        },
        "g3": {
            "attribution_gap_threshold": 0.001,
            "non_convergence_threshold": 0.05,
            "float_tolerance": 1e-9,
            "generation_continuity_rule": "BREAK_ON_FAILURE",
            "min_generations_for_trigger": 3,
            "per_generation_candidate_budget": 16,
            "shaping_weight_cap": 3.0,
            "min_generations_per_window": 1,
            "max_generations_per_window": 6,
            "generation_budget_counts_in_candidate_budget": True,
            "allow_post_trigger_generations": False,
        },
    }
    # Nested NaN inside success_thresholds must be caught.
    draft["success_thresholds"]["max_single_trade_contribution_fraction"] = float("nan")
    with pytest.raises(ValueError, match="non-finite|NaN|finite"):
        validate_preregistration(draft)


def test_preregistration_nan_inside_list_rejected():
    """A NaN inside a list value (candidate-event parameters, which the schema
    leaves as an arbitrary object) must be caught by the finite-value walker
    that recurses into lists."""
    from Scripts.gold2_closed_loop.schemas import validate_candidate_event

    record = {
        "event_id": "a" * 32,
        "sequence": 1,
        "candidate_id": "c1",
        "event_type": "REGISTERED",
        "execution_status": "PENDING",
        "partition": "W1/train",
        "parameters": {"seeds": [17, 23, float("nan")]},
        "metrics": None,
        "attempted_at_utc": "2026-07-14T00:00:00Z",
        "previous_event_sha256": "0" * 64,
        "event_sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="non-finite|NaN|finite"):
        validate_candidate_event(record)
