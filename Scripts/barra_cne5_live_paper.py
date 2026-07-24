#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import math
import os
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

import live_paper_console as console
import live_paper_runner as runner


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


def normalize_console_output_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    return "custom" if mode == "custom" else "native"


def resolve_console_output_mode(runtime_config: dict | None = None, requested_mode: str | None = None) -> str:
    configured_mode = None
    if isinstance(runtime_config, dict):
        configured_mode = runtime_config.get("console-output-mode")
    return normalize_console_output_mode(requested_mode or configured_mode)


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
        "shared-live-market-snapshot-file": resolve_path(parameters.get("shared-live-market-snapshot-file"), root / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json", results_base),
        "shared-live-market-report-file": resolve_path(parameters.get("shared-live-market-report-file"), root / "Results" / "shared-live-market" / "ashare-live-market-report.json", results_base),
        "portfolio-state-file": resolve_path(parameters.get("portfolio-state-file"), root / "Results" / "barra-cne5-live-state.json", results_base),
        "daily-quote-archive-path": resolve_path(parameters.get("daily-quote-archive-path"), root / "Data" / "archive" / "barra-cne5-live-daily-quotes", data_base),
        "shared-live-market-archive-path": resolve_path(parameters.get("shared-live-market-archive-path"), root / "Data" / "archive" / "ashare-live-market-daily-quotes", data_base),
        "daily-summary-file": resolve_path(parameters.get("daily-summary-file"), root / "Results" / "barra-cne5-live-daily-summary.csv", results_base),
        "allocation-report-file": resolve_path(parameters.get("allocation-report-file"), root / "Results" / "barra-cne5-live-allocation.csv", results_base),
        "factor-exposure-file": resolve_path(parameters.get("factor-exposure-file"), root / "Results" / "barra-cne5-live-factor-exposure.csv", results_base),
        "trade-report-file": resolve_path(parameters.get("trade-report-file"), root / "Results" / "barra-cne5-live-trades.csv", results_base),
        "factor-source-mode": str(parameters.get("factor-source-mode") or "auto"),
        "random-factor-seed": str(parameters.get("random-factor-seed") or "42"),
        "universe": str(parameters.get("symbols") or parameters.get("universe") or "csi300"),
        "bridge-ready-timeout-seconds": max(120, safe_int(parameters.get("bridge-ready-timeout-seconds"), 900)),
        "live-price-max-requests-per-minute": str(parameters.get("live-price-max-requests-per-minute") or "50"),
        "live-price-source-mode": str(parameters.get("live-price-source-mode") or "auto"),
        "live-price-poll-interval-seconds": str(
            parameters.get("live-price-poll-interval-seconds")
            or parameters.get("live-factor-poll-interval-seconds")
            or "60"
        ),
        "live-price-refresh-interval-seconds": str(parameters.get("live-price-refresh-interval-seconds") or "60"),
        "shared-live-market-refresh-interval-seconds": str(parameters.get("shared-live-market-refresh-interval-seconds") or "60"),
        "simulated-live-price-random-seed": str(parameters.get("simulated-live-price-random-seed") or "20260317"),
        "simulated-live-price-lookback-days": str(parameters.get("simulated-live-price-lookback-days") or "60"),
        "simulated-live-price-volatility-scale": str(parameters.get("simulated-live-price-volatility-scale") or "8.0"),
        "simulated-live-price-min-daily-volatility": str(parameters.get("simulated-live-price-min-daily-volatility") or "0.80"),
        "simulated-live-price-jump-probability": str(parameters.get("simulated-live-price-jump-probability") or "0.22"),
        "simulated-live-price-jump-scale": str(parameters.get("simulated-live-price-jump-scale") or "0.10"),
        "live-signal-interval-minutes": str(parameters.get("live-signal-interval-minutes") or "3"),
        "bridge-ready-min-quote-coverage": str(parameters.get("bridge-ready-min-quote-coverage") or "1.0"),
        "bridge-ready-min-factor-coverage": str(parameters.get("bridge-ready-min-factor-coverage") or "0.95"),
        "initial-cash": str(parameters.get("initial-cash") or "100000"),
        "console-output-mode": normalize_console_output_mode(parameters.get("console-output-mode")),
        "rebalance-frequency": str(parameters.get("rebalance-frequency") or "monthly"),
        "top-n": str(parameters.get("top-n") or "30"),
        "target-portfolio-exposure": str(parameters.get("target-portfolio-exposure") or "0.95"),
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data_v2"),
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


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def build_live_session_root() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root() / "Results" / "live-paper" / "barra-cne5" / f"session-{timestamp}-{os.getpid()}"


def prepare_session_config(config_path: str | Path | None = None) -> tuple[Path, dict, Path]:
    source_config_path = resolve_live_config_path(config_path)
    runtime_config = load_live_paper_runtime_config(source_config_path)
    session_root = build_live_session_root()
    session_root.mkdir(parents=True, exist_ok=True)

    factor_root = session_root / "factor-data"
    quote_archive_root = session_root / "quote-archive"
    session_config_path = session_root / "config-barra-cne5-live-paper.session.json"

    payload = json.loads(source_config_path.read_text(encoding="utf-8"))
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
        payload["parameters"] = parameters

    parameters.update({
        "factor-data-path": str(factor_root),
        "live-factor-report-file": str(session_root / "barra-cne5-live-bridge-report.json"),
        "live-price-snapshot-file": str(session_root / "barra-cne5-live-price-snapshot.json"),
        "daily-quote-archive-path": str(quote_archive_root),
        "daily-summary-file": str(session_root / "barra-cne5-live-daily-summary.csv"),
        "allocation-report-file": str(session_root / "barra-cne5-live-allocation.csv"),
        "factor-exposure-file": str(session_root / "barra-cne5-live-factor-exposure.csv"),
        "trade-report-file": str(session_root / "barra-cne5-live-trades.csv"),
        "portfolio-state-file": str(runtime_config["portfolio-state-file"]),
    })

    session_config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session_config_path, load_live_paper_runtime_config(session_config_path), session_root


def acquire_live_paper_lock(lock_path: Path, session_root: Path) -> dict | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            existing_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            existing_payload = {}
        existing_pid = safe_int(existing_payload.get("pid"), 0)
        if existing_pid and existing_pid != os.getpid() and pid_is_alive(existing_pid):
            return {
                "pid": existing_pid,
                "session_root": str(existing_payload.get("session_root") or "-"),
                "started_at": str(existing_payload.get("started_at") or "-"),
            }

    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "session_root": str(session_root),
    }
    lock_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return None


