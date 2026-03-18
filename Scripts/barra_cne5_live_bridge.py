#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
DATA_SOURCE_DIR = CURRENT_DIR.parent / "data-source" / "tushare"
for candidate in [CURRENT_DIR, DATA_SOURCE_DIR]:
    candidate_text = str(candidate)
    if candidate_text not in sys.path:
        sys.path.insert(0, candidate_text)

import barra_cne5_factor_bridge
from barra_cne5_data_loader import BarraCNE5DataLoader
import config as tushare_runtime_config
from rt_daily_downloader import GbmSyntheticRtDailyClient, TushareRtDailyClient


PATH_KEYS = {
    "tushare-data-path",
    "factor-data-path",
    "live-factor-report-file",
    "live-price-snapshot-file",
    "daily-quote-archive-path",
    "external-factor-path",
}
DATA_RELATIVE_KEYS = {"factor-data-path", "daily-quote-archive-path", "external-factor-path"}
RESULTS_RELATIVE_KEYS = {"live-factor-report-file", "live-price-snapshot-file"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug"


def default_external_factor_path() -> Path:
    return repo_root() / "Data" / "alternative" / "barra-cne5-factors"


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "factor-data-path": str(root / "Data" / "alternative" / "barra-cne5-live-factors"),
        "live-factor-report-file": str(root / "Results" / "barra-cne5-live-bridge-report.json"),
        "live-price-snapshot-file": str(root / "Results" / "barra-cne5-live-price-snapshot.json"),
        "daily-quote-archive-path": str(root / "Data" / "archive" / "barra-cne5-live-daily-quotes"),
        "factor-source-mode": "auto",
        "random-factor-seed": 42,
        "external-factor-path": str(default_external_factor_path()),
        "universe": "csi300",
        "index-code": "000300.SH",
        "market-symbol": "000300.SH",
        "symbols": None,
        "tushare-token": "",
        "tushare-http-url": "",
        "tushare-token-env-var": "TUSHARE_TOKEN",
        "live-price-batch-size": 100,
        "live-price-max-workers": 1,
        "live-price-max-requests-per-minute": 50,
        "live-price-poll-interval-seconds": 180,
        "live-price-source-mode": "auto",
        "simulated-live-price-random-seed": 20260317,
        "simulated-live-price-lookback-days": 60,
        "simulated-live-price-min-history-days": 20,
        "simulated-live-price-trading-minutes-per-day": 240,
        "simulated-live-price-volatility-scale": 8.0,
        "simulated-live-price-min-daily-volatility": 0.80,
        "simulated-live-price-jump-probability": 0.22,
        "simulated-live-price-jump-scale": 0.10,
        "factor-worker-count": "auto",
        "parallel-date-block-size": 1,
        "progress-interval-symbols": 100,
        "progress-interval-files": 50,
        "live-factor-poll-interval-seconds": 180,
        "timezone": "Asia/Shanghai",
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def resolve_live_parameter_paths(config_payload: dict, config_root: Path) -> dict:
    launcher_base = launcher_workdir()
    data_base = launcher_base
    results_base = launcher_base

    parameters = dict(config_payload.get("parameters") or {})

    data_folder = config_payload.get("data-folder")
    if data_folder:
        data_path = Path(str(data_folder))
        data_base = data_path if data_path.is_absolute() else (launcher_base / data_path).resolve()

    results_folder = config_payload.get("results-destination-folder")
    if results_folder:
        results_path = Path(str(results_folder))
        results_base = results_path if results_path.is_absolute() else (launcher_base / results_path).resolve()

    resolved = dict(parameters)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue

        path = Path(str(value))
        if path.is_absolute():
            resolved[key] = str(path)
            continue

        if key in DATA_RELATIVE_KEYS:
            resolved[key] = str((data_base / path).resolve())
        elif key in RESULTS_RELATIVE_KEYS:
            resolved[key] = str((results_base / path).resolve())
        else:
            resolved[key] = str((config_root / path).resolve())

    return resolved


def coerce_int(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_live_bridge_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()

    if config_path:
        path = Path(config_path).resolve()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("parameters"), dict):
            config.update(resolve_live_parameter_paths(loaded, path.parent))
        elif isinstance(loaded, dict):
            config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    config = resolve_config_paths(config, repo_root())
    config["live-factor-poll-interval-seconds"] = max(1, coerce_int(config.get("live-factor-poll-interval-seconds"), 180))
    config["live-price-batch-size"] = max(1, coerce_int(config.get("live-price-batch-size"), 100))
    config["live-price-max-workers"] = max(1, coerce_int(config.get("live-price-max-workers"), 1))
    config["live-price-max-requests-per-minute"] = max(
        1,
        coerce_int(config.get("live-price-max-requests-per-minute"), 50),
    )
    config["live-price-poll-interval-seconds"] = max(
        1,
        coerce_int(config.get("live-price-poll-interval-seconds"), config["live-factor-poll-interval-seconds"]),
    )
    config["simulated-live-price-random-seed"] = coerce_int(
        config.get("simulated-live-price-random-seed"),
        20260317,
    )
    config["simulated-live-price-lookback-days"] = max(
        10,
        coerce_int(config.get("simulated-live-price-lookback-days"), 60),
    )
    config["simulated-live-price-min-history-days"] = max(
        5,
        coerce_int(config.get("simulated-live-price-min-history-days"), 20),
    )
    config["simulated-live-price-trading-minutes-per-day"] = max(
        1,
        coerce_int(config.get("simulated-live-price-trading-minutes-per-day"), 240),
    )
    config["simulated-live-price-volatility-scale"] = max(
        1.0,
        float(config.get("simulated-live-price-volatility-scale") or 8.0),
    )
    config["simulated-live-price-min-daily-volatility"] = max(
        0.05,
        float(config.get("simulated-live-price-min-daily-volatility") or 0.80),
    )
    config["simulated-live-price-jump-probability"] = max(
        0.0,
        min(1.0, float(config.get("simulated-live-price-jump-probability") or 0.22)),
    )
    config["simulated-live-price-jump-scale"] = max(
        0.0,
        float(config.get("simulated-live-price-jump-scale") or 0.10),
    )
    return config


def resolve_tushare_token(config: dict, explicit_token: str | None = None) -> str:
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()

    configured_token = str(config.get("tushare-token") or "").strip()
    if configured_token:
        return configured_token

    env_var = str(config.get("tushare-token-env-var") or "TUSHARE_TOKEN")
    token = os.getenv(env_var, "").strip()
    if token:
        return token

    fallback_token = str(getattr(tushare_runtime_config, "TUSHARE_TOKEN", "") or "").strip()
    if fallback_token:
        return fallback_token

    raise RuntimeError(f"Missing Tushare token. Please set {env_var}, configure `tushare-token`, or pass --tushare-token.")


def resolve_live_trade_date(config: dict, now: datetime | None = None) -> str:
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current_time = now.astimezone(timezone) if now else datetime.now(timezone)
    today = current_time.strftime("%Y%m%d")
    window_start = (current_time - timedelta(days=14)).strftime("%Y%m%d")
    window_end = (current_time + timedelta(days=14)).strftime("%Y%m%d")

    loader = BarraCNE5DataLoader(config["tushare-data-path"])
    trading_dates = loader.get_trading_dates(
        start_date=window_start,
        end_date=window_end,
        reference_symbol=config.get("market-symbol", "000300.SH"),
    )
    if not trading_dates:
        raise RuntimeError("Unable to resolve live trading calendar for Barra CNE5 bridge.")

    if today in trading_dates:
        return today

    historical_dates = [trade_date for trade_date in trading_dates if trade_date <= today]
    if historical_dates:
        return historical_dates[-1]

    return trading_dates[0]


def resolve_market_data_context(config: dict, now: datetime | None = None) -> dict:
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current_time = now.astimezone(timezone) if now else datetime.now(timezone)
    today = current_time.strftime("%Y%m%d")
    requested_mode = str(config.get("live-price-source-mode") or "auto").strip().lower()
    if requested_mode not in {"auto", "realtime", "simulate"}:
        requested_mode = "auto"

    loader = BarraCNE5DataLoader(config["tushare-data-path"])
    trading_dates = loader.get_trading_dates(
        start_date=today,
        end_date=today,
        reference_symbol=config.get("market-symbol", "000300.SH"),
    )
    is_trading_day = today in set(trading_dates)
    minute_of_day = current_time.hour * 60 + current_time.minute + current_time.second / 60.0

    if not is_trading_day:
        session_state = "calendar-closed"
        market_open = False
    elif 9 * 60 + 30 <= minute_of_day < 11 * 60 + 30:
        session_state = "regular-session-morning"
        market_open = True
    elif 13 * 60 <= minute_of_day < 15 * 60:
        session_state = "regular-session-afternoon"
        market_open = True
    elif minute_of_day < 9 * 60 + 30:
        session_state = "pre-open"
        market_open = False
    elif minute_of_day < 13 * 60:
        session_state = "midday-break"
        market_open = False
    else:
        session_state = "after-hours"
        market_open = False

    if requested_mode == "simulate":
        selected_mode = "gbm-simulated"
        reason = "configured simulate mode"
    elif requested_mode == "realtime":
        selected_mode = "tushare-realtime"
        reason = "configured realtime mode"
    elif market_open:
        selected_mode = "tushare-realtime"
        reason = "A-share regular session open"
    else:
        selected_mode = "gbm-simulated"
        reason = f"A-share market closed ({session_state})"

    return {
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "session_state": session_state,
        "market_open": market_open,
        "reason": reason,
        "evaluated_at": current_time.isoformat(),
    }


def build_bridge_factor_config(config: dict, trade_date: str) -> dict:
    return barra_cne5_factor_bridge.load_pipeline_config(
        overrides={
            "tushare-data-path": config.get("tushare-data-path"),
            "output-path": config.get("factor-data-path"),
            "report-file": config.get("live-factor-report-file"),
            "factor-source-mode": config.get("factor-source-mode"),
            "random-factor-seed": config.get("random-factor-seed"),
            "external-factor-path": config.get("external-factor-path"),
            "universe": config.get("universe"),
            "index-code": config.get("index-code"),
            "market-symbol": config.get("market-symbol"),
            "symbols": config.get("symbols"),
            "date": trade_date,
            "factor-worker-count": config.get("factor-worker-count"),
            "parallel-date-block-size": config.get("parallel-date-block-size"),
            "progress-interval-symbols": config.get("progress-interval-symbols"),
            "progress-interval-files": config.get("progress-interval-files"),
        }
    )


def factor_output_columns() -> list[str]:
    return list(barra_cne5_factor_bridge.OUTPUT_COLUMNS)


def resolve_external_factor_seed_path(config: dict) -> Path | None:
    configured = config.get("external-factor-path")
    candidate = Path(str(configured)).resolve() if configured else default_external_factor_path().resolve()
    if not candidate.exists():
        return None

    output_path = Path(config["factor-data-path"]).resolve()
    if candidate == output_path:
        return None
    return candidate


def factor_file_path(root: str | Path, ts_code: str) -> Path:
    ticker, market = ts_code.split(".")
    market_directory = "sse" if market == "SH" else "szse"
    return Path(root) / market_directory / "daily" / f"{ticker}.csv"


def load_factor_file(path: Path) -> pd.DataFrame:
    columns = factor_output_columns()
    if not path.exists():
        return pd.DataFrame(columns=columns)

    frame = pd.read_csv(path, dtype={"trade_date": str})
    if frame.empty or "trade_date" not in frame.columns:
        return pd.DataFrame(columns=columns)

    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[columns].copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
    frame = frame.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
    return frame.reset_index(drop=True)


def seed_live_factor_history_from_external(config: dict, universe: list[str], trade_date: str) -> dict | None:
    external_root = resolve_external_factor_seed_path(config)
    if external_root is None:
        return None

    output_root = Path(config["factor-data-path"]).resolve()
    requested = len(universe)
    if requested <= 0:
        return {
            "status": "ok",
            "mode": "external-seed",
            "external_factor_path": str(external_root),
            "output_path": str(output_root),
            "resolved_symbol_count": 0,
            "written_symbol_count": 0,
            "written_symbols": [],
            "exact_trade_date_symbol_count": 0,
            "carry_forward_symbol_count": 0,
            "missing_symbol_count": 0,
            "missing_symbols": [],
            "row_count": 0,
            "snapshots": [{"trade_date": trade_date, "rows": 0, "coverage": 0.0}],
        }

    print(
        f"[live bridge][factor seed] source=external path={external_root} "
        f"trade_date={trade_date} symbols={requested}",
        flush=True,
    )

    written_symbols: list[str] = []
    missing_symbols: list[str] = []
    exact_trade_date_count = 0
    carry_forward_count = 0
    report_every = max(1, int(config.get("progress-interval-symbols", 100) or 100))

    for index, ts_code in enumerate(universe, start=1):
        source_path = factor_file_path(external_root, ts_code)
        source_frame = load_factor_file(source_path)
        if source_frame.empty:
            missing_symbols.append(ts_code)
        else:
            history = source_frame[source_frame["trade_date"] <= trade_date].copy()
            if history.empty:
                missing_symbols.append(ts_code)
            else:
                if (history["trade_date"] == trade_date).any():
                    exact_trade_date_count += 1
                else:
                    carry_forward_count += 1
                    carry_row = history.iloc[[-1]].copy()
                    carry_row.loc[:, "trade_date"] = trade_date
                    history = pd.concat([history, carry_row], ignore_index=True)

                destination_path = factor_file_path(output_root, ts_code)
                existing_frame = load_factor_file(destination_path)
                if existing_frame.empty:
                    merged = history.copy()
                else:
                    merged = pd.concat([existing_frame, history], ignore_index=True)
                merged["trade_date"] = merged["trade_date"].astype(str).str.zfill(8)
                merged = merged.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                merged.to_csv(destination_path, index=False, float_format="%.10f")
                written_symbols.append(ts_code)

        if index == 1 or index % report_every == 0 or index == requested:
            print(
                f"[live bridge][factor seed] {index}/{requested} "
                f"written={len(written_symbols)} exact={exact_trade_date_count} "
                f"carry_forward={carry_forward_count} missing={len(missing_symbols)} "
                f"current={ts_code}",
                flush=True,
            )

    coverage = len(written_symbols) / max(requested, 1)
    report = {
        "status": "ok" if written_symbols else "no_external_factors",
        "mode": "external-seed",
        "external_factor_path": str(external_root),
        "output_path": str(output_root),
        "resolved_symbol_count": requested,
        "written_symbol_count": len(written_symbols),
        "written_symbols": sorted(written_symbols),
        "exact_trade_date_symbol_count": exact_trade_date_count,
        "carry_forward_symbol_count": carry_forward_count,
        "missing_symbol_count": len(missing_symbols),
        "missing_symbols": missing_symbols,
        "row_count": len(written_symbols),
        "snapshots": [{
            "trade_date": trade_date,
            "rows": len(written_symbols),
            "coverage": round(coverage, 4),
        }],
    }
    print(
        f"[live bridge][factor seed] completed mode=external-seed "
        f"written_symbols={len(written_symbols)}/{requested} "
        f"carry_forward={carry_forward_count} missing={len(missing_symbols)}",
        flush=True,
    )
    return report


def materialize_live_factor_snapshot(config: dict, universe: list[str], trade_date: str) -> dict:
    requested_mode = str(config.get("factor-source-mode") or "auto").strip().lower()
    if requested_mode in {"auto", "import"}:
        seed_report = seed_live_factor_history_from_external(config, universe, trade_date)
        if seed_report and seed_report.get("written_symbol_count", 0) > 0:
            return seed_report
        print(
            f"[live bridge][factor] external seed unavailable; fallback mode={requested_mode}",
            flush=True,
        )

    bridge_config = build_bridge_factor_config(config, trade_date)
    return barra_cne5_factor_bridge.run_factor_bridge(bridge_config)


def write_live_bridge_report(
    config: dict,
    trade_date: str,
    bridge_report: dict,
    market_preview: dict | None = None,
    live_quote_report: dict | None = None,
) -> None:
    report_path = Path(config["live-factor-report-file"])
    payload = {
        "status": "ok",
        "generated_at": datetime.now(ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))).isoformat(),
        "trade_date": trade_date,
        "factor_data_path": config["factor-data-path"],
        "live_price_snapshot_file": config.get("live-price-snapshot-file"),
        "bridge_report": bridge_report,
        "live_quote_report": live_quote_report or {},
        "market_preview": market_preview or {},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric):
        return None
    return numeric


