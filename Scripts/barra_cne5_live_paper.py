#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-barra-cne5-live-paper.json"


def launcher_binary_path() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher"


def default_bridge_python_executable() -> str:
    preferred = Path("/root/miniconda3/envs/quant311/bin/python")
    if preferred.exists():
        return str(preferred)
    return sys.executable


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def load_live_paper_runtime_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    loaded = json.loads(config_file.read_text(encoding="utf-8"))
    parameters = loaded.get("parameters") if isinstance(loaded, dict) else None
    if not isinstance(parameters, dict):
        parameters = {}

    launcher_workdir = repo_root() / "Launcher" / "bin" / "Debug"
    data_base = launcher_workdir
    results_base = launcher_workdir

    data_folder = loaded.get("data-folder") if isinstance(loaded, dict) else None
    if data_folder:
        path = Path(str(data_folder))
        data_base = path if path.is_absolute() else (launcher_workdir / path).resolve()

    results_folder = loaded.get("results-destination-folder") if isinstance(loaded, dict) else None
    if results_folder:
        path = Path(str(results_folder))
        results_base = path if path.is_absolute() else (launcher_workdir / path).resolve()

    def resolve_path(value, default: Path, base: Path) -> Path:
        path = Path(str(value)) if value else default
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    root = repo_root()
    return {
        "factor-data-path": resolve_path(parameters.get("factor-data-path"), root / "Data" / "alternative" / "barra-cne5-live-factors", data_base),
        "external-factor-path": resolve_path(parameters.get("external-factor-path"), root / "Data" / "alternative" / "barra-cne5-factors", data_base),
        "live-factor-report-file": resolve_path(parameters.get("live-factor-report-file"), root / "Results" / "barra-cne5-live-bridge-report.json", results_base),
        "live-price-snapshot-file": resolve_path(parameters.get("live-price-snapshot-file"), root / "Results" / "barra-cne5-live-price-snapshot.json", results_base),
        "portfolio-state-file": resolve_path(parameters.get("portfolio-state-file"), root / "Results" / "barra-cne5-live-state.json", results_base),
        "daily-quote-archive-path": resolve_path(parameters.get("daily-quote-archive-path"), root / "Data" / "archive" / "barra-cne5-live-daily-quotes", data_base),
        "daily-summary-file": resolve_path(parameters.get("daily-summary-file"), root / "Results" / "barra-cne5-live-daily-summary.csv", results_base),
        "allocation-report-file": resolve_path(parameters.get("allocation-report-file"), root / "Results" / "barra-cne5-live-allocation.csv", results_base),
        "factor-exposure-file": resolve_path(parameters.get("factor-exposure-file"), root / "Results" / "barra-cne5-live-factor-exposure.csv", results_base),
        "trade-report-file": resolve_path(parameters.get("trade-report-file"), root / "Results" / "barra-cne5-live-trades.csv", results_base),
        "factor-source-mode": str(parameters.get("factor-source-mode") or "auto"),
        "random-factor-seed": str(parameters.get("random-factor-seed") or "42"),
        "universe": str(parameters.get("symbols") or parameters.get("universe") or "csi300"),
        "bridge-ready-timeout-seconds": max(120, safe_int(parameters.get("bridge-ready-timeout-seconds"), 900)),
        "live-price-max-requests-per-minute": str(parameters.get("live-price-max-requests-per-minute") or "40"),
        "live-price-poll-interval-seconds": str(
            parameters.get("live-price-poll-interval-seconds")
            or parameters.get("live-factor-poll-interval-seconds")
            or "60"
        ),
        "live-price-refresh-interval-seconds": str(parameters.get("live-price-refresh-interval-seconds") or "60"),
        "initial-cash": str(parameters.get("initial-cash") or "100000"),
        "rebalance-frequency": str(parameters.get("rebalance-frequency") or "monthly"),
        "top-n": str(parameters.get("top-n") or "30"),
        "target-portfolio-exposure": str(parameters.get("target-portfolio-exposure") or "0.95"),
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data"),
    }


def format_timestamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def count_factor_files(factor_data_path: Path) -> int:
    if not factor_data_path.exists():
        return 0
    return sum(1 for _ in factor_data_path.glob("*/*/*.csv"))