def release_live_paper_lock(lock_path: Path) -> None:
    if not lock_path.exists():
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if safe_int(payload.get("pid"), 0) == os.getpid():
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


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


def build_factor_source_line(bridge_report: dict | None) -> str | None:
    if not isinstance(bridge_report, dict):
        return None
    payload = bridge_report.get("bridge_report")
    if not isinstance(payload, dict):
        return None

    parts = [
        f"trade_date={bridge_report.get('trade_date', '-')}",
        f"mode={payload.get('mode', '-')}",
    ]
    source_path = payload.get("external_factor_path") or payload.get("output_path")
    if source_path:
        parts.append(f"path={source_path}")
    if payload.get("resolved_symbol_count") is not None:
        parts.append(f"resolved={payload.get('resolved_symbol_count')}")
    if payload.get("written_symbol_count") is not None:
        parts.append(f"written={payload.get('written_symbol_count')}")
    if payload.get("exact_trade_date_symbol_count") is not None:
        parts.append(f"exact={payload.get('exact_trade_date_symbol_count')}")
    if payload.get("carry_forward_symbol_count") is not None:
        parts.append(f"carry_forward={payload.get('carry_forward_symbol_count')}")
    if payload.get("missing_symbol_count") is not None:
        parts.append(f"missing={payload.get('missing_symbol_count')}")
    return "[factor source] " + " ".join(str(part) for part in parts)


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
        key=lambda row: safe_float(row.get("market_value"))
        or (safe_float(row.get("quantity")) or 0.0) * (safe_float(row.get("market_price")) or safe_float(row.get("price")) or 0.0),
        reverse=True,
    )
    preview = latest_rows if limit is None else latest_rows[:limit]
    table_rows = [
        [
            row.get("symbol", "-"),
            format_integer(row.get("quantity")),
            format_price(row.get("price")),
            format_price(row.get("market_price") if row.get("market_price") not in (None, "") else row.get("price")),
            format_direct_percent(resolve_quote_pct_chg((snapshot_quotes or {}).get(row.get("symbol", "")))),
            format_currency(
                safe_float(row.get("market_value"))
                or (safe_float(row.get("quantity")) or 0.0)
                * (safe_float(row.get("market_price")) or safe_float(row.get("price")) or 0.0)
            ),
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
            ("AvgCost", "right"),
            ("MktPrice", "right"),
            ("PctChg", "right"),
            ("Value", "right"),
            ("Weight", "right"),
            ("Score", "right"),
        ],
        table_rows,
    )
    return (
        f"[portfolio allocation] trade_date={latest_trade_date} "
        f"holdings={len(latest_rows)} shown={len(table_rows)}\n{table}"
    )


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
    return (
        f"[trade signals] trade_date={latest_trade_date} "
        f"rows={len(latest_rows)} shown={len(table_rows)}\n{table}"
    )


