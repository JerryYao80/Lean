"""Failing contracts for the proof-local formal review adapter (Task 11,
Phase 2 STOP gate).

The formal review adapter derives ONE immutable frozen review bundle from a
G1 candidate's formal trace + LEAN-native result packet. Design invariants
this test pins down (spec §8, §13, §14):

* ``w_realized`` (the realized position weight used for telescoping
  attribution) must be derived from post-fill ``HOLDINGS_SNAPSHOT``
  events in the formal trace, NEVER from P&L / dp (the diagnostic fallback
  in ``Scripts/review/adapters/gold2.py`` is forbidden in formal mode).
* A missing OR incomplete formal trace invalidates the review with
  ``validity_status=INVALID, invalid_reason=MISSING_FORMAL_TRACE`` — there
  is NO residual fallback. (spec §8: "feedback adapter 在无 formal trace
  时正式模式必须返回 INVALID/MISSING_FORMAL_TRACE".)
* Exactly one canonical bundle is written per review run; its content is
  hash-stable (two reviews of the same inputs produce the same bundle hash).
* The review never re-ranks candidates: it attributes the frozen G1
  candidate only.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.adapters.formal_review import (
    FormalReviewAdapter,
    ReviewBundle,
)


def _write_trace(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return path


def _identity(seq: int, event_type: str, ts: str, payload: dict) -> dict:
    return {
        "schema_version": "1",
        "sequence": seq,
        "event_type": event_type,
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G1",
        "run_id": "proof-g1",
        "candidate_id": "C1",
        "event_time_utc": ts,
        "payload": payload,
    }


def _minimal_valid_trace() -> list[dict]:
    """A trace that passes FormalTraceReconciler: DECISION -> ORDER_INTENT
    -> FILL -> HOLDINGS_SNAPSHOT, one-to-one per orderId."""
    return [
        _identity(1, "DECISION", "2022-01-03T00:00:00Z",
                  {"targets": [{"symbol": "518880", "quantity": 100}]}),
        _identity(2, "ORDER_INTENT", "2022-01-03T00:00:01Z",
                  {"orderId": 1, "symbol": "518880", "quantity": 100}),
        _identity(3, "FILL", "2022-01-03T00:00:02Z",
                  {"orderId": 1, "symbol": "518880", "fillPrice": 5.0,
                   "fillQuantity": 100, "fee": 2.5, "feeCurrency": "CNY",
                   "status": "Filled", "direction": "Buy"}),
        _identity(4, "HOLDINGS_SNAPSHOT", "2022-01-03T00:00:03Z",
                  {"orderId": 1, "symbol": "518880", "quantity": 100,
                   "averagePrice": 5.0, "price": 5.0, "cash": 250.0,
                   "totalPortfolioValue": 750.0}),
    ]


# --- missing / incomplete trace -> INVALID / MISSING_FORMAL_TRACE ------


def test_missing_trace_invalidates_review(tmp_path):
    result = FormalReviewAdapter().run(tmp_path / "missing.jsonl")
    assert (result.validity_status, result.invalid_reason) == (
        "INVALID", "MISSING_FORMAL_TRACE",
    )
    assert result.bundle is None


def test_empty_trace_invalidates_review(tmp_path):
    p = tmp_path / "trace.jsonl"
    p.write_text("", encoding="utf-8")
    result = FormalReviewAdapter().run(p)
    assert result.validity_status == "INVALID"
    assert result.invalid_reason == "MISSING_FORMAL_TRACE"


def test_incomplete_trace_no_holdings_snapshot_invalidates(tmp_path):
    """A trace with a FILL but no correlated HOLDINGS_SNAPSHOT fails
    reconciliation (intent-before-fill/snapshot-after-fill) -> INVALID,
    not residual fallback."""
    events = _minimal_valid_trace()[:3]  # drop the HOLDINGS_SNAPSHOT
    p = _write_trace(tmp_path / "trace.jsonl", events)
    result = FormalReviewAdapter().run(p)
    assert result.validity_status == "INVALID"
    assert result.invalid_reason == "MISSING_FORMAL_TRACE"
    assert result.bundle is None


# --- one immutable bundle, hash-stable --------------------------------


def test_one_immutable_bundle_written(tmp_path):
    trace = _write_trace(tmp_path / "trace.jsonl", _minimal_valid_trace())
    out_dir = tmp_path / "bundle"
    result = FormalReviewAdapter().run(trace, bundle_dir=out_dir)
    assert result.validity_status == "VALID"
    assert result.bundle is not None
    assert result.bundle.bundle_path.is_file()
    # Exactly one bundle file (no residual/duplicate outputs).
    assert len(list(out_dir.glob("*.json"))) == 1


def test_bundle_hash_stable_across_runs(tmp_path):
    trace = _write_trace(tmp_path / "trace.jsonl", _minimal_valid_trace())
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    ra = FormalReviewAdapter().run(trace, bundle_dir=out_a)
    rb = FormalReviewAdapter().run(trace, bundle_dir=out_b)
    assert ra.bundle is not None and rb.bundle is not None
    assert ra.bundle.bundle_sha256 == rb.bundle.bundle_sha256


# --- w_realized from post-fill holdings, never P&L/dp ------------------


def test_w_realized_derived_from_holdings_snapshot(tmp_path):
    """w_realized must come from the post-fill HOLDINGS_SNAPSHOT quantity
    (LEAN-native holdings), not profit_loss/dp. We use a trace whose
    P&L/dp-implied weight would differ from the holdings quantity so the
    two are distinguishable."""
    events = _minimal_valid_trace()
    # quantity in HOLDINGS_SNAPSHOT = 100, TPV = 750, price = 5.0
    # w_realized (holdings) = quantity * price / TPV = 100*5/750 = 0.6666...
    # A P&L/dp fallback would compute weight from profit_loss, which is
    # not even present in the trace — so any non-None weight must derive
    # from the holdings snapshot, not P&L.
    trace = _write_trace(tmp_path / "trace.jsonl", events)
    result = FormalReviewAdapter().run(trace, bundle_dir=tmp_path / "b")
    assert result.bundle is not None
    w = result.bundle.w_realized
    assert w is not None
    # 100 * 5.0 / 750 = 0.6666...
    assert w == pytest.approx(Decimal("100") * Decimal("5") / Decimal("750"),
                              abs=Decimal("1e-9"))


def test_review_never_re_ranks_candidates(tmp_path):
    """The review attributes the FROZEN G1 candidate only. Passing an
    explicit candidate_id list (as a future-proofing seam) must not let
    the review pick a different candidate than the one in the trace."""
    trace = _write_trace(tmp_path / "trace.jsonl", _minimal_valid_trace())
    result = FormalReviewAdapter().run(trace, bundle_dir=tmp_path / "b")
    assert result.bundle is not None
    # The bundle's candidate_id matches the trace's candidate_id (C1).
    assert result.bundle.candidate_id == "C1"