def format_price(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric:.2f}"


def format_percent(value) -> str:
    numeric = safe_float(value)
    return "-" if numeric is None else f"{numeric:+.2f}%"


def format_volume(value) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "-"
    if abs(numeric) >= 1_000_000_000:
        return f"{numeric / 1_000_000_000:.2f}B"
    if abs(numeric) >= 1_000_000:
        return f"{numeric / 1_000_000:.2f}M"
    if abs(numeric) >= 1_000:
        return f"{numeric / 1_000:.2f}K"
    return f"{numeric:.0f}"


def format_market_value(value) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "-"
    if abs(numeric) >= 100_000_000:
        return f"{numeric / 100_000_000:.2f}e8"
    if abs(numeric) >= 10_000:
        return f"{numeric / 10_000:.2f}w"
    return f"{numeric:.0f}"


def sanitize_json_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def normalize_trade_date_value(value, fallback: str) -> str:
    if value is None:
        return fallback
    text = "".join(character for character in str(value).strip() if character.isdigit())
    return text[:8] if len(text) >= 8 else fallback


def normalize_live_quotes(quotes: pd.DataFrame | None, trade_date: str, fetch_timestamp: datetime) -> pd.DataFrame:
    columns = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "price",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
        "fetch_timestamp",
        "source_api",
    ]
    if quotes is None or quotes.empty:
        return pd.DataFrame(columns=columns)

    normalized = quotes.copy()
    normalized.columns = [str(column).strip().lower() for column in normalized.columns]
    if "ts_code" not in normalized.columns:
        raise RuntimeError("Realtime quote response must include ts_code column.")

    normalized["ts_code"] = normalized["ts_code"].astype(str).str.strip().str.upper()
    normalized = normalized[normalized["ts_code"] != ""].copy()
    if normalized.empty:
        return pd.DataFrame(columns=columns)

    if "trade_date" not in normalized.columns:
        normalized["trade_date"] = trade_date
    normalized["trade_date"] = normalized["trade_date"].map(lambda value: normalize_trade_date_value(value, trade_date))

    for field in ["open", "high", "low", "close", "price", "pre_close", "pct_chg", "vol", "amount"]:
        if field not in normalized.columns:
            normalized[field] = None
        normalized[field] = pd.to_numeric(normalized[field], errors="coerce")

    normalized["close"] = normalized["close"].where(normalized["close"].notna(), normalized["price"])
    generated_fetch_timestamp = fetch_timestamp.astimezone(ZoneInfo(str(fetch_timestamp.tzinfo or "Asia/Shanghai"))).isoformat()
    if "fetch_timestamp" not in normalized.columns:
        normalized["fetch_timestamp"] = generated_fetch_timestamp
    normalized["fetch_timestamp"] = normalized["fetch_timestamp"].where(
        normalized["fetch_timestamp"].notna() & (normalized["fetch_timestamp"].astype(str).str.strip() != ""),
        generated_fetch_timestamp,
    )
    inferred_source_api = normalized["ts_code"].map(
        lambda ts_code: "rt_etf_k" if TushareRtDailyClient.is_etf_code(ts_code) else "rt_k"
    )
    if "source_api" not in normalized.columns:
        normalized["source_api"] = inferred_source_api
    normalized["source_api"] = normalized["source_api"].where(
        normalized["source_api"].notna() & (normalized["source_api"].astype(str).str.strip() != ""),
        inferred_source_api,
    )

    normalized = normalized.drop_duplicates(subset=["ts_code"], keep="last").sort_values("ts_code").reset_index(drop=True)
    return normalized.loc[:, columns]


