"""Failing contracts and validation tests for the formal trace loader (Task 8).

The loader replicates the C# ``FormalTraceReconciler`` order/fill/holdings
correlation invariants (Algorithm.CSharp/Gold2ClosedLoop/FormalTraceReconciler.cs)
so that a Python-side STOP gate can reject a broken trace deterministically
without re-running the C# engine.
"""

from __future__ import annotations

import json

import pytest

from Scripts.gold2_closed_loop.formal_trace import (
    FormalEvent,
    load_trace,
    validate_formal_events,
)

# Snake-case JSON property names emitted by FormalTraceEvent.cs
# [JsonProperty(PropertyName=...)].


def _base_event(**overrides):
    """A minimal valid DECISION event with full identity tuple."""
    ev = {
        "schema_version": "1",
        "sequence": 1,
        "event_type": "DECISION",
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G0",
        "run_id": "R1",
        "candidate_id": "C1",
        "event_time_utc": "2022-01-04T07:00:00Z",
        "payload": {"targets": [{"symbol": "518880", "quantity": "100"}]},
    }
    ev.update(overrides)
    return ev


def _intent(seq, order_id=7, time="2022-01-04T07:00:00Z"):
    return _base_event(
        sequence=seq,
        event_type="ORDER_INTENT",
        event_time_utc=time,
        payload={"orderId": order_id, "symbol": "518880", "quantity": "100"},
    )


def _fill(seq, order_id=7, time="2022-01-04T07:00:00Z"):
    return _base_event(
        sequence=seq,
        event_type="FILL",
        event_time_utc=time,
        payload={
            "orderId": order_id,
            "symbol": "518880",
            "fillPrice": "3.50",
            "fillQuantity": "100",
            "fee": "1.05",
            "feeCurrency": "CNY",
            "status": "Filled",
            "direction": "Buy",
        },
    )


def _holdings(seq, order_id=7, time="2022-01-04T07:00:00Z"):
    return _base_event(
        sequence=seq,
        event_type="HOLDINGS_SNAPSHOT",
        event_time_utc=time,
        payload={
            "orderId": order_id,
            "symbol": "518880",
            "quantity": "100",
            "averagePrice": "3.50",
            "price": "3.50",
            "cash": "999648.95",
            "totalPortfolioValue": "1000000.00",
        },
    )


# --- Plan contract: an RL-state dict is NOT a formal event ----------------


def test_rl_state_is_not_formal():
    # The mature Gold2 RL-state export writes {"time","tpv"} which has no
    # event_type / schema_version; the strict loader must reject it as an
    # unknown formal event.
    with pytest.raises(ValueError, match="unknown formal event"):
        validate_formal_events([{"time": "2022-01-04", "tpv": 1000000}])


# --- Valid full chain ------------------------------------------------------


def test_valid_full_chain_passes():
    events = [
        _base_event(sequence=1, event_type="DECISION"),
        _intent(2, order_id=7),
        _fill(3, order_id=7),
        _holdings(4, order_id=7),
    ]
    validate_formal_events(events)  # must not raise


def test_valid_chain_multi_order_passes():
    events = [
        _base_event(sequence=1, event_type="DECISION"),
        _intent(2, order_id=1),
        _fill(3, order_id=1),
        _holdings(4, order_id=1),
        _intent(5, order_id=2),
        _fill(6, order_id=2),
        _holdings(7, order_id=2),
    ]
    validate_formal_events(events)


# --- Sequence rules -------------------------------------------------------


def test_sequence_gap_rejected():
    events = [_base_event(sequence=1), _base_event(sequence=3)]
    with pytest.raises(ValueError, match="sequence"):
        validate_formal_events(events)


def test_sequence_duplicate_rejected():
    events = [_base_event(sequence=1), _base_event(sequence=1)]
    with pytest.raises(ValueError, match="sequence"):
        validate_formal_events(events)


def test_sequence_zero_first_rejected():
    events = [_base_event(sequence=0)]
    with pytest.raises(ValueError, match="sequence"):
        validate_formal_events(events)


# --- Time rules -----------------------------------------------------------


def test_nondecreasing_time_violation_rejected():
    events = [
        _base_event(sequence=1, event_time_utc="2022-01-04T07:00:00Z"),
        _base_event(sequence=2, event_time_utc="2022-01-03T07:00:00Z"),
    ]
    with pytest.raises(ValueError, match="time"):
        validate_formal_events(events)


def test_equal_time_allowed():
    events = [
        _base_event(sequence=1, event_time_utc="2022-01-04T07:00:00Z"),
        _base_event(sequence=2, event_time_utc="2022-01-04T07:00:00Z"),
    ]
    validate_formal_events(events)


# --- Event type / schema --------------------------------------------------


