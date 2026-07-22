"""Opt-in real LEAN integration test for the Gold2 closed-loop proof
STOP P1 gate (Task 8).

This test is SKIPPED unless the environment variable
``GOLD2_PROOF_INTEGRATION`` is set to exactly ``"1"``. When enabled, it runs
the mature ``Gold2BetaVolTargetStrategy`` AND the proof
``Gold2ClosedLoopProofStrategy`` on identical short W1-train data
(2018-01-02..2018-03-31), then asserts behavior-preservation:

  * The proof trace validates (``formal_trace.validate_formal_events``).
  * The proof packet's fills equal the mature packet's fills via
    ``compare_economic_events`` (both normalized to the economic-event
    shape from LEAN-native result-packet trades).
  * The proof trace's FILL events match the proof packet's fills
    (self-consistency).

Design decision (mature-vs-proof comparison source)
---------------------------------------------------
The mature ``Gold2BetaVolTargetStrategy`` does NOT emit a formal trace. So
equivalence cannot be trace-vs-trace. Instead we prove BEHAVIOR-PRESERVATION
of the instrumentation by comparing LEAN-native result packets: both runs
write an ``<run_id>.json`` packet and an ``<run_id>-order-events.json``
sidecar. We normalize the filled order events from BOTH packets to the
economic-event shape and assert they are equivalent. This proves the proof
strategy's trace decorator did not perturb mature economic behavior.

If the environment is missing the built DLL or 518880 data, the opt-in test
is SKIPPED (env var not set by default) so unit tests still pass. If it
fails due to a REAL equivalence mismatch, the caller reports
DONE_WITH_CONCERNS with the mismatch — this test does NOT modify the mature
or proof strategy to force a pass.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure Scripts is importable when run from the worktree root.
_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

# Imports below are after sys.path manipulation; suppress E402.
from Scripts.gold2_closed_loop.equivalence import compare_economic_events  # noqa: E402
from Scripts.gold2_closed_loop.formal_trace import load_trace, validate_formal_events  # noqa: E402
from Scripts.gold2_closed_loop.lean_artifacts import load_exact_packet  # noqa: E402
from Scripts.gold2_closed_loop.lean_runner import build_run_config, run_lean  # noqa: E402

_INTEGRATION_ENV = "GOLD2_PROOF_INTEGRATION"

# LEAN OrderStatus.Filled integer value (Common/Orders/OrderTypes.cs:158).
_ORDER_STATUS_FILLED = 3
# LEAN OrderDirection.Buy=0, Sell=1 (Common/Orders/OrderTypes.cs:87).
_DIRECTION_NAMES = {0: "Buy", 1: "Sell", "buy": "Buy", "sell": "Sell"}

_MATURE_STRATEGY = "Gold2BetaVolTargetStrategy"
_PROOF_STRATEGY = "Gold2ClosedLoopProofStrategy"

# W1-train short slice (518880 data starts 2017-11-10; early 2018 available).
_SLICE_START = "2018-01-02"
_SLICE_END = "2018-03-31"

_BASE_CONFIG_PATH = (
    _WORKTREE / "Scripts" / "gold2_closed_loop" / "config" / "proof_lean_base.json"
)


def _skip_if_not_enabled() -> None:
    if os.environ.get(_INTEGRATION_ENV) != "1":
        pytest.skip(
            f"set {_INTEGRATION_ENV}=1 to run the real LEAN STOP P1 gate "
            f"(requires built DLL + 518880 data)"
        )


def _skip_if_dll_missing() -> None:
    dll = (
        _WORKTREE
        / "Algorithm.CSharp"
        / "bin"
        / "Debug"
        / "QuantConnect.Algorithm.CSharp.dll"
    )
    if not dll.is_file():
        pytest.skip(f"proof DLL not built: {dll}")


def _skip_if_data_missing() -> None:
    # 518880 daily data lives under the main Data tree. The worktree config
    # uses data-folder="../../../Data" which resolves to the main Lean/Data
    # directory from the worktree root.
    z = _WORKTREE / ".." / ".." / ".." / "Data" / "equity" / "sse" / "daily" / "518880.zip"
    z = z.resolve()
    if not z.is_file():
        pytest.skip(f"518880 daily data not found: {z}")


def _load_base_config() -> dict[str, Any]:
    """Load the proof base config and force algorithm-location to the
    freshly-built WORKTREE DLL.

    The base config's ``../../../Algorithm.CSharp/bin/Debug/...`` resolves
    (from the worktree root) to the MAIN repo's DLL, which may be stale and
    lack the proof strategy type. The integration test must run against the
    worktree's own freshly-built DLL. We override the in-memory dict only
    (the immutable ``proof_lean_base.json`` file is never modified).
    """
    base = json.loads(_BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    worktree_dll = (
        _WORKTREE
        / "Algorithm.CSharp"
        / "bin"
        / "Debug"
        / "QuantConnect.Algorithm.CSharp.dll"
    )
    base["algorithm-location"] = str(worktree_dll.resolve())
    return base


def _order_events_packet(run_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Load the ``<run_id>-order-events.json`` sidecar written by LEAN."""
    path = run_dir / f"{run_id}-order-events.json"
    if not path.is_file():
        # LEAN may not write the sidecar in all configs; fall back to the
        # orders dict embedded in the main packet.
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    loaded = json.loads(raw)
    if isinstance(loaded, list):
        return loaded
    return []