def load_existing_live_price_snapshot(path: str | Path) -> tuple[dict, pd.DataFrame]:
    snapshot_path = Path(path)
    columns = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "price",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
        "fetch_timestamp",
        "source_api",
    ]
    empty = pd.DataFrame(columns=columns)
    if not snapshot_path.exists():
        return {}, empty

    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, empty
    if not isinstance(payload, dict):
        return {}, empty

    rows = payload.get("quotes")
    if not isinstance(rows, list) or not rows:
        return payload, empty

    frame = pd.DataFrame(rows)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "ts_code" not in frame.columns:
        return payload, empty

    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame[frame["ts_code"] != ""].copy()
    if frame.empty:
        return payload, empty

    for field in ["trade_date", "open", "high", "low", "close", "price", "pre_close", "pct_chg", "vol", "amount", "fetch_timestamp", "source_api"]:
        if field not in frame.columns:
            frame[field] = None

    frame["trade_date"] = frame["trade_date"].map(
        lambda value: normalize_trade_date_value(value, str(payload.get("trade_date") or ""))
    )
    for field in ["open", "high", "low", "close", "price", "pre_close", "pct_chg", "vol", "amount"]:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame["source_api"] = frame["source_api"].astype(str).where(frame["source_api"].notna(), None)
    frame["fetch_timestamp"] = frame["fetch_timestamp"].astype(str).where(frame["fetch_timestamp"].notna(), None)
    frame = frame.drop_duplicates(subset=["ts_code"], keep="last").sort_values("ts_code").reset_index(drop=True)
    return payload, frame.loc[:, columns]


