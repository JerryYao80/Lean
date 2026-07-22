"""Tests for artifact loading + TradeContext construction (no-lookahead). Spec §3.4, §6.1."""
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from artifacts import load_closed_trades, load_state_trace, build_trade_context  # noqa: E402


def _write_state_trace(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_state_trace_returns_chronological():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-07-01T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5},
            {"ts": "2020-07-03T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.6},
        ])
        trace = load_state_trace(p)
        assert len(trace) == 3
        assert trace[0]["dir_coef"] == 0.5


def test_build_context_reads_realized_from_first_row_after_entry():
    """Spec §3.4 (C3 P timing): realized weight from first ts>entry (post-fill),
    NOT the ts==entry row (pre-fill)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-07-01T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4, "w_smooth": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5, "w_smooth": 0.5},
            {"ts": "2020-07-03T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.6, "w_smooth": 0.6},
        ])
        trace = load_state_trace(p)
        ctx = build_trade_context(
            entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
            symbol="518880", state_trace=trace, alpha_insights=[],
        )
        assert ctx.entry_bar["dir_coef"] == 0.5   # ts<=entry row (signal acted on)
        assert ctx.realized_bar["w_after_vol"] == 0.5  # ts>entry row (post-fill)


def test_build_context_entry_bar_is_nearest_ts_le_entry():
    """entry_bar = nearest ts <= entry_time (the signal the strategy acted on)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state_trace.jsonl"
        _write_state_trace(p, [
            {"ts": "2020-06-30T00:00:00", "dir_coef": 0.5, "w_after_vol": 0.4},
            {"ts": "2020-07-02T00:00:00", "dir_coef": 1.0, "w_after_vol": 0.5},
        ])
        trace = load_state_trace(p)
        ctx = build_trade_context(
            entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
            symbol="518880", state_trace=trace, alpha_insights=[],
        )
        assert ctx.entry_bar["dir_coef"] == 0.5


def test_build_context_returns_none_entry_bar_when_no_state_trace():
    """No state_trace → entry_bar=None (adapter falls back to residual §3.5)."""
    ctx = build_trade_context(
        entry_time="2020-07-01T00:00:00", exit_time="2020-07-05T00:00:00",
        symbol="518880", state_trace=None, alpha_insights=[],
    )
    assert ctx.entry_bar is None


def test_load_closed_trades_maps_direction():
    """closedTrade.direction: 0=Long, 1=Short (LEAN TradeDirection enum)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "algo.json"
        p.write_text(json.dumps({
            "totalPerformance": {"closedTrades": [
                {"id": 1, "symbols": [{"value": "518880"}], "entryTime": "2020-07-01",
                 "entryPrice": "10.0", "direction": 0, "quantity": "100",
                 "exitTime": "2020-07-05", "exitPrice": "11.0",
                 "profitLoss": "100.0", "totalFees": "5.0",
                 "mae": "0.0", "mfe": "1.5", "duration": "4.00:00:00",
                 "endTradeDrawdown": "0.0", "isWin": True, "orderIds": [1]},
            ]}
        }))
        trades = load_closed_trades(p)
        assert len(trades) == 1
        assert trades[0].side == "long"
        assert trades[0].quantity == Decimal("100")
        assert trades[0].profit_loss == Decimal("100.0")