def load_bridge_report(report_path: Path) -> dict | None:
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def read_latest_csv_row(path: Path, modified_after: float | None = None) -> dict[str, str] | None:
    if not path.exists():
        return None
    if modified_after is not None and path.stat().st_mtime + 1e-6 < float(modified_after):
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None
    if not rows:
        return None
    return {
        str(key): ("" if value is None else str(value))
        for key, value in rows[-1].items()
    }


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
    if numeric != numeric:
        return None
    return numeric


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


def load_snapshot_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_snapshot_quote_lookup(path: Path) -> tuple[dict, dict[str, dict]]:
    payload = load_snapshot_payload(path)
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return payload, {}

    lookup: dict[str, dict] = {}
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        ts_code = str(quote.get("ts_code") or "").strip()
        if not ts_code:
            continue
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
    quotes = snapshot_payload.get("quotes")
    if not isinstance(quotes, list):
        return "-"
    candidates = []
    for quote in quotes:
        if isinstance(quote, dict) and quote.get("fetch_timestamp"):
            candidates.append(str(quote["fetch_timestamp"]))
    return max(candidates) if candidates else "-"


def build_account_summary(
    runtime_config: dict,
    daily_row: dict[str, str] | None,
    snapshot_payload: dict,
    bridge_report: dict | None = None,
    live_quote_count: int | None = None,
) -> str | None:
    if not daily_row:
        return None

    initial_cash = safe_float(runtime_config.get("initial-cash"))
    equity = safe_float(daily_row.get("equity"))
    cash = safe_float(daily_row.get("cash"))
    invested = safe_float(daily_row.get("invested"))
    turnover = safe_float(daily_row.get("turnover"))
    live_quote_report = bridge_report.get("live_quote_report") if isinstance(bridge_report, dict) else {}
    if not isinstance(live_quote_report, dict):
        live_quote_report = {}
    pnl_amount = (equity - initial_cash) if equity is not None and initial_cash is not None else None
    pnl_ratio = ((equity / initial_cash) - 1.0) if equity is not None and initial_cash not in (None, 0.0) else None

    rows = [
        ["Initial Cash", format_currency(initial_cash)],
        ["Current Equity", format_currency(equity)],
        ["PnL", format_currency(pnl_amount)],
        ["PnL%", format_fraction_percent(pnl_ratio)],
        ["Invested", format_currency(invested)],
        ["Cash", format_currency(cash)],
        ["Holdings", daily_row.get("holdings", "-")],
        ["Eligible", daily_row.get("eligible_symbols", "-")],
        ["Selected", daily_row.get("selected_symbols", "-")],
        ["Turnover", format_fraction_percent(turnover)],
        ["Live Quotes", str(live_quote_count if live_quote_count is not None else snapshot_payload.get("quote_count", "-"))],
        ["Refreshed", str(live_quote_report.get("refreshed_quote_count", "-"))],
        ["Carry Forward", str(live_quote_report.get("carried_forward_quote_count", "-"))],
        ["Full Refresh", f"{live_quote_report.get('estimated_full_refresh_minutes', '-')} min"],
        ["Quote Time", latest_snapshot_fetch_time(snapshot_payload)],
    ]
    table = render_ascii_table([("Metric", "left"), ("Value", "right")], rows)
    return f"[account summary] trade_date={daily_row.get('trade_date', '-')}\n{table}"


def build_allocation_preview(
    path: Path,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = None,
    modified_after: float | None = None,
) -> str | None:
    rows = read_csv_rows(path, modified_after=modified_after)
    if not rows:
        return None
    trade_dates = [row.get("trade_date", "") for row in rows if row.get("trade_date")]
    if not trade_dates:
        return None
    latest_trade_date = max(trade_dates)
    latest_rows = [row for row in rows if row.get("trade_date") == latest_trade_date]
    latest_rows.sort(
        key=lambda row: (safe_float(row.get("quantity")) or 0.0) * (safe_float(row.get("price")) or 0.0),
        reverse=True,
    )
    preview = latest_rows if limit is None else latest_rows[:limit]
    table_rows = [
        [
            row.get("symbol", "-"),
            format_integer(row.get("quantity")),
            format_price(row.get("price")),
            format_direct_percent(resolve_quote_pct_chg((snapshot_quotes or {}).get(row.get("symbol", "")))),
            format_currency((safe_float(row.get("quantity")) or 0.0) * (safe_float(row.get("price")) or 0.0)),
            format_weight_percent(row.get("weight")),
            format_decimal(row.get("score")),
        ]
        for row in preview
    ]
    if not table_rows:
        return None
    table = render_ascii_table(
        [
            ("Symbol", "left"),
            ("Qty", "right"),
            ("Price", "right"),
            ("PctChg", "right"),
            ("Value", "right"),
            ("Weight", "right"),
            ("Score", "right"),
        ],
        table_rows,
    )
    return f"[portfolio allocation] trade_date={latest_trade_date} holdings={len(table_rows)}\n{table}"