def build_quote_refresh_plan(
    universe: list[str],
    refresh_limit: int,
    previous_snapshot_payload: dict | None,
    trade_date: str,
    poll_interval_seconds: float | int = 180,
) -> dict:
    total_symbols = len(universe)
    if total_symbols <= 0:
        return {
            "refresh_symbols": [],
            "refresh_count": 0,
            "refresh_start_offset": 0,
            "refresh_end_offset": 0,
            "next_refresh_offset": 0,
            "estimated_full_refresh_minutes": 0,
        }
    estimated_full_refresh_minutes = max(1, int(math.ceil(max(1.0, float(poll_interval_seconds or 180)) / 60.0)))
    return {
        "refresh_symbols": list(universe),
        "refresh_count": total_symbols,
        "refresh_start_offset": 0,
        "refresh_end_offset": total_symbols,
        "next_refresh_offset": 0,
        "estimated_full_refresh_minutes": estimated_full_refresh_minutes,
    }


def merge_live_quotes(
    universe: list[str],
    trade_date: str,
    previous_quotes: pd.DataFrame | None,
    fresh_quotes: pd.DataFrame | None,
) -> pd.DataFrame:
    columns = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "price",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
        "fetch_timestamp",
        "source_api",
    ]
    allowed = {str(ts_code).strip().upper() for ts_code in universe if str(ts_code).strip()}
    frames: list[pd.DataFrame] = []

    if previous_quotes is not None and not previous_quotes.empty:
        retained = previous_quotes.copy()
        for column in columns:
            if column not in retained.columns:
                retained[column] = None
        retained["ts_code"] = retained["ts_code"].astype(str).str.strip().str.upper()
        retained = retained[
            retained["ts_code"].isin(allowed)
            & (retained["trade_date"].astype(str).str.zfill(8) == trade_date)
        ].copy()
        if not retained.empty:
            frames.append(retained.loc[:, columns])

    if fresh_quotes is not None and not fresh_quotes.empty:
        updated = fresh_quotes.copy()
        for column in columns:
            if column not in updated.columns:
                updated[column] = None
        updated["ts_code"] = updated["ts_code"].astype(str).str.strip().str.upper()
        updated = updated[updated["ts_code"].isin(allowed)].copy()
        if not updated.empty:
            updated.loc[:, "trade_date"] = trade_date
            frames.append(updated.loc[:, columns])

    if not frames:
        return pd.DataFrame(columns=columns)

    merged = pd.concat(frames, ignore_index=True)
    merged["trade_date"] = merged["trade_date"].astype(str).str.zfill(8)
    merged = merged.drop_duplicates(subset=["ts_code"], keep="last")
    merged = merged.sort_values("ts_code").reset_index(drop=True)
    return merged.loc[:, columns]


def archive_live_quotes(config: dict, quotes: pd.DataFrame, trade_date: str) -> str | None:
    if quotes.empty:
        return None

    archive_root = Path(config["daily-quote-archive-path"])
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_file = archive_root / f"date={trade_date}" / "quotes.parquet"
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    if archive_file.exists():
        existing = pd.read_parquet(archive_file)
        frames = [frame for frame in (existing, quotes) if frame is not None and not frame.empty]
        if frames:
            combined = pd.concat(frames, ignore_index=True)
        else:
            combined = quotes.copy()
        combined = combined.drop_duplicates(subset=["ts_code", "fetch_timestamp"], keep="last")
        combined.to_parquet(archive_file, engine="pyarrow", index=False)
    else:
        quotes.to_parquet(archive_file, engine="pyarrow", index=False)

    return str(archive_file)