def _filled_orders_from_packet(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract filled orders from the LEAN result packet's ``orders`` dict."""
    orders = packet.get("orders")
    if not isinstance(orders, dict):
        return []
    out = []
    for _oid, order in orders.items():
        if not isinstance(order, dict):
            continue
        if order.get("status") != _ORDER_STATUS_FILLED:
            continue
        out.append(order)
    return out


def _filled_events_from_order_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter the order-events sidecar down to filled events."""
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        status = ev.get("status")
        if status == "filled" or status == _ORDER_STATUS_FILLED:
            out.append(ev)
    return out


def _normalize_packet_fills(
    packet: dict[str, Any],
    order_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize LEAN-native packet fills to the economic-event shape.

    Uses the order-events sidecar (which has fillPrice/fillQuantity/fee/
    direction) when available; otherwise synthesizes from the orders dict
    (which lacks per-fill price/fee detail).
    """
    filled_events = _filled_events_from_order_events(order_events)
    if filled_events:
        return [
            {
                "event_type": "FILL",
                "time": _epoch_to_iso(ev.get("time")),
                "payload": {
                    "orderId": ev.get("orderId"),
                    "symbol": ev.get("symbolValue"),
                    "fillPrice": str(ev.get("fillPrice", 0)),
                    "fillQuantity": str(ev.get("fillQuantity", 0)),
                    "fee": str(ev.get("orderFeeAmount", 0)),
                    "feeCurrency": ev.get("orderFeeCurrency", "CNY"),
                    "status": "Filled",
                    "direction": _DIRECTION_NAMES.get(
                        ev.get("direction"), str(ev.get("direction"))
                    ),
                },
            }
            for ev in filled_events
        ]

    # Fallback: orders dict (no per-fill price/fee). This is a weaker check
    # but still catches a multiplicity/order divergence.
    orders = _filled_orders_from_packet(packet)
    return [
        {
            "event_type": "FILL",
            "time": order.get("lastFillTime") or order.get("time"),
            "payload": {
                "orderId": order.get("id"),
                "symbol": order.get("symbol", {}).get("value"),
                "fillPrice": str(order.get("price", 0)),
                "fillQuantity": str(order.get("quantity", 0)),
                "fee": "0",
                "feeCurrency": order.get("priceCurrency", "CNY"),
                "status": "Filled",
                "direction": _DIRECTION_NAMES.get(order.get("direction"), "Unknown"),
            },
        }
        for order in orders
    ]


def _epoch_to_iso(value: Any) -> str | None:
    """Convert a LEAN epoch-seconds timestamp (float) to ISO 8601 UTC.

    LEAN writes order-event ``time`` as fractional epoch seconds in the
    local timezone (Asia/Shanghai for A-share runs). For equivalence we
    only need the timestamps to match across the two runs; both runs use
    the same timezone so the epoch values are directly comparable. We
    format to ISO for readability but the comparison is value-based.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return str(value)


def _normalize_trace_fills(trace_events: list) -> list[dict[str, Any]]:
    """Normalize formal-trace FILL events to the economic-event shape."""
    out = []
    for ev in trace_events:
        if ev.event_type != "FILL":
            continue
        p = ev.payload or {}
        out.append(
            {
                "event_type": "FILL",
                "time": ev.event_time_utc,
                "payload": {
                    "orderId": p.get("orderId"),
                    "symbol": p.get("symbol"),
                    "fillPrice": str(p.get("fillPrice", 0)),
                    "fillQuantity": str(p.get("fillQuantity", 0)),
                    "fee": str(p.get("fee", 0)),
                    "feeCurrency": p.get("feeCurrency", "CNY"),
                    "status": p.get("status", "Filled"),
                    "direction": p.get("direction", "Unknown"),
                },
            }
        )
    return out


@pytest.mark.skipif(
    os.environ.get(_INTEGRATION_ENV) != "1",
    reason=f"set {_INTEGRATION_ENV}=1 to run the real LEAN STOP P1 gate",
)
def test_gold2_proof_stop_p1_gate(tmp_path):
    """STOP P1: mature vs proof produce zero economic mismatch on W1-train slice.

    Runs both strategies on 2018-01-02..2018-03-31 (518880 data), loads the
    LEAN-native result packets, normalizes fills to the economic-event
    shape, and asserts equivalence. Also validates the proof trace and
    checks proof-trace fills match the proof packet fills (self-consistency).
    """
    _skip_if_dll_missing()
    _skip_if_data_missing()

    base = _load_base_config()

    # Shared tunables: use the proof base config's parameters so both runs
    # share identical strategy inputs.
    shared_params = dict(base.get("parameters", {}))
    # Force the short slice for both runs.
    shared_params["start-date"] = _SLICE_START
    shared_params["end-date"] = _SLICE_END

    run_dir_mature = tmp_path / "mature"
    run_dir_proof = tmp_path / "proof"
    trace_path = tmp_path / "proof" / "trace.jsonl"

    # Mature run: NO trace.
    mature_config = build_run_config(
        base,
        run_dir_mature,
        _MATURE_STRATEGY,
        shared_params,
        run_id="mature-g0",
        start_date=_SLICE_START,
        end_date=_SLICE_END,
    )

    # Proof run: WITH trace.
    proof_config = build_run_config(
        base,
        run_dir_proof,
        _PROOF_STRATEGY,
        shared_params,
        run_id="proof-g0",
        start_date=_SLICE_START,
        end_date=_SLICE_END,
        trace_path=trace_path,
        experiment_id="E1",
        window_id="W1",
        stage_id="G0",
        candidate_id="C1",
    )

    mature_result = run_lean(
        mature_config,
        run_dir=run_dir_mature,
        run_id="mature-g0",
        timeout_seconds=600,
        worktree_root=_WORKTREE,
    )
    assert mature_result.is_success(), (
        f"mature run failed: {mature_result.status}\n{mature_result.error}"
    )

    proof_result = run_lean(
        proof_config,
        run_dir=run_dir_proof,
        run_id="proof-g0",
        timeout_seconds=600,
        worktree_root=_WORKTREE,
        trace_path=trace_path,
    )
    assert proof_result.is_success(), (
        f"proof run failed: {proof_result.status}\n{proof_result.error}"
    )

    # Load packets + order-events sidecars.
    mature_packet = load_exact_packet(run_dir_mature, "mature-g0")
    proof_packet = load_exact_packet(run_dir_proof, "proof-g0")
    mature_events = _order_events_packet(run_dir_mature, "mature-g0")
    proof_events = _order_events_packet(run_dir_proof, "proof-g0")

    # --- Behavior-preservation: mature fills == proof fills --------------
    mature_fills = _normalize_packet_fills(mature_packet, mature_events)
    proof_fills = _normalize_packet_fills(proof_packet, proof_events)
    result = compare_economic_events(mature_fills, proof_fills, 1e-12, 1e-12)
    assert result.equivalent, (
        "BEHAVIOR-PRESERVATION FAILED: mature vs proof fills mismatch:\n"
        + "\n".join(result.mismatches)
    )

    # --- Proof trace validates (reconciliation passes) -------------------
    trace_events = load_trace(trace_path)
    # validate_formal_events accepts FormalEvent objects directly.
    validate_formal_events(trace_events)

    # --- Self-consistency: proof trace fills == proof packet fills ------
    trace_fills = _normalize_trace_fills(trace_events)
    sc = compare_economic_events(trace_fills, proof_fills, 1e-12, 1e-12)
    assert sc.equivalent, (
        "SELF-CONSISTENCY FAILED: proof trace FILL events do not match the "
        "proof packet's fills:\n" + "\n".join(sc.mismatches)
    )