def build_exposure_preview(path: Path, modified_after: float | None = None) -> str | None:
    row = read_latest_csv_row(path, modified_after=modified_after)
    if not row:
        return None
    factors = [
        ("beta", row.get("beta")),
        ("momentum", row.get("momentum")),
        ("size", row.get("size")),
        ("earnyld", row.get("earnyld")),
        ("resvol", row.get("resvol")),
        ("growth", row.get("growth")),
        ("btop", row.get("btop")),
        ("leverage", row.get("leverage")),
        ("liquidity", row.get("liquidity")),
        ("nlsize", row.get("nlsize")),
    ]
    table = render_ascii_table(
        [("Factor", "left"), ("Exposure", "right")],
        [[name, format_decimal(value, digits=3)] for name, value in factors],
    )
    return f"[exposure] trade_date={row.get('trade_date', '-')}\n{table}"


def build_signal_preview(
    path: Path,
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = None,
    modified_after: float | None = None,
) -> str | None:
    rows = read_csv_rows(path, modified_after=modified_after)
    if not rows:
        return None
    trade_dates = [row.get("trade_date", "") for row in rows if row.get("trade_date")]
    if not trade_dates:
        return None
    latest_trade_date = max(trade_dates)
    latest_rows = [row for row in rows if row.get("trade_date") == latest_trade_date]
    latest_rows.sort(
        key=lambda row: (row.get("executed_at", ""), row.get("symbol", ""), row.get("action", "")),
    )
    preview = latest_rows if limit is None else latest_rows[-limit:]
    table_rows = []
    for row in preview:
        action = row.get("action", "-")
        symbol = row.get("symbol", "-")
        quantity = format_integer(row.get("quantity"))
        before_quantity = format_integer(row.get("quantity_before"))
        after_quantity = format_integer(row.get("quantity_after"))
        price_text = format_price(row.get("price"))
        pct_chg_text = format_direct_percent(resolve_quote_pct_chg((snapshot_quotes or {}).get(symbol)))
        score_text = format_decimal(row.get("score"))
        trade_value = safe_float(row.get("trade_value"))
        fee = safe_float(row.get("fee"))
        table_rows.append([
            row.get("executed_at", "-"),
            action,
            symbol,
            quantity,
            before_quantity,
            after_quantity,
            price_text,
            pct_chg_text,
            "-" if trade_value is None else f"{trade_value:.2f}",
            "-" if fee is None else f"{fee:.2f}",
            score_text,
        ])
    if not table_rows:
        return None
    table = render_ascii_table(
        [
            ("Time", "left"),
            ("Action", "left"),
            ("Symbol", "left"),
            ("Qty", "right"),
            ("Before", "right"),
            ("After", "right"),
            ("Price", "right"),
            ("PctChg", "right"),
            ("Value", "right"),
            ("Fee", "right"),
            ("Score", "right"),
        ],
        table_rows,
    )
    return f"[trade signals] trade_date={latest_trade_date} rows={len(table_rows)}\n{table}"