def test_unknown_event_type_rejected():
    events = [_base_event(sequence=1, event_type="WHATEVER")]
    with pytest.raises(ValueError, match="unknown.*event"):
        validate_formal_events(events)


def test_missing_schema_version_rejected():
    ev = _base_event(sequence=1)
    ev.pop("schema_version")
    with pytest.raises(ValueError, match="schema_version"):
        validate_formal_events([ev])


# --- Identity tuple -------------------------------------------------------


def test_missing_identity_field_rejected():
    ev = _base_event(sequence=1)
    ev.pop("run_id")
    with pytest.raises(ValueError, match="run_id"):
        validate_formal_events([ev])


# --- NaN / Inf payload ----------------------------------------------------


def test_nan_payload_rejected():
    ev = _base_event(sequence=1)
    ev["payload"] = {"targets": [{"symbol": "518880", "quantity": float("nan")}]}
    with pytest.raises(ValueError, match="nan|inf|finite"):
        validate_formal_events([ev])


def test_inf_payload_rejected():
    ev = _base_event(sequence=1)
    ev["payload"] = {"targets": [{"symbol": "518880", "quantity": float("inf")}]}
    with pytest.raises(ValueError, match="nan|inf|finite"):
        validate_formal_events([ev])


# --- Reconciliation (replicate C# FormalTraceReconciler) ------------------


def test_fill_without_intent_rejected():
    events = [_base_event(sequence=1), _fill(2, order_id=7)]
    with pytest.raises(ValueError, match="ORDER_INTENT"):
        validate_formal_events(events)


def test_fill_without_following_snapshot_rejected():
    events = [_intent(1, order_id=7), _fill(2, order_id=7)]
    with pytest.raises(ValueError, match="HOLDINGS_SNAPSHOT"):
        validate_formal_events(events)


def test_spurious_snapshot_rejected():
    # Snapshot with no preceding fill awaiting a snapshot.
    events = [
        _intent(1, order_id=7),
        _fill(2, order_id=7),
        _holdings(3, order_id=7),
        _holdings(4, order_id=7),  # spurious: nothing pending
    ]
    with pytest.raises(ValueError, match="spurious|HOLDINGS_SNAPSHOT"):
        validate_formal_events(events)


def test_wrong_order_id_rejected():
    events = [
        _intent(1, order_id=7),
        _fill(2, order_id=8),  # no intent for orderId 8
    ]
    with pytest.raises(ValueError, match="ORDER_INTENT"):
        validate_formal_events(events)


def test_multi_fill_multi_snapshot_pass():
    events = [
        _intent(1, order_id=7),
        _fill(2, order_id=7),
        _holdings(3, order_id=7),
        _fill(4, order_id=7),
        _holdings(5, order_id=7),
    ]
    validate_formal_events(events)


def test_multi_fill_one_snapshot_fail():
    events = [
        _intent(1, order_id=7),
        _fill(2, order_id=7),
        _fill(3, order_id=7),
        _holdings(4, order_id=7),  # only closes one fill
    ]
    with pytest.raises(ValueError, match="HOLDINGS_SNAPSHOT"):
        validate_formal_events(events)


def test_duplicate_open_intent_rejected():
    events = [
        _intent(1, order_id=7),
        _intent(2, order_id=7),  # duplicate intent without intervening fill
    ]
    with pytest.raises(ValueError, match="ORDER_INTENT"):
        validate_formal_events(events)


# --- load_trace round-trip ------------------------------------------------


def test_load_trace_reads_real_jsonl(tmp_path):
    events = [
        _base_event(sequence=1, event_type="DECISION"),
        _intent(2, order_id=7),
        _fill(3, order_id=7),
        _holdings(4, order_id=7),
    ]
    path = tmp_path / "trace.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for ev in events:
            stream.write(json.dumps(ev) + "\n")
    loaded = load_trace(path)
    assert len(loaded) == 4
    assert isinstance(loaded[0], FormalEvent)
    assert loaded[0].event_type == "DECISION"
    assert loaded[3].event_type == "HOLDINGS_SNAPSHOT"
    assert loaded[2].payload["orderId"] == 7
    # And the loaded list must itself pass validation.
    validate_formal_events([ev_to_dict(e) for e in loaded])


def test_load_trace_rejects_malformed_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"schema_version": "1"}\n{not json\n', encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        load_trace(path)


def ev_to_dict(ev: FormalEvent) -> dict:
    return {
        "schema_version": ev.schema_version,
        "sequence": ev.sequence,
        "event_type": ev.event_type,
        "experiment_id": ev.experiment_id,
        "window_id": ev.window_id,
        "stage_id": ev.stage_id,
        "run_id": ev.run_id,
        "candidate_id": ev.candidate_id,
        "event_time_utc": ev.event_time_utc,
        "payload": ev.payload,
    }
