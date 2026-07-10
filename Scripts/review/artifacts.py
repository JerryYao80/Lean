"""Load LEAN backtest artifacts + build TradeContext (no-lookahead). Spec §1.4, §3.4.

Reads {algo}.json (closedTrades, charts, orders), {algo}-order-events.json,
optional alpha-results.json, optional state_trace*.jsonl.
"""
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adapters.base import TradeRecord, TradeContext

_TRADE_DIRECTION = {0: "long", 1: "short"}


def load_closed_trades(algo_json_path) -> list:
    """Load totalPerformance.closedTrades → list[TradeRecord]. Spec §2.3.

    closedTrade keys (verified 599 trades, gold2): id, symbols, entryTime,
    entryPrice, direction (0=Long/1=Short), quantity (unsigned), exitTime,
    exitPrice, profitLoss (STRING, gross, carries direction sign), totalFees,
    mae, mfe, duration ("d.hh:mm:ss"), endTradeDrawdown, isWin, orderIds.
    """
    data = json.loads(Path(algo_json_path).read_text())
    out = []
    for ct in data.get("totalPerformance", {}).get("closedTrades", []):
        symbols = ct.get("symbols", [])
        sym = symbols[0]["value"] if symbols else ""
        out.append(TradeRecord(
            symbol=sym,
            entry_time=ct["entryTime"],
            entry_price=Decimal(str(ct["entryPrice"])),
            exit_time=ct["exitTime"],
            exit_price=Decimal(str(ct["exitPrice"])),
            quantity=Decimal(str(ct["quantity"])),
            side=_TRADE_DIRECTION.get(int(ct.get("direction", 0)), "long"),
            profit_loss=Decimal(str(ct["profitLoss"])),  # STRING → Decimal
            fees=Decimal(str(ct["totalFees"])),
            tpv_entry=Decimal("0"),  # filled by caller from state_trace tpv or equity curve
            order_ids=list(ct.get("orderIds", [])),
        ))
    return out


def load_state_trace(path) -> list:
    """Load state_trace*.jsonl → chronological list of dicts. None if path missing."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _parse_ts(s: str):
    """Parse ISO-8601 (with or without timezone). Trims to seconds for compare."""
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    return datetime.fromisoformat(s[:19])


def build_trade_context(entry_time: str, exit_time: str, symbol: str,
                        state_trace, alpha_insights) -> TradeContext:
    """Build TradeContext with no-lookahead (spec §3.4 C3):
    - entry_bar = latest state_trace row with ts <= entry_time (signal acted on)
    - realized_bar = first row with ts > entry_time AND ts <= exit_time (post-fill weight)
    """
    if not state_trace:
        return TradeContext(entry_bar=None, realized_bar=None)

    entry_dt = _parse_ts(entry_time)
    exit_dt = _parse_ts(exit_time)

    entry_bar = None
    realized_bar = None
    for row in state_trace:
        try:
            ts = _parse_ts(row.get("ts", ""))
        except (ValueError, TypeError):
            continue
        if ts <= entry_dt:
            entry_bar = row  # keep the latest ts <= entry (signal acted on)
        if ts > entry_dt and ts <= exit_dt and realized_bar is None:
            realized_bar = row  # first post-fill row (post-fill weight)
            break
    return TradeContext(entry_bar=entry_bar, realized_bar=realized_bar)