def build_market_preview_line(bridge_report: dict | None) -> str | None:
    if not bridge_report:
        return None
    market_preview = bridge_report.get("market_preview")
    if not isinstance(market_preview, dict):
        return None
    live_quote_report = bridge_report.get("live_quote_report")
    if not isinstance(live_quote_report, dict):
        live_quote_report = {}
    rows = list(market_preview.get("rows") or [])
    if not rows:
        return None
    preview_rows = rows[:4]
    table = render_ascii_table(
        [
            ("Symbol", "left"),
            ("Close", "right"),
            ("PctChg", "right"),
            ("Source", "left"),
        ],
        [
            [
                row.get("symbol", "-"),
                format_price(row.get("close")),
                "-" if safe_float(row.get("pct_chg")) is None else f"{safe_float(row.get('pct_chg')):+.2f}%",
                row.get("source_api", "-"),
            ]
            for row in preview_rows
        ],
    )
    return (
        f"[market preview] trade_date={market_preview.get('trade_date', '-')} "
        f"requested={live_quote_report.get('requested_symbol_count', market_preview.get('universe_size', 0))} "
        f"received={live_quote_report.get('received_quote_count', market_preview.get('received_count', 0))} "
        f"fetched_at={live_quote_report.get('generated_at', '-')} "
        f"minute_cap={live_quote_report.get('max_requests_per_minute', '-')}\n"
        f"{table}"
    )


def print_live_plan(config_path: Path, runtime_config: dict) -> None:
    print("=" * 100)
    print("Barra CNE5 Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    print("Market data         : Tushare realtime daily bars via TushareDataQueue", flush=True)
    print("Order model         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source       : {runtime_config['factor-source-mode']} (seed={runtime_config['random-factor-seed']})", flush=True)
    print(f"Universe            : {runtime_config['universe']}", flush=True)
    print(f"Bridge ready timeout: {runtime_config['bridge-ready-timeout-seconds']} seconds", flush=True)
    print(f"Minute API cap      : {runtime_config['live-price-max-requests-per-minute']} requests/minute", flush=True)
    print(f"rt_k poll interval  : {runtime_config['live-price-poll-interval-seconds']} seconds", flush=True)
    print(f"LEAN queue refresh  : {runtime_config['live-price-refresh-interval-seconds']} seconds", flush=True)
    print(f"Initial cash        : {runtime_config['initial-cash']}", flush=True)
    print(f"Rebalance           : {runtime_config['rebalance-frequency']} topN={runtime_config['top-n']} exposure={runtime_config['target-portfolio-exposure']}", flush=True)
    print(f"Tushare data path   : {runtime_config['tushare-data-path']}", flush=True)
    print(f"Factor data path    : {runtime_config['factor-data-path']}", flush=True)
    print(f"Historical factors  : {runtime_config['external-factor-path']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-factor-report-file']}", flush=True)
    print(f"Price snapshot      : {runtime_config['live-price-snapshot-file']}", flush=True)
    print(f"Portfolio state     : {runtime_config['portfolio-state-file']}", flush=True)
    print(f"Quote archive       : {runtime_config['daily-quote-archive-path']}", flush=True)
    print(f"Daily summary       : {runtime_config['daily-summary-file']}", flush=True)
    print(f"Allocation report   : {runtime_config['allocation-report-file']}", flush=True)
    print(f"Exposure report     : {runtime_config['factor-exposure-file']}", flush=True)
    print(f"Trade report        : {runtime_config['trade-report-file']}", flush=True)
    print("Process flow        : 1) bridge seeds factors 2) bridge rotates rt_k/rt_etf_k refresh each minute and carries forward the remaining snapshot 3) LEAN consumes the snapshot 4) strategy restores portfolio state, computes signals, and updates cash/holdings", flush=True)
    print("=" * 100)


def print_bridge_wait_status(runtime_config: dict) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    report_path = Path(runtime_config["live-factor-report-file"])
    snapshot_path = Path(runtime_config["live-price-snapshot-file"])
    bridge_report = load_bridge_report(report_path)
    trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0
    resolved_symbols = bridge_report.get("bridge_report", {}).get("resolved_symbol_count") if bridge_report else 0
    requested_quotes = bridge_report.get("live_quote_report", {}).get("requested_symbol_count") if bridge_report else 0
    live_quotes = bridge_report.get("live_quote_report", {}).get("received_quote_count") if bridge_report else 0
    print(
        f"[stage bridge-wait] {format_timestamp()} trade_date={trade_date} mode={mode} "
        f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols}/{resolved_symbols} "
        f"live_quotes={live_quotes}/{requested_quotes} snapshot_exists={int(snapshot_path.exists())}",
        flush=True,
    )


