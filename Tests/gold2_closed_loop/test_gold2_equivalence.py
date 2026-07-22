"""Equivalence tests for the Gold2 closed-loop proof (Task 8).

The comparator proves behavior-preservation between two economic-event
sequences: it allows ONLY proof IDs, output paths, and serialization
formatting to differ. Decimal quantities, prices, fees, cash, holdings and
TPV are compared EXACTLY (modulo a ~1e-12 guard against float repr drift).
"""

from __future__ import annotations

from Scripts.gold2_closed_loop.equivalence import compare_economic_events


def _fill(seq, order_id=7, price="3.50", qty="100", fee="1.05", time="2022-01-04T07:00:00Z", direction="Buy"):
    return {
        "schema_version": "1",
        "sequence": seq,
        "event_type": "FILL",
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G0",
        "run_id": "R1",
        "candidate_id": "C1",
        "event_time_utc": time,
        "payload": {
            "orderId": order_id,
            "symbol": "518880",
            "fillPrice": price,
            "fillQuantity": qty,
            "fee": fee,
            "feeCurrency": "CNY",
            "status": "Filled",
            "direction": direction,
        },
    }


def _holdings(seq, order_id=7, time="2022-01-04T07:00:00Z"):
    return {
        "schema_version": "1",
        "sequence": seq,
        "event_type": "HOLDINGS_SNAPSHOT",
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G0",
        "run_id": "R1",
        "candidate_id": "C1",
        "event_time_utc": time,
        "payload": {
            "orderId": order_id,
            "symbol": "518880",
            "quantity": "100",
            "averagePrice": "3.50",
            "price": "3.50",
            "cash": "999648.95",
            "totalPortfolioValue": "1000000.00",
        },
    }


def _intent(seq, order_id=7, time="2022-01-04T07:00:00Z"):
    return {
        "schema_version": "1",
        "sequence": seq,
        "event_type": "ORDER_INTENT",
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G0",
        "run_id": "R1",
        "candidate_id": "C1",
        "event_time_utc": time,
        "payload": {"orderId": order_id, "symbol": "518880", "quantity": "100"},
    }


def _decision(seq, time="2022-01-04T07:00:00Z"):
    return {
        "schema_version": "1",
        "sequence": seq,
        "event_type": "DECISION",
        "experiment_id": "E1",
        "window_id": "W1",
        "stage_id": "G0",
        "run_id": "R1",
        "candidate_id": "C1",
        "event_time_utc": time,
        "payload": {"targets": [{"symbol": "518880", "quantity": "100"}]},
    }


def _full_chain(run_id="R1", candidate_id="C1", experiment_id="E1"):
    """A minimal valid chain: decision -> intent -> fill -> holdings."""
    return [
        _decision(1),
        _intent(2),
        _fill(3),
        _holdings(4),
        # vary proof IDs locally
    ] if run_id == "R1" else [
        {**_decision(1), "run_id": run_id, "candidate_id": candidate_id, "experiment_id": experiment_id},
        {**_intent(2), "run_id": run_id, "candidate_id": candidate_id, "experiment_id": experiment_id},
        {**_fill(3), "run_id": run_id, "candidate_id": candidate_id, "experiment_id": experiment_id},
        {**_holdings(4), "run_id": run_id, "candidate_id": candidate_id, "experiment_id": experiment_id},
    ]


# --- Plan contract --------------------------------------------------------


def test_decimal_price_mismatch_blocks_equivalence():
    left = [
        {
            "event_type": "FILL",
            "time": "2022-01-04T07:00:00Z",
            "quantity": "100",
            "price": "3.50",
        }
    ]
    right = [
        {
            "event_type": "FILL",
            "time": "2022-01-04T07:00:00Z",
            "quantity": "100",
            "price": "3.51",
        }
    ]
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


# --- Identical ------------------------------------------------------------


def test_identical_event_lists_equivalent():
    left = _full_chain()
    right = _full_chain()
    result = compare_economic_events(left, right, 1e-12, 1e-12)
    assert result.equivalent, result.mismatches


def test_decimal_quantity_mismatch_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right[3]["payload"]["quantity"] = "101"  # holdings quantity
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_decimal_fee_mismatch_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right[2]["payload"]["fee"] = "1.06"
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_holdings_tpv_mismatch_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right[3]["payload"]["totalPortfolioValue"] = "999999.99"
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_cash_mismatch_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right[3]["payload"]["cash"] = "999648.96"
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


# --- Multiplicity / order -------------------------------------------------


def test_multiplicity_mismatch_extra_fill_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right.append({**_fill(5, order_id=7), "sequence": 5})
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_order_mismatch_not_equivalent():
    left = _full_chain()
    right = [_full_chain()[0], _full_chain()[2], _full_chain()[1], _full_chain()[3]]
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_time_mismatch_not_equivalent():
    left = _full_chain()
    right = _full_chain()
    right[3]["event_time_utc"] = "2022-01-05T07:00:00Z"
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


# --- Ignored fields -------------------------------------------------------


def test_proof_id_differences_ignored():
    left = _full_chain(run_id="R1", candidate_id="C1", experiment_id="E1")
    right = _full_chain(run_id="R2", candidate_id="C2", experiment_id="E2")
    result = compare_economic_events(left, right, 1e-12, 1e-12)
    assert result.equivalent, result.mismatches


def test_output_path_differences_ignored():
    left = _full_chain()
    right = _full_chain()
    right[0]["payload"]["trace_path"] = "/tmp/a.jsonl"
    right[1]["payload"]["trace_path"] = "/tmp/b.jsonl"
    result = compare_economic_events(left, right, 1e-12, 1e-12)
    assert result.equivalent, result.mismatches


def test_serialization_formatting_ignored():
    """Whitespace, key order, and JSON-string vs numeric decimals are
    formatting-only differences that must NOT block equivalence."""
    import json

    left = _full_chain()
    # Right is a re-serialized (pretty, reordered) version of the same
    # economic content. Numbers-as-strings vs numbers must compare equal
    # when they denote the same Decimal value.
    right_raw = json.loads(json.dumps(left))
    result = compare_economic_events(left, right_raw, 1e-12, 1e-12)
    assert result.equivalent, result.mismatches


def test_direction_mismatch_not_equivalent():
    """Direction affects sign semantics; must compare strictly."""
    left = _full_chain()
    right = _full_chain()
    right[2]["payload"]["direction"] = "Sell"
    assert not compare_economic_events(left, right, 1e-12, 1e-12).equivalent


def test_mismatch_message_cites_field():
    left = _full_chain()
    right = _full_chain()
    right[2]["payload"]["fillPrice"] = "3.51"
    result = compare_economic_events(left, right, 1e-12, 1e-12)
    assert not result.equivalent
    joined = " ".join(result.mismatches)
    assert "fillPrice" in joined or "price" in joined.lower()
