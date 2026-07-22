#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
DATA_SOURCE_DIR = CURRENT_DIR.parent / "data-source" / "tushare"

import sys

for candidate in [CURRENT_DIR, DATA_SOURCE_DIR]:
    candidate_text = str(candidate)
    if candidate_text not in sys.path:
        sys.path.insert(0, candidate_text)

from barra_cne5_data_loader import BarraCNE5DataLoader
from rt_daily_downloader import GbmSyntheticRtDailyClient, TushareRtDailyClient


DEFAULT_INCLUDED_STOCK_MARKETS = ("主板", "创业板", "科创板")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug"


def default_shared_snapshot_file() -> Path:
    return repo_root() / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json"


def default_shared_report_file() -> Path:
    return repo_root() / "Results" / "shared-live-market" / "ashare-live-market-report.json"


def default_shared_archive_path() -> Path:
    return repo_root() / "Data" / "archive" / "ashare-live-market-daily-quotes"


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


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


def normalize_trade_date_value(value, fallback: str) -> str:
    if value is None:
        return fallback
    text = "".join(character for character in str(value).strip() if character.isdigit())
    return text[:8] if len(text) >= 8 else fallback


def resolve_trade_date(
    tushare_data_path: str | Path,
    market_symbol: str = "000300.SH",
    now: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> str:
    tz = ZoneInfo(str(timezone or "Asia/Shanghai"))
    current_time = now.astimezone(tz) if now else datetime.now(tz)
    today = current_time.strftime("%Y%m%d")
    window_start = (current_time - timedelta(days=14)).strftime("%Y%m%d")
    window_end = (current_time + timedelta(days=14)).strftime("%Y%m%d")
    loader = BarraCNE5DataLoader(tushare_data_path)
    trading_dates = loader.get_trading_dates(
        start_date=window_start,
        end_date=window_end,
        reference_symbol=market_symbol,
    )
    if not trading_dates:
        return today
    if today in trading_dates:
        return today
    historical_dates = [trade_date for trade_date in trading_dates if trade_date <= today]
    return historical_dates[-1] if historical_dates else trading_dates[0]


def resolve_market_data_context(
    tushare_data_path: str | Path,
    requested_mode: str = "auto",
    market_symbol: str = "000300.SH",
    now: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> dict:
    tz = ZoneInfo(str(timezone or "Asia/Shanghai"))
    current_time = now.astimezone(tz) if now else datetime.now(tz)
    today = current_time.strftime("%Y%m%d")
    requested = str(requested_mode or "auto").strip().lower()
    if requested not in {"auto", "realtime", "simulate"}:
        requested = "auto"

    loader = BarraCNE5DataLoader(tushare_data_path)
    trading_dates = loader.get_trading_dates(
        start_date=today,
        end_date=today,
        reference_symbol=market_symbol,
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

    if requested == "simulate":
        selected_mode = "gbm-simulated"
        reason = "configured simulate mode"
    elif requested == "realtime":
        selected_mode = "tushare-realtime"
        reason = "configured realtime mode"
    elif market_open:
        selected_mode = "tushare-realtime"
        reason = "A-share regular session open"
    else:
        selected_mode = "gbm-simulated"
        reason = f"A-share market closed ({session_state})"

    return {
        "requested_mode": requested,
        "selected_mode": selected_mode,
        "session_state": session_state,
        "market_open": market_open,
        "reason": reason,
        "now": current_time,
        "evaluated_at": current_time.isoformat(),
    }


def load_stock_universe(
    tushare_data_path: str | Path,
    asof_date: str,
    included_markets: tuple[str, ...] | list[str] | None = None,
    list_status: str = "L",
) -> list[str]:
    path = Path(tushare_data_path) / "stock_basic" / "data.parquet"
    if not path.exists():
        return []
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return []
    if frame.empty or "ts_code" not in frame.columns:
        return []

    data = frame.copy()
    if "list_status" in data.columns:
        data = data[data["list_status"].astype(str) == list_status]
    markets = tuple(included_markets or DEFAULT_INCLUDED_STOCK_MARKETS)
    if "market" in data.columns:
        data = data[data["market"].isin(markets)]
    if "list_date" in data.columns:
        data = data[data["list_date"].astype(str).fillna("") <= str(asof_date)]
    return sorted(data["ts_code"].dropna().astype(str).str.strip().str.upper().unique().tolist())


def load_etf_universe(
    tushare_data_path: str | Path,
    asof_date: str,
    list_status: str = "L",
) -> list[str]:
    path = Path(tushare_data_path) / "etf_basic" / "data.parquet"
    if not path.exists():
        return []
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return []
    if frame.empty or "ts_code" not in frame.columns:
        return []

    data = frame.copy()
    if "list_status" in data.columns:
        data = data[data["list_status"].astype(str) == list_status]
    if "exchange" in data.columns:
        data = data[data["exchange"].astype(str).isin({"SH", "SZ"})]
    if "list_date" in data.columns:
        list_dates = data["list_date"].astype(str).fillna("")
        data = data[(list_dates == "") | (list_dates <= str(asof_date))]
    return sorted(data["ts_code"].dropna().astype(str).str.strip().str.upper().unique().tolist())


def load_full_market_universe(tushare_data_path: str | Path, asof_date: str) -> list[str]:
    symbols = set(load_stock_universe(tushare_data_path, asof_date))
    symbols.update(load_etf_universe(tushare_data_path, asof_date))
    return sorted(symbols)


def normalize_quotes(quotes: pd.DataFrame | None, trade_date: str, fetch_timestamp: datetime) -> pd.DataFrame:
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
    generated_fetch_timestamp = fetch_timestamp.isoformat()
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


def load_snapshot(path: str | Path) -> tuple[dict, pd.DataFrame]:
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
    if frame.empty:
        return payload, empty
    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[columns].copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame["trade_date"] = frame["trade_date"].map(
        lambda value: normalize_trade_date_value(value, str(payload.get("trade_date") or ""))
    )
    for field in ["open", "high", "low", "close", "price", "pre_close", "pct_chg", "vol", "amount"]:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame["source_api"] = frame["source_api"].astype(str).where(frame["source_api"].notna(), None)
    frame["fetch_timestamp"] = frame["fetch_timestamp"].astype(str).where(frame["fetch_timestamp"].notna(), None)
    frame = frame.drop_duplicates(subset=["ts_code"], keep="last").sort_values("ts_code").reset_index(drop=True)
    return payload, frame


def merge_quotes(universe: list[str], trade_date: str, previous_quotes: pd.DataFrame | None, fresh_quotes: pd.DataFrame | None) -> pd.DataFrame:
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
        previous = previous_quotes.copy()
        previous["ts_code"] = previous["ts_code"].astype(str).str.strip().str.upper()
        previous = previous[previous["ts_code"].isin(allowed)]
        if not previous.empty:
            frames.append(previous.loc[:, columns])
    if fresh_quotes is not None and not fresh_quotes.empty:
        fresh = fresh_quotes.copy()
        fresh["ts_code"] = fresh["ts_code"].astype(str).str.strip().str.upper()
        fresh = fresh[fresh["ts_code"].isin(allowed)]
        if not fresh.empty:
            frames.append(fresh.loc[:, columns])
    if not frames:
        return pd.DataFrame(columns=columns)

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code"], keep="last")
    merged["trade_date"] = merged["trade_date"].astype(str).str.zfill(8)
    merged["trade_date"] = merged["trade_date"].where(merged["trade_date"] != "", trade_date)
    merged = merged.sort_values("ts_code").reset_index(drop=True)
    return merged.loc[:, columns]


def archive_quotes(archive_root: str | Path, quotes: pd.DataFrame, trade_date: str) -> str | None:
    if quotes is None or quotes.empty:
        return None
    root = Path(archive_root)
    root.mkdir(parents=True, exist_ok=True)
    archive_file = root / f"date={trade_date}" / "quotes.parquet"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    if archive_file.exists():
        existing = pd.read_parquet(archive_file)
        frames = [frame for frame in (existing, quotes) if frame is not None and not frame.empty]
        combined = pd.concat(frames, ignore_index=True) if frames else quotes.copy()
        combined = combined.drop_duplicates(subset=["ts_code", "fetch_timestamp"], keep="last")
        combined.to_parquet(archive_file, engine="pyarrow", index=False)
    else:
        quotes.to_parquet(archive_file, engine="pyarrow", index=False)
    return str(archive_file)


def write_snapshot(snapshot_path: str | Path, trade_date: str, quotes: pd.DataFrame, fetch_timestamp: datetime, metadata: dict | None = None) -> dict:
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ok",
        "generated_at": fetch_timestamp.isoformat(),
        "trade_date": trade_date,
        "quote_count": int(len(quotes.index)),
        "quotes": [
            {key: value if not pd.isna(value) else None for key, value in row.items()}
            for row in quotes.to_dict(orient="records")
        ],
    }
    if metadata:
        payload.update(metadata)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def write_report(report_path: str | Path, payload: dict) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_is_fresh(snapshot_payload: dict | None, refresh_interval_seconds: int, trade_date: str, selected_mode: str, now: datetime) -> bool:
    if not isinstance(snapshot_payload, dict):
        return False
    if str(snapshot_payload.get("trade_date") or "") != str(trade_date):
        return False
    if str(snapshot_payload.get("source_mode") or snapshot_payload.get("market_data_mode") or "") != str(selected_mode):
        return False
    generated_at = snapshot_payload.get("generated_at")
    if not generated_at:
        return False
    try:
        generated_time = datetime.fromisoformat(str(generated_at))
    except ValueError:
        return False
    if generated_time.tzinfo is None:
        generated_time = generated_time.replace(tzinfo=now.tzinfo)
    return (now - generated_time).total_seconds() < max(1, int(refresh_interval_seconds))


def filter_quotes(quotes: pd.DataFrame, universe: list[str]) -> pd.DataFrame:
    if quotes is None or quotes.empty:
        return pd.DataFrame(columns=list(quotes.columns) if quotes is not None else [])
    allowed = {str(symbol).strip().upper() for symbol in universe if str(symbol).strip()}
    filtered = quotes.copy()
    filtered["ts_code"] = filtered["ts_code"].astype(str).str.strip().str.upper()
    filtered = filtered[filtered["ts_code"].isin(allowed)]
    return filtered.sort_values("ts_code").reset_index(drop=True)


def create_realtime_client(config: dict):
    token = str(config.get("tushare-token") or "").strip()
    if not token:
        env_var = str(config.get("tushare-token-env-var") or "TUSHARE_TOKEN")
        token = str(os.getenv(env_var, "")).strip()
    if not token:
        try:
            import config as tushare_config  # type: ignore

            token = str(getattr(tushare_config, "TUSHARE_TOKEN", "") or "").strip()
        except Exception:
            token = ""
    if not token:
        return None
    return TushareRtDailyClient(
        token=token,
        batch_size=max(1, safe_int(config.get("shared-live-market-batch-size"), 200)),
        http_url=config.get("tushare-http-url"),
        verbose=False,
        max_workers=max(1, safe_int(config.get("shared-live-market-max-workers"), 1)),
        max_requests_per_minute=max(1, safe_int(config.get("shared-live-market-max-requests-per-minute"), 50)),
    )


def create_simulated_client(config: dict):
    return GbmSyntheticRtDailyClient(
        tushare_data_path=config["tushare-data-path"],
        batch_size=max(1, safe_int(config.get("shared-live-market-batch-size"), 200)),
        poll_interval_seconds=max(1, safe_int(config.get("shared-live-market-refresh-interval-seconds"), 60)),
        lookback_days=max(10, safe_int(config.get("simulated-live-price-lookback-days"), 60)),
        min_history_days=max(5, safe_int(config.get("simulated-live-price-min-history-days"), 20)),
        trading_minutes_per_day=max(1, safe_int(config.get("simulated-live-price-trading-minutes-per-day"), 240)),
        random_seed=safe_int(config.get("simulated-live-price-random-seed"), 20260317),
        volatility_scale=max(1.0, float(config.get("simulated-live-price-volatility-scale") or 8.0)),
        min_daily_volatility=max(0.05, float(config.get("simulated-live-price-min-daily-volatility") or 0.80)),
        jump_probability=max(0.0, min(1.0, float(config.get("simulated-live-price-jump-probability") or 0.22))),
        jump_scale=max(0.0, float(config.get("simulated-live-price-jump-scale") or 0.10)),
        timezone=str(config.get("timezone") or "Asia/Shanghai"),
        verbose=False,
    )


def fetch_quotes(client, universe: list[str], trade_date: str, progress_callback=None) -> pd.DataFrame:
    try:
        return client.fetch_quotes(universe, trade_date=trade_date, progress_callback=progress_callback)
    except TypeError:
        try:
            return client.fetch_quotes(universe, trade_date=trade_date)
        except TypeError:
            return client.fetch_quotes(universe)


def ensure_full_market_snapshot(
    config: dict,
    trade_date: str,
    market_context: dict,
    realtime_client=None,
    simulated_client=None,
    progress_callback=None,
) -> dict:
    now = market_context.get("now")
    if not isinstance(now, datetime):
        tz = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
        now = datetime.now(tz)

    selected_mode = str(market_context.get("selected_mode") or "gbm-simulated")
    snapshot_path = Path(str(config.get("shared-live-market-snapshot-file") or default_shared_snapshot_file()))
    report_path = Path(str(config.get("shared-live-market-report-file") or default_shared_report_file()))
    archive_path = Path(str(config.get("shared-live-market-archive-path") or default_shared_archive_path()))
    refresh_interval_seconds = max(1, safe_int(config.get("shared-live-market-refresh-interval-seconds"), 60))

    existing_payload, existing_quotes = load_snapshot(snapshot_path)
    if snapshot_is_fresh(existing_payload, refresh_interval_seconds, trade_date, selected_mode, now):
        return {
            "status": "cached",
            "trade_date": trade_date,
            "snapshot_payload": existing_payload,
            "fresh_quotes": pd.DataFrame(columns=list(existing_quotes.columns)),
            "quotes": existing_quotes,
            "report": existing_payload,
            "used_cached_snapshot": True,
            "universe": load_full_market_universe(config["tushare-data-path"], trade_date),
        }

    universe = load_full_market_universe(config["tushare-data-path"], trade_date)
    active_client = realtime_client if selected_mode == "tushare-realtime" and realtime_client is not None else simulated_client
    if active_client is None:
        raise RuntimeError("Missing market data client for full-market snapshot refresh.")

    raw_quotes = fetch_quotes(active_client, universe, trade_date, progress_callback=progress_callback)
    normalized_quotes = normalize_quotes(raw_quotes, trade_date, now)
    merged_quotes = merge_quotes(universe, trade_date, existing_quotes, normalized_quotes)
    quote_metadata = getattr(active_client, "last_fetch_metadata", {}) or {}
    archived_file = archive_quotes(archive_path, normalized_quotes, trade_date)
    snapshot_payload = write_snapshot(
        snapshot_path,
        trade_date,
        merged_quotes,
        now,
        metadata={
            "requested_symbol_count": len(universe),
            "received_quote_count": int(len(merged_quotes.index)),
            "refreshed_quote_count": int(len(normalized_quotes.index)),
            "carried_forward_quote_count": max(0, int(len(merged_quotes.index)) - int(len(normalized_quotes.index))),
            "missing_quote_count": max(0, len(universe) - int(len(merged_quotes.index))),
            "source_mode": selected_mode,
            "requested_source_mode": market_context.get("requested_mode"),
            "session_state": market_context.get("session_state"),
            "source_reason": market_context.get("reason"),
            "max_requests_per_minute": max(1, safe_int(config.get("shared-live-market-max-requests-per-minute"), 50)),
            "batch_size": max(1, safe_int(config.get("shared-live-market-batch-size"), 200)),
            "stock_symbol_count": sum(0 if TushareRtDailyClient.is_etf_code(symbol) else 1 for symbol in universe),
            "etf_symbol_count": sum(1 if TushareRtDailyClient.is_etf_code(symbol) else 0 for symbol in universe),
            "daily_quote_archive_file": archived_file,
            "client_metadata": quote_metadata,
        },
    )
    report = {
        "trade_date": trade_date,
        "generated_at": now.isoformat(),
        "status": "ok" if not merged_quotes.empty else "no_quotes",
        "source_mode": selected_mode,
        "requested_symbol_count": len(universe),
        "received_quote_count": int(len(merged_quotes.index)),
        "refreshed_quote_count": int(len(normalized_quotes.index)),
        "carried_forward_quote_count": max(0, int(len(merged_quotes.index)) - int(len(normalized_quotes.index))),
        "missing_quote_count": max(0, len(universe) - int(len(merged_quotes.index))),
        "stock_symbol_count": snapshot_payload.get("stock_symbol_count", 0),
        "etf_symbol_count": snapshot_payload.get("etf_symbol_count", 0),
        "snapshot_file": str(snapshot_path),
        "archive_file": archived_file,
        "session_state": market_context.get("session_state"),
        "source_reason": market_context.get("reason"),
        "client_metadata": quote_metadata,
    }
    write_report(report_path, report)
    return {
        "status": report["status"],
        "trade_date": trade_date,
        "snapshot_payload": snapshot_payload,
        "fresh_quotes": normalized_quotes,
        "quotes": merged_quotes,
        "report": report,
        "used_cached_snapshot": False,
        "universe": universe,
    }