def write_live_price_snapshot(
    config: dict,
    trade_date: str,
    quotes: pd.DataFrame,
    fetch_timestamp: datetime,
    metadata: dict | None = None,
) -> dict:
    snapshot_path = Path(config["live-price-snapshot-file"])
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "status": "ok",
        "generated_at": fetch_timestamp.isoformat(),
        "trade_date": trade_date,
        "quote_count": int(len(quotes.index)),
        "quotes": [
            {key: sanitize_json_value(value) for key, value in row.items()}
            for row in quotes.to_dict(orient="records")
        ],
    }
    if metadata:
        payload.update(metadata)
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_live_market_preview(
    trade_date: str,
    requested_count: int,
    quotes: pd.DataFrame,
    sample_size: int = 6,
    market_data_context: dict | None = None,
) -> dict:
    rows: list[dict[str, object]] = []
    if not quotes.empty:
        preview_rows = quotes.head(sample_size).to_dict(orient="records")
        for row in preview_rows:
            source_api = str(row.get("source_api") or "")
            rows.append({
                "symbol": row.get("ts_code"),
                "asset_type": "etf" if "etf" in source_api else "equity",
                "trade_date": row.get("trade_date") or trade_date,
                "close": row.get("close") or row.get("price"),
                "pct_chg": row.get("pct_chg"),
                "vol": row.get("vol"),
                "amount": row.get("amount"),
                "fetch_timestamp": row.get("fetch_timestamp"),
                "turnover_rate": None,
                "total_mv": None,
                "status": "ok",
                "source_api": row.get("source_api"),
            })

    return {
        "trade_date": trade_date,
        "universe_size": requested_count,
        "received_count": int(len(quotes.index)),
        "sample_size": len(rows),
        "source_mode": (market_data_context or {}).get("selected_mode"),
        "session_state": (market_data_context or {}).get("session_state"),
        "reason": (market_data_context or {}).get("reason"),
        "rows": rows,
    }


def print_live_market_preview(preview: dict) -> None:
    rows = list(preview.get("rows") or [])
    print(
        f"[live bridge][market] trade_date={preview.get('trade_date')} "
        f"source={preview.get('source_mode') or '-'} "
        f"session={preview.get('session_state') or '-'} "
        f"requested={preview.get('universe_size', 0)} "
        f"received={preview.get('received_count', 0)} preview_rows={len(rows)}",
        flush=True,
    )
    if preview.get("reason"):
        print(f"[live bridge][market] reason={preview.get('reason')}", flush=True)
    if not rows:
        print("[live bridge][market] no realtime quotes received in this cycle", flush=True)
        return

    asset_types = {}
    source_apis = {}
    for row in rows:
        asset_type = str(row.get("asset_type") or "unknown")
        source_api = str(row.get("source_api") or "unknown")
        asset_types[asset_type] = asset_types.get(asset_type, 0) + 1
        source_apis[source_api] = source_apis.get(source_api, 0) + 1
    asset_summary = ", ".join(f"{key}={value}" for key, value in sorted(asset_types.items()))
    source_summary = ", ".join(f"{key}={value}" for key, value in sorted(source_apis.items()))
    print(
        f"[live bridge][market] asset_types={asset_summary or '-'} source_apis={source_summary or '-'}",
        flush=True,
    )


def print_live_quote_flow(trade_date: str, fresh_quotes: pd.DataFrame, snapshot_quotes: pd.DataFrame, refresh_plan: dict) -> None:
    if snapshot_quotes.empty:
        print(
            f"[live bridge][quote flow] trade_date={trade_date} status=empty refreshed=0 snapshot_total=0",
            flush=True,
        )
        return

    total = len(snapshot_quotes.index)
    fresh_count = int(len(fresh_quotes.index)) if fresh_quotes is not None else 0
    carried_forward = max(0, total - fresh_count)
    source_counts = snapshot_quotes["source_api"].value_counts(dropna=False).to_dict() if "source_api" in snapshot_quotes.columns else {}
    source_summary = ", ".join(f"{key}={value}" for key, value in sorted(source_counts.items()))
    latest_fetch = "-"
    if "fetch_timestamp" in snapshot_quotes.columns:
        timestamps = [str(value) for value in snapshot_quotes["fetch_timestamp"].dropna().tolist()]
        if timestamps:
            latest_fetch = max(timestamps)
    print(
        f"[live bridge][quote flow] trade_date={trade_date} refreshed={fresh_count} "
        f"carried_forward={carried_forward} snapshot_total={total} "
        f"refresh_cursor={refresh_plan.get('refresh_start_offset', 0)}->{refresh_plan.get('refresh_end_offset', 0)} "
        f"full_refresh_est={refresh_plan.get('estimated_full_refresh_minutes', 0)}m "
        f"apis={source_summary or '-'} latest_fetch={latest_fetch}",
        flush=True,
    )