def print_runtime_status(runtime_config: dict, output_fresh_since: float | None = None) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    bridge_report = load_bridge_report(Path(runtime_config["live-factor-report-file"]))
    daily_row = read_latest_csv_row(Path(runtime_config["daily-summary-file"]), modified_after=output_fresh_since)
    snapshot_payload, snapshot_quotes = build_snapshot_quote_lookup(Path(runtime_config["live-price-snapshot-file"]))

    bridge_trade_date = bridge_report.get("trade_date") if bridge_report else "-"
    bridge_mode = bridge_report.get("bridge_report", {}).get("mode") if bridge_report else "-"
    written_symbols = bridge_report.get("bridge_report", {}).get("written_symbol_count") if bridge_report else 0
    live_quote_report = bridge_report.get("live_quote_report", {}) if bridge_report else {}
    received_quotes = live_quote_report.get("received_quote_count") if isinstance(live_quote_report, dict) else 0
    refreshed_quotes = live_quote_report.get("refreshed_quote_count") if isinstance(live_quote_report, dict) else 0
    carried_forward_quotes = live_quote_report.get("carried_forward_quote_count") if isinstance(live_quote_report, dict) else 0
    full_refresh_minutes = live_quote_report.get("estimated_full_refresh_minutes") if isinstance(live_quote_report, dict) else "-"
    initial_cash = safe_float(runtime_config.get("initial-cash"))

    if daily_row:
        equity = safe_float(daily_row.get("equity"))
        pnl_ratio = ((equity / initial_cash) - 1.0) if equity is not None and initial_cash not in (None, 0.0) else None
        print(
            f"[live status] {format_timestamp()} trade_date={daily_row.get('trade_date', '-')} "
            f"equity={format_currency(equity)} pnl={format_fraction_percent(pnl_ratio)} cash={format_currency(daily_row.get('cash'))} "
            f"holdings={daily_row.get('holdings', '-')} eligible={daily_row.get('eligible_symbols', '-')} "
            f"selected={daily_row.get('selected_symbols', '-')} turnover={format_fraction_percent(daily_row.get('turnover'))} "
            f"live_quotes={received_quotes} refreshed={refreshed_quotes} carry_forward={carried_forward_quotes} "
            f"full_refresh≈{full_refresh_minutes}m "
            f"rebalanced={daily_row.get('rebalanced', '-')}",
            flush=True,
        )
    else:
        print(
            f"[live status] {format_timestamp()} no daily summary yet "
            f"bridge_trade_date={bridge_trade_date} mode={bridge_mode} "
            f"factor_files={count_factor_files(factor_path)} written_symbols={written_symbols} "
                f"live_quotes={received_quotes}",
            flush=True,
        )

    account_summary = build_account_summary(
        runtime_config,
        daily_row,
        snapshot_payload,
        bridge_report=bridge_report,
        live_quote_count=received_quotes,
    )
    if account_summary:
        print(account_summary, flush=True)

    signal_preview = build_signal_preview(
        Path(runtime_config["trade-report-file"]),
        snapshot_quotes=snapshot_quotes,
        modified_after=output_fresh_since,
    )
    if signal_preview:
        print(signal_preview, flush=True)

    allocation_preview = build_allocation_preview(
        Path(runtime_config["allocation-report-file"]),
        snapshot_quotes=snapshot_quotes,
        limit=None,
        modified_after=output_fresh_since,
    )
    if allocation_preview:
        print(allocation_preview, flush=True)

    exposure_preview = build_exposure_preview(
        Path(runtime_config["factor-exposure-file"]),
        modified_after=output_fresh_since,
    )
    if exposure_preview:
        print(exposure_preview, flush=True)


def normalize_lean_output_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return None
    if "STATISTICS::" in text:
        return f"[stats] {text.split('STATISTICS::', 1)[1].strip()}"
    if "TushareDataConverter.GetLatestData(): Using realtime snapshot" in text:
        return None
    if "TushareDataConverter.GetLatestData(): Realtime snapshot active but missing" in text:
        return None
    if "TushareDataConverter.GetLatestData(): Refreshed " in text:
        return None
    if "TushareDataQueue.Emit():" in text:
        return None
    if "TushareDataQueue.Subscribe():" in text:
        return None
    if "TushareDataQueue." in text or "TushareDataConverter." in text:
        payload = text.split("TRACE::", 1)[1].strip() if "TRACE::" in text else text
        return f"[data] {payload}"
    if "TRACE:: Log:" in text:
        return f"[algo] {text.split('TRACE:: Log:', 1)[1].strip()}"
    if "TRACE:: Debug:" in text:
        message = text.split("TRACE:: Debug:", 1)[1].strip()
        interesting_tokens = (
            "AShareBarraCNE5Algorithm initialized",
            "Execution mode:",
            "Synthetic execution enabled:",
            "[bootstrap]",
            "[factor sync]",
            "rebalance",
            "[signal",
            "[daily]",
            "[portfolio]",
            "Saved Barra outputs",
            "Runtime Error",
            "completed in",
        )
        return f"[algo] {message}" if any(token in message for token in interesting_tokens) else None
    if "ERROR::" in text or "Unhandled exception" in text or "RuntimeError" in text:
        return text
    return None