def build_signal_preview_from_rows(
    rows: list[dict[str, str]],
    snapshot_quotes: dict[str, dict] | None = None,
    limit: int | None = None,
    total_rows: int | None = None,
) -> str | None:
    if not rows:
        return None
    ordered_rows = list(rows)
    preview = ordered_rows if limit is None else ordered_rows[-limit:]
    table_rows = []
    for row in preview:
        symbol = row.get("symbol", "-")
        trade_value = safe_float(row.get("trade_value"))
        fee = safe_float(row.get("fee"))
        table_rows.append([
            row.get("executed_at", "-"),
            row.get("action", "-"),
            symbol,
            format_integer(row.get("quantity")),
            format_integer(row.get("quantity_before")),
            format_integer(row.get("quantity_after")),
            format_price(row.get("price")),
            format_direct_percent(resolve_quote_pct_chg((snapshot_quotes or {}).get(symbol))),
            "-" if trade_value is None else f"{trade_value:.2f}",
            "-" if fee is None else f"{fee:.2f}",
            format_decimal(row.get("score")),
        ])
    if not table_rows:
        return None
    latest_trade_date = max(row.get("trade_date", "") for row in ordered_rows if row.get("trade_date"))
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
    return (
        f"[trade executions] trade_date={latest_trade_date} "
        f"new_rows={len(ordered_rows)} shown={len(table_rows)} total_rows={total_rows if total_rows is not None else len(ordered_rows)}\n{table}"
    )