def render_progress_bar(completed: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    clamped_completed = max(0, min(completed, total))
    filled = int(round(width * clamped_completed / total))
    return "[" + ("#" * filled) + ("-" * max(0, width - filled)) + "]"


def build_quote_progress_logger() -> callable:
    state = {
        "ok": 0,
        "error": 0,
        "missing": 0,
        "last_reported_completed": 0,
        "last_error_key": None,
        "total": 0,
    }

    def emit(event: dict) -> None:
        event_type = event.get("event")
        if event_type == "start":
            state["ok"] = 0
            state["error"] = 0
            state["missing"] = 0
            state["last_reported_completed"] = 0
            state["last_error_key"] = None
            state["total"] = int(event.get("total", 0) or 0)
            print(
                f"[live bridge][download] stage=start total={event.get('total', 0)} "
                f"minute_limit={event.get('max_requests_per_minute', 0)} "
                f"minute_windows={event.get('minute_window_count', 0)} "
                f"window_size={event.get('minute_window_size', 0)} "
                f"batch_size={event.get('batch_size', 0)} batches={event.get('batch_count', 0)} "
                f"workers={event.get('max_workers', 0)} "
                f"trade_date={event.get('trade_date')}",
                flush=True,
            )
            return
        if event_type == "minute_window_start":
            print(
                f"[live bridge][download][minute {event.get('minute_window_index', 0)}/{event.get('minute_window_count', 0)}] "
                f"stage=start requests={event.get('request_start', 0)}-{event.get('request_end', 0)} "
                f"window_symbols={event.get('minute_window_size', 0)} "
                f"trade_date={event.get('trade_date')}",
                flush=True,
            )
            return
        if event_type == "minute_window_wait":
            print(
                f"[live bridge][download][minute {event.get('minute_window_index', 0)}/{event.get('minute_window_count', 0)}] "
                f"stage=wait next_minute={event.get('next_minute_window_index', 0)}/{event.get('minute_window_count', 0)} "
                f"sleep_seconds={int(round(float(event.get('wait_seconds', 0.0) or 0.0)))}",
                flush=True,
            )
            return
        if event_type == "finish":
            total = int(event.get("total", 0) or 0)
            completed = total
            print(
                f"[live bridge][download] stage=finish {render_progress_bar(completed, total)} "
                f"{completed}/{total} "
                f"received={event.get('received', 0)} "
                f"ok={state['ok']} missing={state['missing']} error={state['error']} "
                f"minute_windows={event.get('minute_window_count', 0)} "
                f"trade_date={event.get('trade_date')}",
                flush=True,
            )
            return
        if event_type != "quote":
            return

        status = str(event.get("status") or "").strip().lower()
        if status == "ok":
            state["ok"] += 1
        elif status == "missing":
            state["missing"] += 1
        else:
            state["error"] += 1

        completed = int(event.get("index", 0) or 0)
        total = int(event.get("total", 0) or 0)
        step = max(1, total // 20) if total > 0 else 1
        detail = event.get("error")
        if detail:
            detail = str(detail).replace("\n", " ").strip()
        error_key = f"{status}:{detail or ''}"
        should_print = (
            completed == 1
            or completed >= total
            or completed - int(state["last_reported_completed"]) >= step
            or (status != "ok" and error_key != state.get("last_error_key"))
        )
        if not should_print:
            return

        state["last_reported_completed"] = completed
        if status != "ok":
            state["last_error_key"] = error_key
        percent = (completed * 100.0 / total) if total > 0 else 0.0
        suffix = f" last_error={detail}" if detail and status == "error" else ""
        print(
            f"[live bridge][download] {render_progress_bar(completed, total)} "
            f"{completed}/{total} ({percent:5.1f}%) "
            f"minute={event.get('minute_window_index', 0)}/{event.get('minute_window_count', 0)} "
            f"ok={state['ok']} missing={state['missing']} error={state['error']}{suffix}",
            flush=True,
        )

    return emit


def fetch_live_quotes(quote_client, universe: list[str], trade_date: str, progress_callback) -> pd.DataFrame:
    try:
        return quote_client.fetch_quotes(universe, trade_date=trade_date, progress_callback=progress_callback)
    except TypeError:
        return quote_client.fetch_quotes(universe, trade_date=trade_date)


def summarize_bridge_report(bridge_report: dict) -> str:
    snapshots = list(bridge_report.get("snapshots") or [])
    latest_snapshot = snapshots[-1] if snapshots else {}
    summary_parts = [
        f"mode={bridge_report.get('mode')}",
        f"written_symbols={bridge_report.get('written_symbol_count', 0)}",
    ]
    if bridge_report.get("resolved_symbol_count") is not None:
        summary_parts.append(f"resolved_symbols={bridge_report.get('resolved_symbol_count')}")
    if bridge_report.get("exact_trade_date_symbol_count") is not None:
        summary_parts.append(f"exact={bridge_report.get('exact_trade_date_symbol_count')}")
    if bridge_report.get("carry_forward_symbol_count") is not None:
        summary_parts.append(f"carry_forward={bridge_report.get('carry_forward_symbol_count')}")
    if bridge_report.get("missing_symbol_count") is not None:
        summary_parts.append(f"missing={bridge_report.get('missing_symbol_count')}")
    if latest_snapshot:
        summary_parts.append(f"rows={latest_snapshot.get('rows', 0)}")
        if latest_snapshot.get("coverage") is not None:
            summary_parts.append(f"coverage={float(latest_snapshot.get('coverage', 0.0)) * 100.0:.2f}%")
    return " ".join(summary_parts)


def run_live_bridge(config: dict, once: bool = False, quote_client=None, simulated_quote_client=None) -> int:
    poll_interval = float(
        config.get("live-price-poll-interval-seconds")
        or config.get("live-factor-poll-interval-seconds", 300)
    )
    last_trade_date = None
    last_bridge_report: dict | None = None
    cycle = 0
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))

    realtime_quote_client = quote_client
    if simulated_quote_client is None:
        simulated_quote_client = GbmSyntheticRtDailyClient(
            tushare_data_path=config["tushare-data-path"],
            batch_size=config.get("live-price-batch-size", 100),
            poll_interval_seconds=config.get("live-price-poll-interval-seconds", 180),
            lookback_days=config.get("simulated-live-price-lookback-days", 60),
            min_history_days=config.get("simulated-live-price-min-history-days", 20),
            trading_minutes_per_day=config.get("simulated-live-price-trading-minutes-per-day", 240),
            random_seed=config.get("simulated-live-price-random-seed", 20260317),
            volatility_scale=config.get("simulated-live-price-volatility-scale", 8.0),
            min_daily_volatility=config.get("simulated-live-price-min-daily-volatility", 0.80),
            jump_probability=config.get("simulated-live-price-jump-probability", 0.22),
            jump_scale=config.get("simulated-live-price-jump-scale", 0.10),
            timezone=str(config.get("timezone") or "Asia/Shanghai"),
            verbose=False,
        )

    def resolve_realtime_quote_client():
        nonlocal realtime_quote_client
        if realtime_quote_client is None:
            token = resolve_tushare_token(config)
            realtime_quote_client = TushareRtDailyClient(
                token=token,
                batch_size=config.get("live-price-batch-size", 100),
                http_url=config.get("tushare-http-url"),
                verbose=False,
                max_workers=config.get("live-price-max-workers", 1),
                max_requests_per_minute=config.get("live-price-max-requests-per-minute", 50),
            )
        return realtime_quote_client

    print("=" * 80)
    print("Barra CNE5 Live Bridge", flush=True)
    print("=" * 80)
    print("Bridge purpose     : materialize live Barra factors and realtime daily quote snapshot", flush=True)
    print("Market data mode   : auto switch between Tushare realtime and off-hours GBM simulation", flush=True)
    print("Order mode         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source mode: {config.get('factor-source-mode')}", flush=True)
    print(f"Factor output path : {config.get('factor-data-path')}", flush=True)
    print(f"External factors   : {config.get('external-factor-path') or '(not set)'}", flush=True)
    print(f"Bridge report path : {config.get('live-factor-report-file')}", flush=True)
    print(f"Price snapshot path: {config.get('live-price-snapshot-file')}", flush=True)
    print(f"Quote archive path : {config.get('daily-quote-archive-path')}", flush=True)
    print(f"Universe           : {config.get('symbols') or config.get('universe')}", flush=True)
    print("Realtime APIs      : stock=rt_k, etf=rt_etf_k; off-hours fallback=GBM synthetic daily bars", flush=True)
    print(f"Minute API cap     : {int(config.get('live-price-max-requests-per-minute', 50))} requests/minute", flush=True)
    print(f"Symbols per request: {int(config.get('live-price-batch-size', 100))}", flush=True)
    print(f"rt_k poll interval : {int(poll_interval)} seconds", flush=True)
    print(f"Price source mode  : {config.get('live-price-source-mode')}", flush=True)
    print(
        f"Sim seed/lookback  : {config.get('simulated-live-price-random-seed')}/"
        f"{config.get('simulated-live-price-lookback-days')}d "
        f"vol_scale={config.get('simulated-live-price-volatility-scale')} "
        f"vol_floor={config.get('simulated-live-price-min-daily-volatility')} "
        f"jump_p={config.get('simulated-live-price-jump-probability')} "
        f"jump_sigma={config.get('simulated-live-price-jump-scale')}",
        flush=True,
    )
    print(
        f"Refresh model      : full-universe refresh every {max(1, int(math.ceil(poll_interval / 60.0)))} minutes "
        "with carry-forward only for transient misses",
        flush=True,
    )
    print("=" * 80)

    try:
        while True:
            cycle += 1
            cycle_started_at = time.monotonic()
            now = datetime.now(timezone)
            print("-" * 80)
            print(f"[live bridge][cycle {cycle}] {now.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            trade_date = resolve_live_trade_date(config, now=now)
            print(f"[live bridge][calendar] resolved live trade_date={trade_date}", flush=True)
            market_data_context = (
                {
                    "requested_mode": "override",
                    "selected_mode": "tushare-realtime",
                    "session_state": "explicit-override",
                    "market_open": True,
                    "reason": "run_live_bridge received explicit quote_client override",
                    "evaluated_at": now.isoformat(),
                }
                if quote_client is not None
                else resolve_market_data_context(config, now=now)
            )
            print(
                f"[live bridge][market source] selected={market_data_context.get('selected_mode')} "
                f"requested={market_data_context.get('requested_mode')} "
                f"session={market_data_context.get('session_state')} "
                f"reason={market_data_context.get('reason')}",
                flush=True,
            )

            loader = BarraCNE5DataLoader(config["tushare-data-path"])
            universe = barra_cne5_factor_bridge.build_universe(loader, config, trade_date)
            previous_snapshot_payload, previous_snapshot_quotes = load_existing_live_price_snapshot(
                config["live-price-snapshot-file"]
            )
            refresh_plan = build_quote_refresh_plan(
                universe,
                int(config.get("live-price-max-requests-per-minute", 50)),
                previous_snapshot_payload,
                trade_date,
                poll_interval,
            )
            refresh_symbols = list(refresh_plan["refresh_symbols"])
            print(
                f"[live bridge][market] stage=download trade_date={trade_date} "
                f"requested_symbols={len(universe)} refresh_symbols={len(refresh_symbols)} "
                f"carry_forward={max(0, len(universe) - len(refresh_symbols))} "
                f"full_refresh_est={refresh_plan.get('estimated_full_refresh_minutes', 0)}m "
                f"symbols_per_request={config.get('live-price-batch-size', 100)}",
                flush=True,
            )
            active_quote_client = (
                quote_client
                if quote_client is not None
                else simulated_quote_client
                if market_data_context.get("selected_mode") == "gbm-simulated"
                else resolve_realtime_quote_client()
            )
            raw_quotes = fetch_live_quotes(
                active_quote_client,
                refresh_symbols,
                trade_date,
                progress_callback=build_quote_progress_logger(),
            )
            normalized_quotes = normalize_live_quotes(raw_quotes, trade_date, now)
            merged_quotes = merge_live_quotes(universe, trade_date, previous_snapshot_quotes, normalized_quotes)
            quote_client_metadata = getattr(active_quote_client, "last_fetch_metadata", {}) or {}

            archived_file = archive_live_quotes(config, normalized_quotes, trade_date)
            snapshot_payload = write_live_price_snapshot(
                config,
                trade_date,
                merged_quotes,
                now,
                metadata={
                    "refreshed_quote_count": int(len(normalized_quotes.index)),
                    "carried_forward_quote_count": max(0, int(len(merged_quotes.index)) - int(len(normalized_quotes.index))),
                    "next_refresh_offset": int(refresh_plan.get("next_refresh_offset", 0)),
                    "refresh_start_offset": int(refresh_plan.get("refresh_start_offset", 0)),
                    "refresh_end_offset": int(refresh_plan.get("refresh_end_offset", 0)),
                    "refresh_window_size": int(refresh_plan.get("refresh_count", 0)),
                    "estimated_full_refresh_minutes": int(refresh_plan.get("estimated_full_refresh_minutes", 0)),
                    "market_data_mode": market_data_context.get("selected_mode"),
                    "market_session_state": market_data_context.get("session_state"),
                    "market_data_reason": market_data_context.get("reason"),
                },
            )
            market_preview = build_live_market_preview(
                trade_date,
                len(universe),
                merged_quotes,
                market_data_context=market_data_context,
            )
            print_live_quote_flow(trade_date, normalized_quotes, merged_quotes, refresh_plan)
            print_live_market_preview(market_preview)

            if trade_date != last_trade_date or last_bridge_report is None:
                print(
                    f"[live bridge][factor] stage=materialize trade_date={trade_date} "
                    f"factor_source_mode={config.get('factor-source-mode')}",
                    flush=True,
                )
                last_bridge_report = materialize_live_factor_snapshot(config, universe, trade_date)
                print(f"[live bridge][factor] {summarize_bridge_report(last_bridge_report)}", flush=True)
                last_trade_date = trade_date
            else:
                print(
                    f"[live bridge][factor] trade_date={trade_date} already materialized; "
                    f"next_refresh_in={int(max(1.0, poll_interval))}s",
                    flush=True,
                )

            live_quote_report = {
                "status": "ok" if not merged_quotes.empty else "no_quotes",
                "requested_symbol_count": len(universe),
                "received_quote_count": int(len(merged_quotes.index)),
                "refreshed_quote_count": int(len(normalized_quotes.index)),
                "carried_forward_quote_count": max(0, int(len(merged_quotes.index)) - int(len(normalized_quotes.index))),
                "missing_quote_count": max(0, len(universe) - int(len(merged_quotes.index))),
                "refresh_window_size": int(refresh_plan.get("refresh_count", 0)),
                "refresh_start_offset": int(refresh_plan.get("refresh_start_offset", 0)),
                "refresh_end_offset": int(refresh_plan.get("refresh_end_offset", 0)),
                "next_refresh_offset": int(refresh_plan.get("next_refresh_offset", 0)),
                "estimated_full_refresh_minutes": int(refresh_plan.get("estimated_full_refresh_minutes", 0)),
                "max_requests_per_minute": int(config.get("live-price-max-requests-per-minute", 50)),
                "source_api_counts": merged_quotes["source_api"].value_counts(dropna=False).to_dict()
                if "source_api" in merged_quotes.columns and not merged_quotes.empty
                else {},
                "snapshot_quote_count": int(snapshot_payload.get("quote_count", 0)),
                "live_price_snapshot_file": config.get("live-price-snapshot-file"),
                "daily_quote_archive_file": archived_file,
                "generated_at": now.isoformat(),
                "source_mode": market_data_context.get("selected_mode"),
                "requested_source_mode": market_data_context.get("requested_mode"),
                "session_state": market_data_context.get("session_state"),
                "market_open": bool(market_data_context.get("market_open")),
                "source_reason": market_data_context.get("reason"),
                "client_metadata": quote_client_metadata,
            }
            write_live_bridge_report(
                config,
                trade_date,
                last_bridge_report or {},
                market_preview=market_preview,
                live_quote_report=live_quote_report,
            )

            if once:
                return 0
            elapsed_seconds = time.monotonic() - cycle_started_at
            wait_seconds = max(0.0, poll_interval - elapsed_seconds)
            print(
                f"[live bridge][sleep] polling again in {int(round(wait_seconds))} seconds "
                f"(cycle_elapsed={int(round(elapsed_seconds))}s)",
                flush=True,
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
    except KeyboardInterrupt:
        print("[live bridge] interrupted by user; bridge stopped cleanly", flush=True)
        return 130


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize live Barra CNE5 factor files for LEAN live-paper.")
    parser.add_argument("--config", default=str(repo_root() / "Launcher" / "config" / "config-barra-cne5-live-paper.json"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--factor-source-mode")
    parser.add_argument("--random-factor-seed", type=int)
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--factor-data-path")
    parser.add_argument("--live-factor-report-file")
    parser.add_argument("--live-price-snapshot-file")
    parser.add_argument("--daily-quote-archive-path")
    parser.add_argument("--external-factor-path")
    parser.add_argument("--universe")
    parser.add_argument("--index-code")
    parser.add_argument("--market-symbol")
    parser.add_argument("--symbols")
    parser.add_argument("--tushare-token")
    parser.add_argument("--tushare-http-url")
    parser.add_argument("--live-price-batch-size", type=int)
    parser.add_argument("--live-price-max-workers", type=int)
    parser.add_argument("--live-price-max-requests-per-minute", type=int)
    parser.add_argument("--live-price-poll-interval-seconds", type=int)
    parser.add_argument("--live-price-source-mode")
    parser.add_argument("--simulated-live-price-random-seed", type=int)
    parser.add_argument("--simulated-live-price-lookback-days", type=int)
    parser.add_argument("--simulated-live-price-min-history-days", type=int)
    parser.add_argument("--simulated-live-price-trading-minutes-per-day", type=int)
    parser.add_argument("--simulated-live-price-volatility-scale", type=float)
    parser.add_argument("--simulated-live-price-min-daily-volatility", type=float)
    parser.add_argument("--simulated-live-price-jump-probability", type=float)
    parser.add_argument("--simulated-live-price-jump-scale", type=float)
    parser.add_argument("--live-factor-poll-interval-seconds", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "factor-source-mode": args.factor_source_mode,
        "random-factor-seed": args.random_factor_seed,
        "tushare-data-path": args.tushare_data_path,
        "factor-data-path": args.factor_data_path,
        "live-factor-report-file": args.live_factor_report_file,
        "live-price-snapshot-file": args.live_price_snapshot_file,
        "daily-quote-archive-path": args.daily_quote_archive_path,
        "external-factor-path": args.external_factor_path,
        "universe": args.universe,
        "index-code": args.index_code,
        "market-symbol": args.market_symbol,
        "symbols": args.symbols,
        "tushare-token": args.tushare_token,
        "tushare-http-url": args.tushare_http_url,
        "live-price-batch-size": args.live_price_batch_size,
        "live-price-max-workers": args.live_price_max_workers,
        "live-price-max-requests-per-minute": args.live_price_max_requests_per_minute,
        "live-price-poll-interval-seconds": args.live_price_poll_interval_seconds,
        "live-price-source-mode": args.live_price_source_mode,
        "simulated-live-price-random-seed": args.simulated_live_price_random_seed,
        "simulated-live-price-lookback-days": args.simulated_live_price_lookback_days,
        "simulated-live-price-min-history-days": args.simulated_live_price_min_history_days,
        "simulated-live-price-trading-minutes-per-day": args.simulated_live_price_trading_minutes_per_day,
        "simulated-live-price-volatility-scale": args.simulated_live_price_volatility_scale,
        "simulated-live-price-min-daily-volatility": args.simulated_live_price_min_daily_volatility,
        "simulated-live-price-jump-probability": args.simulated_live_price_jump_probability,
        "simulated-live-price-jump-scale": args.simulated_live_price_jump_scale,
        "live-factor-poll-interval-seconds": args.live_factor_poll_interval_seconds,
    }
    config = load_live_bridge_config(args.config, overrides)
    return run_live_bridge(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