def normalize_process_output_line(label: str, line: str) -> str | None:
    text = line.rstrip("\n")
    if label == "lean":
        normalized = normalize_lean_output_line(text)
        return None if normalized is None else f"[lean] {normalized}"
    text = text.strip()
    if not text:
        return None
    return f"[{label}] {text}"


def stream_process_output(process: subprocess.Popen, label: str) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def consume_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            normalized = normalize_process_output_line(label, line)
            if normalized:
                print(normalized, flush=True)

    thread = threading.Thread(target=consume_output, name=f"{label}-output", daemon=True)
    thread.start()
    return thread


def start_logged_process(command: list[str], cwd: Path, label: str) -> tuple[subprocess.Popen, threading.Thread | None]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    print(f"[process start] name={label} pid={process.pid} cwd={cwd}", flush=True)
    print(f"[process cmd] {label}: {' '.join(command)}", flush=True)
    return process, stream_process_output(process, label)


def join_output_thread(thread: threading.Thread | None) -> None:
    if thread is None:
        return
    thread.join(timeout=5)


def wait_for_process_with_status(
    process: subprocess.Popen,
    runtime_config: dict,
    status_interval_seconds: float | None = None,
    output_fresh_since: float | None = None,
) -> int:
    interval_seconds = max(
        5.0,
        float(status_interval_seconds or runtime_config.get("live-price-poll-interval-seconds") or 60),
    )
    print_runtime_status(runtime_config, output_fresh_since=output_fresh_since)
    next_status = time.time() + interval_seconds
    while True:
        returncode = process.poll()
        if returncode is not None:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since)
            return returncode

        now = time.time()
        if now >= next_status:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since)
            next_status = now + interval_seconds

        time.sleep(1.0)


def wait_for_bridge_ready(
    config_path: str | Path | None,
    bridge_process: subprocess.Popen,
    bridge_started_at: float,
    timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 1.0,
) -> bool:
    runtime_config = load_live_paper_runtime_config(config_path)
    factor_data_path = Path(runtime_config["factor-data-path"])
    report_path = Path(runtime_config["live-factor-report-file"])
    snapshot_path = Path(runtime_config["live-price-snapshot-file"])
    min_quote_coverage = 0.10
    min_factor_coverage = 0.90
    deadline = time.time() + max(1.0, float(timeout_seconds))
    next_status = time.time()

    def is_ready(report: dict | None) -> bool:
        if not report_path.exists() or not snapshot_path.exists() or report is None:
            return False
        if report_path.stat().st_mtime + 1e-6 < bridge_started_at:
            return False
        if snapshot_path.stat().st_mtime + 1e-6 < bridge_started_at:
            return False

        live_quote_report = report.get("live_quote_report")
        bridge_payload = report.get("bridge_report")
        if not isinstance(live_quote_report, dict) or not isinstance(bridge_payload, dict):
            return False

        requested_quotes = int(live_quote_report.get("requested_symbol_count") or 0)
        received_quotes = int(live_quote_report.get("received_quote_count") or 0)
        resolved_symbols = int(bridge_payload.get("resolved_symbol_count") or 0)
        written_symbols = int(bridge_payload.get("written_symbol_count") or 0)

        quote_coverage = (received_quotes / requested_quotes) if requested_quotes > 0 else 0.0
        factor_coverage = (written_symbols / resolved_symbols) if resolved_symbols > 0 else 0.0
        if quote_coverage < min_quote_coverage:
            return False
        if factor_coverage < min_factor_coverage:
            return False
        return count_factor_files(factor_data_path) >= max(1, written_symbols)

    while time.time() < deadline:
        if bridge_process.poll() is not None:
            return False
        bridge_report = load_bridge_report(report_path)
        if is_ready(bridge_report):
            return True
        now = time.time()
        if now >= next_status:
            print_bridge_wait_status(runtime_config)
            next_status = now + max(5.0, float(poll_interval_seconds))
        time.sleep(max(0.1, float(poll_interval_seconds)))

    bridge_report = load_bridge_report(report_path)
    return is_ready(bridge_report)


