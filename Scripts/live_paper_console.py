#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
from pathlib import Path


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


def format_weight_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric * 100.0:.2f}%"


def format_decimal(value, digits: int = 4) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric:.{digits}f}"


def format_price(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric:.2f}"


def format_currency(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"¥{numeric:,.2f}"


def format_fraction_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric * 100.0:+.2f}%"


def format_direct_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric:+.2f}%"


def format_integer(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{int(round(numeric)):,d}"


def render_ascii_table(columns: list[tuple[str, str]], rows: list[list[str]]) -> str:
    headers = [header for header, _ in columns]
    normalized_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in normalized_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(values: list[str]) -> str:
        aligned = []
        for index, value in enumerate(values):
            _, align = columns[index]
            width = widths[index]
            aligned.append(value.rjust(width) if align == "right" else value.ljust(width))
        return "| " + " | ".join(aligned) + " |"

    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [border, format_row(headers), border]
    lines.extend(format_row(row) for row in normalized_rows)
    lines.append(border)
    return "\n".join(lines)


def read_latest_csv_row(path: Path, modified_after: float | None = None) -> dict[str, str] | None:
    rows = read_csv_rows(path, modified_after=modified_after)
    return rows[-1] if rows else None


def read_csv_rows(path: Path, modified_after: float | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    if modified_after is not None and path.stat().st_mtime + 1e-6 < float(modified_after):
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []
    return [
        {str(key): ("" if value is None else str(value)) for key, value in row.items()}
        for row in rows
    ]


def load_json_payload(path: Path, modified_after: float | None = None):
    if not path.exists():
        return {}
    if modified_after is not None and path.stat().st_mtime + 1e-6 < float(modified_after):
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload


def build_snapshot_quote_lookup(path: Path, modified_after: float | None = None) -> tuple[dict, dict[str, dict]]:
    payload = load_json_payload(path, modified_after=modified_after)
    if not isinstance(payload, dict):
        return {}, {}

    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return payload, {}

    lookup: dict[str, dict] = {}
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        ts_code = str(quote.get("ts_code") or "").strip()
        if ts_code:
            lookup[ts_code] = quote
    return payload, lookup


def resolve_quote_pct_chg(quote: dict | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    pct_chg = safe_float(quote.get("pct_chg"))
    if pct_chg is not None:
        return pct_chg

    close = safe_float(quote.get("close"))
    if close is None:
        close = safe_float(quote.get("price"))
    pre_close = safe_float(quote.get("pre_close"))
    if close is None or pre_close in (None, 0.0):
        return None
    return (close / pre_close - 1.0) * 100.0


def latest_snapshot_fetch_time(snapshot_payload: dict) -> str:
    if not isinstance(snapshot_payload, dict):
        return "-"
    quotes = snapshot_payload.get("quotes")
    if not isinstance(quotes, list):
        return "-"
    candidates = []
    for quote in quotes:
        if isinstance(quote, dict) and quote.get("fetch_timestamp"):
            candidates.append(str(quote["fetch_timestamp"]))
    return max(candidates) if candidates else "-"


def should_print_section(display_state: dict | None, key: str, text: str | None) -> bool:
    if not text:
        return False
    if display_state is None:
        return True
    previous = display_state.get(key)
    if previous == text:
        return False
    display_state[key] = text
    return True


def _first_nonempty(row: dict | None, *keys: str):
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _trade_date_from_snapshot(snapshot_payload: dict | None) -> str | None:
    if not isinstance(snapshot_payload, dict):
        return None
    quotes = snapshot_payload.get("quotes")
    if not isinstance(quotes, list):
        return None
    for quote in quotes:
        if isinstance(quote, dict) and quote.get("trade_date"):
            return str(quote["trade_date"])
    return None


def _append_metric(rows: list[list[str]], label: str, value, formatter=None) -> None:
    if value in (None, ""):
        return
    display_value = formatter(value) if formatter else str(value)
    if display_value in (None, "", "-"):
        return
    rows.append([label, str(display_value)])


def build_account_summary_from_sources(
    *,
    initial_cash,
    daily_row: dict[str, str] | None = None,
    portfolio_snapshot: dict | None = None,
    snapshot_payload: dict | None = None,
    bridge_report: dict | None = None,
    live_quote_count: int | None = None,
    extra_rows: list[tuple[str, str]] | None = None,
) -> str | None:
    holdings = portfolio_snapshot.get("holdings") if isinstance(portfolio_snapshot, dict) else None
    if not isinstance(holdings, list):
        holdings = []

    equity = safe_float(_first_nonempty(daily_row, "equity"))
    if equity is None and isinstance(portfolio_snapshot, dict):
        equity = safe_float(_first_nonempty(portfolio_snapshot, "total_value", "current_equity", "equity"))

    cash = safe_float(_first_nonempty(daily_row, "cash"))
    if cash is None and isinstance(portfolio_snapshot, dict):
        cash = safe_float(_first_nonempty(portfolio_snapshot, "cash"))

    invested = safe_float(_first_nonempty(daily_row, "invested", "holdings_value"))
    if invested is None and isinstance(portfolio_snapshot, dict):
        invested = safe_float(_first_nonempty(portfolio_snapshot, "holdings_value", "invested"))

    position_count = _first_nonempty(daily_row, "position_count", "holdings")
    if position_count in (None, "") and holdings:
        position_count = str(len(holdings))

    trade_date = (
        _first_nonempty(daily_row, "trade_date")
        or _first_nonempty(portfolio_snapshot, "trade_date")
        or (bridge_report.get("trade_date") if isinstance(bridge_report, dict) else None)
        or _trade_date_from_snapshot(snapshot_payload)
        or "-"
    )

    live_quote_report = bridge_report.get("live_quote_report") if isinstance(bridge_report, dict) else {}
    if not isinstance(live_quote_report, dict):
        live_quote_report = {}

    base_cash = safe_float(initial_cash)
    pnl_amount = (equity - base_cash) if equity is not None and base_cash is not None else None
    pnl_ratio = ((equity / base_cash) - 1.0) if equity is not None and base_cash not in (None, 0.0) else None

    rows: list[list[str]] = []
    _append_metric(rows, "Initial Cash", base_cash, format_currency)
    _append_metric(rows, "Current Equity", equity, format_currency)
    _append_metric(rows, "PnL", pnl_amount, format_currency)
    _append_metric(rows, "PnL%", pnl_ratio, format_fraction_percent)
    _append_metric(rows, "Invested", invested, format_currency)
    _append_metric(rows, "Cash", cash, format_currency)
    _append_metric(rows, "Holdings", position_count)

    if extra_rows:
        for label, value in extra_rows:
            _append_metric(rows, label, value)

    quote_count = live_quote_count
    if quote_count is None and isinstance(snapshot_payload, dict):
        quotes = snapshot_payload.get("quotes")
        if isinstance(quotes, list):
            quote_count = len(quotes)

    _append_metric(rows, "Live Quotes", quote_count)
    _append_metric(rows, "Refreshed", live_quote_report.get("refreshed_quote_count"))
    _append_metric(rows, "Carry Forward", live_quote_report.get("carried_forward_quote_count"))
    _append_metric(rows, "Full Refresh", live_quote_report.get("estimated_full_refresh_minutes"), lambda value: f"{value} min")
    _append_metric(rows, "Quote Time", latest_snapshot_fetch_time(snapshot_payload or {}))

    if not rows:
        return None

    table = render_ascii_table([("Metric", "left"), ("Value", "right")], rows)
    return f"[account summary] trade_date={trade_date}\n{table}"


def _normalize_trade_row(row: dict[str, str], snapshot_quotes: dict[str, dict] | None = None) -> dict[str, object]:
    symbol = str(_first_nonempty(row, "symbol", "ts_code", "code") or "-")
    price = safe_float(_first_nonempty(row, "price", "fill_price", "market_price", "avg_price"))
    trade_value = safe_float(_first_nonempty(row, "trade_value", "fill_value", "value"))
    fee = safe_float(_first_nonempty(row, "fee", "commission"))
    status = _first_nonempty(row, "status")
    reason = _first_nonempty(row, "reason")
    score = safe_float(_first_nonempty(row, "score"))
    quote_pct = resolve_quote_pct_chg((snapshot_quotes or {}).get(symbol))
    return {
        "trade_date": str(_first_nonempty(row, "trade_date", "date") or ""),
        "time": str(_first_nonempty(row, "executed_at", "timestamp", "time") or "-"),
        "action": str(_first_nonempty(row, "action", "direction", "side") or "-"),
        "symbol": symbol,
        "quantity": _first_nonempty(row, "quantity", "qty", "fill_quantity"),
        "quantity_before": _first_nonempty(row, "quantity_before", "before_quantity", "before"),
        "quantity_after": _first_nonempty(row, "quantity_after", "after_quantity", "after"),
        "price": price,
        "pct_chg": quote_pct,
        "trade_value": trade_value,
        "fee": fee,
        "score": score,
        "reason": str(reason) if reason not in (None, "") else "",
        "status": str(status) if status not in (None, "") else "",
    }


def build_trade_executions_preview(
    rows: list[dict[str, str]],
    *,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = 12,
    total_rows: int | None = None,
) -> str | None:
    if not rows:
        return None

    ordered_rows = list(rows)
    preview_rows = ordered_rows if limit is None else ordered_rows[-limit:]
    normalized_rows = [_normalize_trade_row(row, snapshot_quotes=snapshot_quotes) for row in preview_rows]
    trade_dates = [str(row.get("trade_date") or "") for row in ordered_rows if row.get("trade_date")]
    latest_trade_date = max(trade_dates) if trade_dates else "-"

    has_before_after = any(row["quantity_before"] not in (None, "", "-") or row["quantity_after"] not in (None, "", "-") for row in normalized_rows)
    has_score = any(row["score"] is not None for row in normalized_rows)
    has_reason = any(row["reason"] for row in normalized_rows)
    has_status = any(row["status"] for row in normalized_rows)

    columns: list[tuple[str, str]] = [
        ("Time", "left"),
        ("Action", "left"),
        ("Symbol", "left"),
        ("Qty", "right"),
    ]
    if has_before_after:
        columns.extend([("Before", "right"), ("After", "right")])
    columns.extend([
        ("Price", "right"),
        ("PctChg", "right"),
        ("Value", "right"),
        ("Fee", "right"),
    ])
    if has_score:
        columns.append(("Score", "right"))
    if has_reason:
        columns.append(("Reason", "left"))
    if has_status:
        columns.append(("Status", "left"))

    table_rows: list[list[str]] = []
    for row in normalized_rows:
        cells = [
            str(row["time"]),
            str(row["action"]),
            str(row["symbol"]),
            format_integer(row["quantity"]),
        ]
        if has_before_after:
            cells.extend([
                format_integer(row["quantity_before"]),
                format_integer(row["quantity_after"]),
            ])
        cells.extend([
            format_price(row["price"]),
            format_direct_percent(row["pct_chg"]),
            "-" if row["trade_value"] is None else f"{row['trade_value']:.2f}",
            "-" if row["fee"] is None else f"{row['fee']:.2f}",
        ])
        if has_score:
            cells.append(format_decimal(row["score"]))
        if has_reason:
            cells.append(str(row["reason"] or "-"))
        if has_status:
            cells.append(str(row["status"] or "-"))
        table_rows.append(cells)

    table = render_ascii_table(columns, table_rows)
    total_text = f" total_rows={total_rows}" if total_rows is not None else ""
    return (
        f"[trade executions] trade_date={latest_trade_date} "
        f"rows={len(ordered_rows)} shown={len(table_rows)}{total_text}\n{table}"
    )


def build_no_new_trade_line(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "[trade executions] no trades yet"
    latest = _normalize_trade_row(rows[-1])
    return (
        "[trade executions] no new executions in this cycle "
        f"total_rows={len(rows)} last_execution={latest['time']} "
        f"last_symbol={latest['symbol']} last_action={latest['action']}"
    )


def _normalize_allocation_row(row: dict, total_market_value: float | None, snapshot_quotes: dict[str, dict] | None = None) -> dict[str, object]:
    symbol = str(_first_nonempty(row, "symbol", "ts_code", "code") or "-")
    quantity = safe_float(_first_nonempty(row, "quantity", "qty"))
    avg_cost = safe_float(_first_nonempty(row, "average_price", "avg_cost", "avg_price", "price"))
    market_price = safe_float(_first_nonempty(row, "market_price", "price", "close"))
    market_value = safe_float(_first_nonempty(row, "market_value", "holdings_value", "value"))
    if market_value is None and quantity is not None and market_price is not None:
        market_value = quantity * market_price
    weight = safe_float(_first_nonempty(row, "weight"))
    if weight is None and total_market_value not in (None, 0.0) and market_value is not None:
        weight = market_value / total_market_value
    unrealized_pnl = safe_float(_first_nonempty(row, "unrealized_pnl", "pnl", "profit"))
    unrealized_return = safe_float(_first_nonempty(row, "unrealized_return", "pnl_ratio", "pnl_pct"))
    if unrealized_return is not None and abs(unrealized_return) > 1.0:
        unrealized_return = unrealized_return / 100.0
    score = safe_float(_first_nonempty(row, "score"))
    return {
        "trade_date": str(_first_nonempty(row, "trade_date") or ""),
        "symbol": symbol,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "market_price": market_price,
        "pct_chg": resolve_quote_pct_chg((snapshot_quotes or {}).get(symbol)),
        "market_value": market_value,
        "weight": weight,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_return": unrealized_return,
        "score": score,
    }


def _build_portfolio_allocation_text(
    rows: list[dict],
    *,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = 20,
    trade_date: str | None = None,
) -> str | None:
    if not rows:
        return None

    total_market_value = 0.0
    for row in rows:
        market_value = safe_float(_first_nonempty(row, "market_value", "holdings_value", "value"))
        if market_value is not None:
            total_market_value += market_value

    normalized_rows = [
        _normalize_allocation_row(row, total_market_value if total_market_value > 0 else None, snapshot_quotes=snapshot_quotes)
        for row in rows
    ]
    normalized_rows.sort(key=lambda row: row["market_value"] or 0.0, reverse=True)
    preview_rows = normalized_rows if limit is None else normalized_rows[:limit]
    latest_trade_date = trade_date or max((str(row.get("trade_date") or "") for row in rows if row.get("trade_date")), default="-")

    has_weight = any(row["weight"] is not None for row in preview_rows)
    has_upnl = any(row["unrealized_pnl"] is not None for row in preview_rows)
    has_upnl_pct = any(row["unrealized_return"] is not None for row in preview_rows)
    has_score = any(row["score"] is not None for row in preview_rows)

    columns: list[tuple[str, str]] = [
        ("Symbol", "left"),
        ("Qty", "right"),
        ("AvgCost", "right"),
        ("MktPrice", "right"),
        ("PctChg", "right"),
        ("Value", "right"),
    ]
    if has_weight:
        columns.append(("Weight", "right"))
    if has_upnl:
        columns.append(("UPnL", "right"))
    if has_upnl_pct:
        columns.append(("UPnL%", "right"))
    if has_score:
        columns.append(("Score", "right"))

    table_rows: list[list[str]] = []
    for row in preview_rows:
        cells = [
            str(row["symbol"]),
            format_integer(row["quantity"]),
            format_price(row["avg_cost"]),
            format_price(row["market_price"]),
            format_direct_percent(row["pct_chg"]),
            format_currency(row["market_value"]),
        ]
        if has_weight:
            cells.append(format_weight_percent(row["weight"]))
        if has_upnl:
            cells.append(format_currency(row["unrealized_pnl"]))
        if has_upnl_pct:
            cells.append(format_fraction_percent(row["unrealized_return"]))
        if has_score:
            cells.append(format_decimal(row["score"]))
        table_rows.append(cells)

    table = render_ascii_table(columns, table_rows)
    return (
        f"[portfolio allocation] trade_date={latest_trade_date} "
        f"holdings={len(normalized_rows)} shown={len(table_rows)}\n{table}"
    )


def build_portfolio_allocation_from_csv(
    path: Path,
    *,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = 20,
    modified_after: float | None = None,
) -> str | None:
    rows = read_csv_rows(path, modified_after=modified_after)
    if not rows:
        return None
    trade_dates = [str(row.get("trade_date") or "") for row in rows if row.get("trade_date")]
    latest_trade_date = max(trade_dates) if trade_dates else "-"
    latest_rows = [row for row in rows if str(row.get("trade_date") or "") == latest_trade_date] if latest_trade_date != "-" else rows
    return _build_portfolio_allocation_text(
        latest_rows,
        snapshot_quotes=snapshot_quotes,
        limit=limit,
        trade_date=latest_trade_date,
    )


def build_portfolio_allocation_from_snapshot(
    portfolio_snapshot: dict,
    *,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = 20,
    trade_date: str | None = None,
) -> str | None:
    if not isinstance(portfolio_snapshot, dict):
        return None
    holdings = portfolio_snapshot.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return None
    return _build_portfolio_allocation_text(
        holdings,
        snapshot_quotes=snapshot_quotes,
        limit=limit,
        trade_date=trade_date,
    )