def build_no_new_trade_line(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "[trade executions] no trades yet"
    latest = rows[-1]
    return (
        "[trade executions] no new executions in this cycle "
        f"total_rows={len(rows)} last_execution={latest.get('executed_at', '-')} "
        f"last_symbol={latest.get('symbol', '-')} last_action={latest.get('action', '-')}"
    )


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


def print_live_plan(config_path: Path, runtime_config: dict, session_root: Path | None = None) -> None:
    poll_interval_seconds = max(1, safe_int(runtime_config.get("live-price-poll-interval-seconds"), 60))
    queue_refresh_seconds = max(1, safe_int(runtime_config.get("live-price-refresh-interval-seconds"), 60))
    shared_market_refresh_seconds = max(1, safe_int(runtime_config.get("shared-live-market-refresh-interval-seconds"), 60))
    signal_interval_minutes = max(1, safe_int(runtime_config.get("live-signal-interval-minutes"), 3))
    poll_interval_minutes = max(1, math.ceil(poll_interval_seconds / 60.0))
    queue_refresh_minutes = max(1, math.ceil(queue_refresh_seconds / 60.0))
    shared_market_refresh_minutes = max(1, math.ceil(shared_market_refresh_seconds / 60.0))
    print("=" * 100)
    print("Barra CNE5 Live Paper")
    print("=" * 100)
    print(f"Config file         : {config_path}", flush=True)
    if session_root is not None:
        print(f"Session root        : {session_root}", flush=True)
    print("Market data         : Tushare realtime in-session; GBM-simulated CSI300 daily bars off-session", flush=True)
    print("Order model         : synthetic internal execution only; no real Lean orders", flush=True)
    print(
        "Console output      : "
        + (
            "native no-broker LEAN live-paper stdout"
            if resolve_console_output_mode(runtime_config) == "native"
            else "custom account/trade/allocation summaries"
        ),
        flush=True,
    )
    print(f"Factor mode         : {runtime_config['factor-source-mode']} (seed={runtime_config['random-factor-seed']})", flush=True)
    print("Resolved factor src : printed after bridge materializes live factor files", flush=True)
    print(
        f"Price source mode   : {runtime_config['live-price-source-mode']} "
        f"(sim seed={runtime_config['simulated-live-price-random-seed']} "
        f"lookback={runtime_config['simulated-live-price-lookback-days']}d "
        f"vol_scale={runtime_config['simulated-live-price-volatility-scale']} "
        f"vol_floor={runtime_config['simulated-live-price-min-daily-volatility']} "
        f"jump_p={runtime_config['simulated-live-price-jump-probability']} "
        f"jump_sigma={runtime_config['simulated-live-price-jump-scale']})",
        flush=True,
    )
    print(f"Universe            : {runtime_config['universe']}", flush=True)
    print(f"Bridge ready timeout: {runtime_config['bridge-ready-timeout-seconds']} seconds", flush=True)
    print(f"Bridge quote cover  : {runtime_config['bridge-ready-min-quote-coverage']}", flush=True)
    print(f"Bridge factor cover : {runtime_config['bridge-ready-min-factor-coverage']}", flush=True)
    print(f"Minute API cap      : {runtime_config['live-price-max-requests-per-minute']} requests/minute", flush=True)
    print(f"Market cache refresh: {runtime_config['shared-live-market-refresh-interval-seconds']} seconds", flush=True)
    print(f"rt_k poll interval  : {runtime_config['live-price-poll-interval-seconds']} seconds", flush=True)
    print(f"Signal interval     : {runtime_config['live-signal-interval-minutes']} minutes", flush=True)
    print(f"LEAN queue refresh  : {runtime_config['live-price-refresh-interval-seconds']} seconds", flush=True)
    print(f"Initial cash        : {runtime_config['initial-cash']}", flush=True)
    print(f"Rebalance           : {runtime_config['rebalance-frequency']} topN={runtime_config['top-n']} exposure={runtime_config['target-portfolio-exposure']}", flush=True)
    print(f"Tushare data path   : {runtime_config['tushare-data-path']}", flush=True)
    print(f"Factor data path    : {runtime_config['factor-data-path']}", flush=True)
    print(f"Historical factors  : {runtime_config['external-factor-path']}", flush=True)
    print(f"Bridge report       : {runtime_config['live-factor-report-file']}", flush=True)
    print(f"Price snapshot      : {runtime_config['live-price-snapshot-file']}", flush=True)
    print(f"Shared mkt snapshot : {runtime_config['shared-live-market-snapshot-file']}", flush=True)
    print(f"Portfolio state     : {runtime_config['portfolio-state-file']}", flush=True)
    print(f"Quote archive       : {runtime_config['daily-quote-archive-path']}", flush=True)
    print(f"Shared mkt archive  : {runtime_config['shared-live-market-archive-path']}", flush=True)
    print(f"Daily summary       : {runtime_config['daily-summary-file']}", flush=True)
    print(f"Allocation report   : {runtime_config['allocation-report-file']}", flush=True)
    print(f"Exposure report     : {runtime_config['factor-exposure-file']}", flush=True)
    print(f"Trade report        : {runtime_config['trade-report-file']}", flush=True)
    print(
        "Process flow        : "
        f"1) shared market cache refreshes all A-share stocks and ETFs every {shared_market_refresh_minutes} minutes "
        f"2) Barra bridge filters CSI300 quotes and refreshes the strategy snapshot every {poll_interval_minutes} minutes "
        "3) LEAN starts only after the first full snapshot is ready "
        "4) strategy restores persisted cash/holdings "
        f"5) LEAN queue refreshes every {queue_refresh_minutes} minutes and strategy aligns portfolio every {signal_interval_minutes} minutes, then writes account/trade/allocation outputs",
        flush=True,
    )
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


def print_runtime_status(
    runtime_config: dict,
    output_fresh_since: float | None = None,
    display_state: dict | None = None,
) -> None:
    factor_path = Path(runtime_config["factor-data-path"])
    bridge_report = load_bridge_report(Path(runtime_config["live-factor-report-file"]))
    daily_row = console.read_latest_csv_row(Path(runtime_config["daily-summary-file"]), modified_after=output_fresh_since)
    snapshot_payload, snapshot_quotes = console.build_snapshot_quote_lookup(
        Path(runtime_config["live-price-snapshot-file"]),
    )

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

    extra_rows: list[tuple[str, str]] = []
    if daily_row:
        if daily_row.get("eligible_symbols") not in (None, ""):
            extra_rows.append(("Eligible", str(daily_row.get("eligible_symbols"))))
        if daily_row.get("selected_symbols") not in (None, ""):
            extra_rows.append(("Selected", str(daily_row.get("selected_symbols"))))
        turnover = format_fraction_percent(daily_row.get("turnover"))
        if turnover != "-":
            extra_rows.append(("Turnover", turnover))
        kelly = format_decimal(daily_row.get("kelly_scale"), digits=2)
        if kelly != "-":
            extra_rows.append(("Kelly", kelly))
        effective_exposure = format_fraction_percent(daily_row.get("effective_exposure"))
        if effective_exposure != "-":
            extra_rows.append(("Eff Exposure", effective_exposure))
        stop_loss_exits = daily_row.get("stop_loss_exits")
        if stop_loss_exits not in (None, ""):
            extra_rows.append(("StopLoss Exits", str(stop_loss_exits)))
        trailing_stop_exits = daily_row.get("trailing_stop_exits")
        if trailing_stop_exits not in (None, ""):
            extra_rows.append(("Trail Exits", str(trailing_stop_exits)))
        score_spread = format_decimal(daily_row.get("score_spread"))
        if score_spread != "-":
            extra_rows.append(("Score Spread", score_spread))

    account_summary = console.build_account_summary_from_sources(
        initial_cash=runtime_config.get("initial-cash"),
        daily_row=daily_row,
        snapshot_payload=snapshot_payload,
        bridge_report=bridge_report,
        live_quote_count=received_quotes,
        extra_rows=extra_rows,
    )
    if console.should_print_section(display_state, "account_summary", account_summary):
        print(account_summary, flush=True)

    factor_source_line = build_factor_source_line(bridge_report)
    if console.should_print_section(display_state, "factor_source", factor_source_line):
        print(factor_source_line, flush=True)

    trade_rows = console.read_csv_rows(Path(runtime_config["trade-report-file"]), modified_after=output_fresh_since)
    previous_trade_count = safe_int(display_state.get("_trade_row_count"), 0) if isinstance(display_state, dict) else 0
    total_trade_rows = len(trade_rows)
    if isinstance(display_state, dict):
        display_state["_trade_row_count"] = str(total_trade_rows)

    if trade_rows:
        new_trade_rows = trade_rows[previous_trade_count:] if previous_trade_count < total_trade_rows else []
        if new_trade_rows:
            signal_preview = console.build_trade_executions_preview(
                new_trade_rows,
                snapshot_quotes=snapshot_quotes,
                limit=12,
                total_rows=total_trade_rows,
            )
            if console.should_print_section(display_state, "signal_preview", signal_preview):
                print(signal_preview, flush=True)
        else:
            no_trade_line = console.build_no_new_trade_line(trade_rows)
            if console.should_print_section(display_state, "signal_preview", no_trade_line):
                print(no_trade_line, flush=True)
    else:
        no_trade_line = console.build_no_new_trade_line([])
        if console.should_print_section(display_state, "signal_preview", no_trade_line):
            print(no_trade_line, flush=True)

    allocation_preview = console.build_portfolio_allocation_from_csv(
        Path(runtime_config["allocation-report-file"]),
        snapshot_quotes=snapshot_quotes,
        limit=20,
        modified_after=output_fresh_since,
    )
    if allocation_preview is None:
        allocation_preview = "[portfolio allocation] no holdings yet"
    if console.should_print_section(display_state, "allocation_preview", allocation_preview):
        print(allocation_preview, flush=True)

    exposure_preview = build_exposure_preview(
        Path(runtime_config["factor-exposure-file"]),
        modified_after=output_fresh_since,
    )
    if console.should_print_section(display_state, "exposure_preview", exposure_preview):
        print(exposure_preview, flush=True)


def normalize_lean_output_line(line: str, console_output_mode: str = "custom") -> str | None:
    raw_text = line.rstrip("\n")
    if console_output_mode == "native":
        return raw_text if raw_text.strip() else None

    text = raw_text.strip()
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


def normalize_process_output_line(label: str, line: str, console_output_mode: str = "custom") -> str | None:
    text = line.rstrip("\n")
    if label == "lean":
        normalized = normalize_lean_output_line(text, console_output_mode=console_output_mode)
        if normalized is None:
            return None
        if console_output_mode == "native":
            return normalized
        return f"[lean] {normalized}"
    text = text.strip()
    if not text:
        return None
    return f"[{label}] {text}"


def stream_process_output(
    process: subprocess.Popen,
    label: str,
    console_output_mode: str = "custom",
) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def consume_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            normalized = normalize_process_output_line(label, line, console_output_mode=console_output_mode)
            if normalized:
                print(normalized, flush=True)

    thread = threading.Thread(target=consume_output, name=f"{label}-output", daemon=True)
    thread.start()
    return thread


def start_logged_process(
    command: list[str],
    cwd: Path,
    label: str,
    console_output_mode: str = "custom",
) -> tuple[subprocess.Popen, threading.Thread | None]:
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
    return process, stream_process_output(process, label, console_output_mode=console_output_mode)


def join_output_thread(thread: threading.Thread | None) -> None:
    if thread is None:
        return
    thread.join(timeout=5)


def wait_for_process_with_status(
    process: subprocess.Popen,
    runtime_config: dict,
    status_interval_seconds: float | None = None,
    output_fresh_since: float | None = None,
    console_output_mode: str | None = None,
) -> int:
    resolved_console_output_mode = resolve_console_output_mode(runtime_config, console_output_mode)
    if resolved_console_output_mode == "native":
        return process.wait()

    interval_seconds = max(
        5.0,
        float(status_interval_seconds or runtime_config.get("live-price-poll-interval-seconds") or 60),
    )
    display_state: dict[str, str] = {}
    print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
    next_status = time.time() + interval_seconds
    while True:
        returncode = process.poll()
        if returncode is not None:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
            return returncode

        now = time.time()
        if now >= next_status:
            print_runtime_status(runtime_config, output_fresh_since=output_fresh_since, display_state=display_state)
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
    min_quote_coverage = max(0.0, min(1.0, safe_float(runtime_config.get("bridge-ready-min-quote-coverage")) or 1.0))
    min_factor_coverage = max(0.0, min(1.0, safe_float(runtime_config.get("bridge-ready-min-factor-coverage")) or 0.95))
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
        refreshed_quotes = int(live_quote_report.get("refreshed_quote_count") or 0)
        missing_quotes = int(live_quote_report.get("missing_quote_count") or max(0, requested_quotes - received_quotes))
        resolved_symbols = int(bridge_payload.get("resolved_symbol_count") or 0)
        written_symbols = int(bridge_payload.get("written_symbol_count") or 0)

        quote_coverage = (received_quotes / requested_quotes) if requested_quotes > 0 else 0.0
        factor_coverage = (written_symbols / resolved_symbols) if resolved_symbols > 0 else 0.0
        if quote_coverage < min_quote_coverage:
            return False
        if refreshed_quotes < max(1, requested_quotes) * min_quote_coverage:
            return False
        if missing_quotes > 0:
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
    launcher_dir = launcher_binary_path().resolve().parent
    launcher_dll = launcher_dir / "QuantConnect.Lean.Launcher.dll"
    command = [runner.dotnet_binary_path(), str(launcher_dll), "--config", str(config_path)]
    return command, launcher_dir


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
    console_output_mode: str | None = None,
) -> int:
    session_config_path, runtime_config, session_root = prepare_session_config(config_path)
    runtime_config["console-output-mode"] = resolve_console_output_mode(runtime_config, console_output_mode)
    resolved_config_path = session_config_path
    print_live_plan(resolved_config_path, runtime_config, session_root=session_root)

    lock_path = Path(runtime_config["portfolio-state-file"]).with_name("barra-cne5-live-paper.lock")
    lock_conflict = None
    if start_launcher:
        lock_conflict = acquire_live_paper_lock(lock_path, session_root)
        if lock_conflict is not None:
            print(
                "[live paper] another Barra CNE5 launcher is already active "
                f"(pid={lock_conflict['pid']} session={lock_conflict['session_root']} "
                f"started_at={lock_conflict['started_at']})",
                file=sys.stderr,
                flush=True,
            )
            return 1

    if runtime_config["console-output-mode"] == "native":
        bridge_spec = runner.ProcessSpec(
            command=build_bridge_command(resolved_config_path, python_executable),
            cwd=repo_root(),
            label="bridge",
            passthrough=False,
        )
        launcher_command, workdir = build_launcher_command(resolved_config_path)
        launcher_spec = runner.ProcessSpec(
            command=launcher_command,
            cwd=workdir,
            label="lean",
            passthrough=True,
        )
        try:
            return runner.run_native_live_paper_session(
                strategy_name="Barra CNE5 live paper",
                start_bridge=start_bridge,
                start_launcher=start_launcher,
                bridge_spec=bridge_spec,
                launcher_spec=launcher_spec,
                wait_for_bridge_ready=(lambda process, bridge_started_at: wait_for_bridge_ready(
                    resolved_config_path,
                    process,
                    bridge_started_at=bridge_started_at,
                    timeout_seconds=float(runtime_config["bridge-ready-timeout-seconds"]),
                )) if start_bridge and start_launcher else None,
                bridge_timeout_message=(
                    "Timed out waiting for bridge to finish the first full realtime snapshot and factor materialization. "
                    "Check Tushare connectivity or increase `bridge-ready-timeout-seconds`."
                ),
            )
        finally:
            if start_launcher:
                release_live_paper_lock(lock_path)

    try:
        if not start_bridge and not start_launcher:
            return 0

        if start_bridge and not start_launcher:
            print("[stage 1/1] starting bridge only", flush=True)
            bridge_process, bridge_thread = start_logged_process(
                build_bridge_command(resolved_config_path, python_executable),
                repo_root(),
                "bridge",
                console_output_mode="custom",
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
            launcher_command, workdir = build_launcher_command(resolved_config_path)
            print("[stage 1/1] starting launcher only", flush=True)
            launcher_started_at = time.time()
            launcher_process, launcher_thread = start_logged_process(
                launcher_command,
                workdir,
                "lean",
                console_output_mode=runtime_config["console-output-mode"],
            )
            try:
                return wait_for_process_with_status(
                    launcher_process,
                    runtime_config,
                    output_fresh_since=launcher_started_at,
                    console_output_mode=runtime_config["console-output-mode"],
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
            build_bridge_command(resolved_config_path, python_executable),
            repo_root(),
            "bridge",
            console_output_mode="custom",
        )
        launcher_process = None
        launcher_thread = None
        try:
            print("[stage 2/4] waiting for bridge to materialize live factor files", flush=True)
            if not wait_for_bridge_ready(
                resolved_config_path,
                bridge_process,
                bridge_started_at=bridge_started_at,
                timeout_seconds=float(runtime_config["bridge-ready-timeout-seconds"]),
            ):
                if bridge_process.poll() is not None:
                    return bridge_process.returncode
                print(
                    "Timed out waiting for bridge to finish the first full realtime snapshot and factor materialization. "
                    "Check Tushare connectivity or increase `bridge-ready-timeout-seconds`.",
                    file=sys.stderr,
                )
                return 1

            launcher_command, workdir = build_launcher_command(resolved_config_path)
            print("[stage 3/4] bridge ready; starting LEAN live-paper engine", flush=True)
            launcher_started_at = time.time()
            launcher_process, launcher_thread = start_logged_process(
                launcher_command,
                workdir,
                "lean",
                console_output_mode=runtime_config["console-output-mode"],
            )
            try:
                print("[stage 4/4] entering live status loop", flush=True)
                return wait_for_process_with_status(
                    launcher_process,
                    runtime_config,
                    output_fresh_since=launcher_started_at,
                    console_output_mode=runtime_config["console-output-mode"],
                )
            except KeyboardInterrupt:
                print("[live paper] interrupted by user; stopping launcher and bridge", flush=True)
                return 130
        finally:
            terminate_process(launcher_process)
            terminate_process(bridge_process)
            join_output_thread(launcher_thread)
            join_output_thread(bridge_thread)
    finally:
        if start_launcher:
            release_live_paper_lock(lock_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch Barra CNE5 live bridge and LEAN live-paper engine")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--bridge-only", action="store_true")
    parser.add_argument("--launcher-only", action="store_true")
    parser.add_argument("--python-executable")
    parser.add_argument("--console-output-mode", choices=("native", "custom"))
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
        console_output_mode=args.console_output_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