def build_bridge_command(config_path: str | Path | None = None, python_executable: str | None = None) -> list[str]:
    executable = python_executable or default_bridge_python_executable()
    return [
        executable,
        str(repo_root() / "Scripts" / "barra_cne5_live_bridge.py"),
        "--config",
        str(resolve_live_config_path(config_path)),
    ]


def build_launcher_command(config_path: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = resolve_live_config_path(config_path)
    launcher = launcher_binary_path().resolve()
    command = [str(launcher), "--config", str(config_path)]
    return command, launcher.parent


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_live_paper(
    config_path: str | Path | None = None,
    start_bridge: bool = True,
    start_launcher: bool = True,
    python_executable: str | None = None,
) -> int:
    resolved_config_path = resolve_live_config_path(config_path)
    runtime_config = load_live_paper_runtime_config(resolved_config_path)
    print_live_plan(resolved_config_path, runtime_config)

    if not start_bridge and not start_launcher:
        return 0

    if start_bridge and not start_launcher:
        print("[stage 1/1] starting bridge only", flush=True)
        bridge_process, bridge_thread = start_logged_process(
            build_bridge_command(config_path, python_executable),
            repo_root(),
            "bridge",
        )
        try:
            return bridge_process.wait()
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping bridge", flush=True)
            return 130
        finally:
            terminate_process(bridge_process)
            join_output_thread(bridge_thread)

    if start_launcher and not start_bridge:
        launcher_command, workdir = build_launcher_command(config_path)
        print("[stage 1/1] starting launcher only", flush=True)
        launcher_started_at = time.time()
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")
        try:
            return wait_for_process_with_status(
                launcher_process,
                runtime_config,
                output_fresh_since=launcher_started_at,
            )
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher", flush=True)
            return 130
        finally:
            terminate_process(launcher_process)
            join_output_thread(launcher_thread)

    print("[stage 1/4] starting bridge process", flush=True)
    bridge_started_at = time.time()
    bridge_process, bridge_thread = start_logged_process(
        build_bridge_command(config_path, python_executable),
        repo_root(),
        "bridge",
    )
    launcher_process = None
    launcher_thread = None
    try:
        print("[stage 2/4] waiting for bridge to materialize live factor files", flush=True)
        if not wait_for_bridge_ready(
            config_path,
            bridge_process,
            bridge_started_at=bridge_started_at,
            timeout_seconds=float(runtime_config["bridge-ready-timeout-seconds"]),
        ):
            if bridge_process.poll() is not None:
                return bridge_process.returncode
            print(
                "Timed out waiting for bridge to materialize live factor files. "
                "Consider increasing `bridge-ready-timeout-seconds` if the realtime universe is large.",
                file=sys.stderr,
            )
            return 1

        launcher_command, workdir = build_launcher_command(config_path)
        print("[stage 3/4] bridge ready; starting LEAN live-paper engine", flush=True)
        launcher_started_at = time.time()
        launcher_process, launcher_thread = start_logged_process(launcher_command, workdir, "lean")
        try:
            print("[stage 4/4] entering live status loop", flush=True)
            return wait_for_process_with_status(
                launcher_process,
                runtime_config,
                output_fresh_since=launcher_started_at,
            )
        except KeyboardInterrupt:
            print("[live paper] interrupted by user; stopping launcher and bridge", flush=True)
            return 130
    finally:
        terminate_process(launcher_process)
        terminate_process(bridge_process)
        join_output_thread(launcher_thread)
        join_output_thread(bridge_thread)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch Barra CNE5 live bridge and LEAN live-paper engine")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    parser.add_argument("--python-executable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start_bridge = not args.launcher_only
    start_launcher = not args.bridge_only
    return run_live_paper(
        args.config,
        start_bridge=start_bridge,
        start_launcher=start_launcher,
        python_executable=args.python_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
