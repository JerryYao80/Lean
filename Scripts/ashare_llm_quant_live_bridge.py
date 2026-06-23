#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
DATA_SOURCE_DIR = CURRENT_DIR.parent / "data-source" / "tushare"
for candidate in [CURRENT_DIR, DATA_SOURCE_DIR]:
    candidate_text = str(candidate)
    if candidate_text not in sys.path:
        sys.path.insert(0, candidate_text)

import config as tushare_runtime_config
import ashare_live_market_cache as live_market_cache
from rt_daily_downloader import GbmSyntheticRtDailyClient, TushareRtDailyClient


PATH_KEYS = {
    "tushare-data-path",
    "historical-feature-path",
    "feature-data-path",
    "historical-benchmark-file",
    "benchmark-file",
    "live-bridge-report-file",
    "live-price-snapshot-file",
    "daily-quote-archive-path",
    "shared-live-market-snapshot-file",
    "shared-live-market-report-file",
    "shared-live-market-archive-path",
}
DATA_RELATIVE_KEYS = {
    "historical-feature-path",
    "feature-data-path",
    "historical-benchmark-file",
    "benchmark-file",
    "daily-quote-archive-path",
    "shared-live-market-archive-path",
}
RESULTS_RELATIVE_KEYS = {
    "live-bridge-report-file",
    "live-price-snapshot-file",
    "shared-live-market-snapshot-file",
    "shared-live-market-report-file",
}

FEATURE_COLUMNS = [
    "trade_date",
    "close",
    "pct_chg",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "turnover_rate_f",
    "circ_mv",
    "momentum_120_20",
    "return_5",
    "volatility_20",
    "flow_ratio",
    "big_flow_ratio",
    "in_universe",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug"


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "historical-feature-path": str(root / "Data" / "alternative" / "ashare-llm-quant-features"),
        "feature-data-path": str(root / "Data" / "alternative" / "ashare-llm-quant-live-features"),
        "historical-benchmark-file": str(root / "Data" / "alternative" / "ashare-llm-quant-features" / "benchmark" / "000300.SH.csv"),
        "benchmark-file": str(root / "Data" / "alternative" / "ashare-llm-quant-live-features" / "benchmark" / "000300.SH.csv"),
        "live-bridge-report-file": str(root / "Results" / "ashare-llm-quant-live-bridge-report.json"),
        "live-price-snapshot-file": str(root / "Results" / "ashare-llm-quant-live-price-snapshot.json"),
        "daily-quote-archive-path": str(root / "Data" / "archive" / "ashare-llm-quant-live-daily-quotes"),
        "shared-live-market-snapshot-file": str(live_market_cache.default_shared_snapshot_file()),
        "shared-live-market-report-file": str(live_market_cache.default_shared_report_file()),
        "shared-live-market-archive-path": str(live_market_cache.default_shared_archive_path()),
        "market-symbol": "000300.SH",
        "tushare-token": "",
        "tushare-http-url": "",
        "tushare-token-env-var": "TUSHARE_TOKEN",
        "live-price-source-mode": "auto",
        "live-price-batch-size": 100,
        "live-price-max-workers": 1,
        "live-price-max-requests-per-minute": 50,
        "live-factor-poll-interval-seconds": 60,
        "live-price-refresh-interval-seconds": 60,
        "shared-live-market-refresh-interval-seconds": 60,
        "shared-live-market-batch-size": 200,
        "shared-live-market-max-workers": 1,
        "shared-live-market-max-requests-per-minute": 50,
        "simulated-live-price-random-seed": 20260317,
        "simulated-live-price-lookback-days": 60,
        "simulated-live-price-min-history-days": 20,
        "simulated-live-price-trading-minutes-per-day": 240,
        "simulated-live-price-volatility-scale": 8.0,
        "simulated-live-price-min-daily-volatility": 0.80,
        "simulated-live-price-jump-probability": 0.22,
        "simulated-live-price-jump-scale": 0.10,
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
    try:
        return int(str(value))
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
            if value is not None:
                config[key] = value

    config = resolve_config_paths(config, repo_root())
    config["live-price-batch-size"] = max(1, coerce_int(config.get("live-price-batch-size"), 100))
    config["live-price-max-workers"] = max(1, coerce_int(config.get("live-price-max-workers"), 1))
    config["live-price-max-requests-per-minute"] = max(1, coerce_int(config.get("live-price-max-requests-per-minute"), 50))
    config["live-factor-poll-interval-seconds"] = max(1, coerce_int(config.get("live-factor-poll-interval-seconds"), 60))
    config["live-price-refresh-interval-seconds"] = max(1, coerce_int(config.get("live-price-refresh-interval-seconds"), 60))
    config["shared-live-market-refresh-interval-seconds"] = max(1, coerce_int(config.get("shared-live-market-refresh-interval-seconds"), 60))
    config["shared-live-market-batch-size"] = max(1, coerce_int(config.get("shared-live-market-batch-size"), 200))
    config["shared-live-market-max-workers"] = max(1, coerce_int(config.get("shared-live-market-max-workers"), 1))
    config["shared-live-market-max-requests-per-minute"] = max(1, coerce_int(config.get("shared-live-market-max-requests-per-minute"), 50))
    config["simulated-live-price-random-seed"] = coerce_int(config.get("simulated-live-price-random-seed"), 20260317)
    config["simulated-live-price-lookback-days"] = max(10, coerce_int(config.get("simulated-live-price-lookback-days"), 60))
    config["simulated-live-price-min-history-days"] = max(5, coerce_int(config.get("simulated-live-price-min-history-days"), 20))
    config["simulated-live-price-trading-minutes-per-day"] = max(1, coerce_int(config.get("simulated-live-price-trading-minutes-per-day"), 240))
    config["simulated-live-price-volatility-scale"] = max(1.0, float(config.get("simulated-live-price-volatility-scale") or 8.0))
    config["simulated-live-price-min-daily-volatility"] = max(0.05, float(config.get("simulated-live-price-min-daily-volatility") or 0.80))
    config["simulated-live-price-jump-probability"] = max(0.0, min(1.0, float(config.get("simulated-live-price-jump-probability") or 0.22)))
    config["simulated-live-price-jump-scale"] = max(0.0, float(config.get("simulated-live-price-jump-scale") or 0.10))
    return config


def resolve_tushare_token(config: dict) -> str:
    configured_token = str(config.get("tushare-token") or "").strip()
    if configured_token:
        return configured_token
    env_var = str(config.get("tushare-token-env-var") or "TUSHARE_TOKEN")
    env_token = os.getenv(env_var, "").strip()
    if env_token:
        return env_token
    fallback = str(getattr(tushare_runtime_config, "TUSHARE_TOKEN", "") or "").strip()
    return fallback


def ts_code_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = str(ts_code).strip().upper().split(".", 1)
    return ticker, "sse" if suffix == "SH" else "szse"


def feature_csv_path(feature_root: str | Path, ts_code: str) -> Path:
    ticker, market = ts_code_parts(ts_code)
    return Path(feature_root) / market / "daily" / f"{ticker}.csv"


def discover_universe(feature_root: str | Path) -> list[str]:
    root = Path(feature_root)
    symbols: list[str] = []
    for market_dir, suffix in [("sse", "SH"), ("szse", "SZ")]:
        daily_dir = root / market_dir / "daily"
        if not daily_dir.exists():
            continue
        for path in sorted(daily_dir.glob("*.csv")):
            ticker = path.stem.strip()
            if ticker:
                symbols.append(f"{ticker}.{suffix}")
    return symbols


def read_trade_dates_from_benchmark(path: Path) -> list[str]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, usecols=["trade_date"])
    return sorted(frame["trade_date"].astype(str).str.zfill(8).dropna().unique().tolist())


def resolve_live_trade_date(config: dict, now: datetime | None = None) -> str:
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current_time = now.astimezone(timezone) if now else datetime.now(timezone)
    today = current_time.strftime("%Y%m%d")
    trade_dates = read_trade_dates_from_benchmark(Path(config["historical-benchmark-file"]))
    if not trade_dates:
        return today
    if today in set(trade_dates):
        return today
    historical_dates = [trade_date for trade_date in trade_dates if trade_date <= today]
    return historical_dates[-1] if historical_dates else trade_dates[0]


def is_ashare_session(current_time: datetime) -> bool:
    local_time = current_time.timetz().replace(tzinfo=None)
    morning = dt_time(9, 30) <= local_time < dt_time(11, 30)
    afternoon = dt_time(13, 0) <= local_time < dt_time(15, 0)
    return morning or afternoon


def resolve_market_data_context(config: dict, now: datetime | None = None) -> dict:
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current_time = now.astimezone(timezone) if now else datetime.now(timezone)
    requested_mode = str(config.get("live-price-source-mode") or "auto").strip().lower()
    if requested_mode not in {"auto", "realtime", "simulate"}:
        requested_mode = "auto"

    trade_dates = set(read_trade_dates_from_benchmark(Path(config["historical-benchmark-file"])))
    today = current_time.strftime("%Y%m%d")
    in_session = today in trade_dates and is_ashare_session(current_time)

    if requested_mode == "realtime":
        selected_mode = "tushare-realtime"
    elif requested_mode == "simulate":
        selected_mode = "gbm-simulated"
    else:
        selected_mode = "tushare-realtime" if in_session else "gbm-simulated"

    return {
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "is_trading_day": today in trade_dates,
        "is_session_open": in_session,
        "now": current_time,
    }


def safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return None if numeric != numeric else numeric


@dataclass
class HistoricalFeatureStore:
    feature_root: Path
    benchmark_path: Path
    _feature_cache: dict[str, pd.DataFrame] | None = None
    _benchmark_cache: pd.DataFrame | None = None

    def feature_frame(self, ts_code: str) -> pd.DataFrame:
        if self._feature_cache is None:
            self._feature_cache = {}
        cached = self._feature_cache.get(ts_code)
        if cached is not None:
            return cached.copy()
        path = feature_csv_path(self.feature_root, ts_code)
        frame = pd.read_csv(path)
        frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
        self._feature_cache[ts_code] = frame
        return frame.copy()

    def benchmark_frame(self) -> pd.DataFrame:
        if self._benchmark_cache is None:
            frame = pd.read_csv(self.benchmark_path)
            frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
            self._benchmark_cache = frame
        return self._benchmark_cache.copy()


def update_scaled_value(value, scale: float) -> float | None:
    numeric = safe_float(value)
    return None if numeric is None else numeric * scale


def inverse_scaled_value(value, scale: float) -> float | None:
    numeric = safe_float(value)
    if numeric is None or scale == 0.0:
        return numeric
    return numeric / scale


def build_live_feature_row(history: pd.DataFrame, quote: dict[str, object], trade_date: str) -> dict[str, object]:
    frame = history.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
    frame = frame[frame["trade_date"] <= trade_date].copy()
    frame = frame.tail(160).reset_index(drop=True)
    if frame.empty:
        raise ValueError("Historical feature frame is empty.")

    last_row = frame.iloc[-1].to_dict()
    if str(last_row.get("trade_date") or "") == trade_date:
        frame = frame.iloc[:-1].copy()
        last_row = (frame.iloc[-1].to_dict() if not frame.empty else last_row)

    baseline_close = max(0.01, safe_float(last_row.get("close")) or 0.01)
    close = safe_float(quote.get("close")) or safe_float(quote.get("price")) or baseline_close
    pre_close = safe_float(quote.get("pre_close")) or baseline_close
    pct_chg = safe_float(quote.get("pct_chg"))
    if pct_chg is None and pre_close not in (None, 0.0):
        pct_chg = (close / pre_close - 1.0) * 100.0
    if pct_chg is None:
        pct_chg = safe_float(last_row.get("pct_chg")) or 0.0

    appended = pd.concat(
        [
            frame.loc[:, ["trade_date", "close", "pct_chg"]],
            pd.DataFrame([{"trade_date": trade_date, "close": close, "pct_chg": pct_chg}]),
        ],
        ignore_index=True,
    )
    appended["close"] = pd.to_numeric(appended["close"], errors="coerce")
    appended["pct_chg"] = pd.to_numeric(appended["pct_chg"], errors="coerce")
    appended["momentum_120_20"] = appended["close"].shift(20) / appended["close"].shift(120) - 1.0
    appended["return_5"] = appended["close"].pct_change(5)
    appended["volatility_20"] = appended["pct_chg"].div(100.0).rolling(20).std()
    metrics = appended.iloc[-1].to_dict()

    scale = close / baseline_close if baseline_close > 0 else 1.0
    return {
        "trade_date": trade_date,
        "close": close,
        "pct_chg": pct_chg,
        "pb": update_scaled_value(last_row.get("pb"), scale),
        "ps_ttm": update_scaled_value(last_row.get("ps_ttm"), scale),
        "dv_ttm": inverse_scaled_value(last_row.get("dv_ttm"), scale),
        "turnover_rate_f": safe_float(last_row.get("turnover_rate_f")),
        "circ_mv": update_scaled_value(last_row.get("circ_mv"), scale),
        "momentum_120_20": safe_float(metrics.get("momentum_120_20")),
        "return_5": safe_float(metrics.get("return_5")),
        "volatility_20": safe_float(metrics.get("volatility_20")),
        "flow_ratio": safe_float(last_row.get("flow_ratio")),
        "big_flow_ratio": safe_float(last_row.get("big_flow_ratio")),
        "in_universe": int(round(safe_float(last_row.get("in_universe")) or 0.0)),
    }


def build_live_benchmark_frame(history: pd.DataFrame, quote: dict[str, object], trade_date: str) -> pd.DataFrame:
    frame = history.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
    frame = frame[frame["trade_date"] <= trade_date].copy()
    if not frame.empty and str(frame.iloc[-1]["trade_date"]) == trade_date:
        frame = frame.iloc[:-1].copy()
    if frame.empty:
        raise ValueError("Historical benchmark frame is empty.")

    last_close = max(0.01, safe_float(frame.iloc[-1]["close"]) or 0.01)
    close = safe_float(quote.get("close")) or safe_float(quote.get("price")) or last_close
    pre_close = safe_float(quote.get("pre_close")) or last_close
    pct_chg = safe_float(quote.get("pct_chg"))
    if pct_chg is None and pre_close not in (None, 0.0):
        pct_chg = (close / pre_close - 1.0) * 100.0
    if pct_chg is None:
        pct_chg = 0.0

    frame = pd.concat(
        [
            frame.loc[:, ["trade_date", "close", "pct_chg"]],
            pd.DataFrame([{"trade_date": trade_date, "close": close, "pct_chg": pct_chg}]),
        ],
        ignore_index=True,
    )
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["pct_chg"] = pd.to_numeric(frame["pct_chg"], errors="coerce")
    frame["ma_window"] = frame["close"].rolling(120).mean()
    frame["momentum_window"] = frame["close"].pct_change(60)
    return frame


def write_feature_file(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([[row.get(column) for column in FEATURE_COLUMNS]], columns=FEATURE_COLUMNS).to_csv(
        path,
        index=False,
        float_format="%.10f",
    )


def write_snapshot_file(path: Path, generated_at: str, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at,
        "quotes": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def archive_quotes(path: Path, trade_date: str, quotes: pd.DataFrame) -> None:
    path.mkdir(parents=True, exist_ok=True)
    archive_path = path / f"{trade_date}.csv"
    quotes.to_csv(archive_path, index=False, float_format="%.10f")


def synthetic_session_timestamp(trade_date: str, now: datetime, timezone: ZoneInfo) -> str:
    session_minutes = list(range(9 * 60 + 30, 11 * 60 + 30)) + list(range(13 * 60, 15 * 60))
    minute_index = (now.hour * 60 + now.minute) % len(session_minutes)
    target_minutes = session_minutes[minute_index]
    hour = target_minutes // 60
    minute = target_minutes % 60
    mapped = datetime(
        int(trade_date[0:4]),
        int(trade_date[4:6]),
        int(trade_date[6:8]),
        hour,
        minute,
        tzinfo=timezone,
    )
    return mapped.isoformat()


def normalize_quote_records(
    quotes: pd.DataFrame,
    trade_date: str,
    selected_mode: str,
    now: datetime,
    timezone: ZoneInfo,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    synthetic_timestamp = synthetic_session_timestamp(trade_date, now, timezone)
    for row in quotes.to_dict(orient="records"):
        normalized = {str(key): row.get(key) for key in row.keys()}
        normalized["ts_code"] = str(normalized.get("ts_code") or "").strip().upper()
        normalized["trade_date"] = trade_date
        if selected_mode == "gbm-simulated":
            normalized["fetch_timestamp"] = synthetic_timestamp
        elif not normalized.get("fetch_timestamp"):
            normalized["fetch_timestamp"] = now.isoformat()
        rows.append(normalized)
    return rows


def materialize_live_snapshot(config: dict, trade_date: str, market_context: dict, store: HistoricalFeatureStore, realtime_client, simulated_client) -> dict:
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current_time = market_context["now"]
    universe = discover_universe(config["historical-feature-path"])
    shared_snapshot = live_market_cache.ensure_full_market_snapshot(
        config,
        trade_date,
        market_context,
        realtime_client=realtime_client,
        simulated_client=simulated_client,
    )
    selected_mode = str((shared_snapshot.get("report") or {}).get("source_mode") or market_context["selected_mode"])
    metadata = dict((shared_snapshot.get("report") or {}).get("client_metadata") or {})
    filtered_quotes = live_market_cache.filter_quotes(shared_snapshot.get("quotes"), universe)
    filtered_fresh_quotes = live_market_cache.filter_quotes(shared_snapshot.get("fresh_quotes"), universe)
    normalized_quote_rows = normalize_quote_records(filtered_quotes, trade_date, selected_mode, current_time, timezone)
    quote_lookup = {
        str(row.get("ts_code") or "").strip().upper(): row
        for row in normalized_quote_rows
        if str(row.get("ts_code") or "").strip()
    }

    live_rows: list[dict[str, object]] = []
    snapshot_rows: list[dict[str, object]] = []
    carry_forward_count = 0
    for ts_code in universe:
        history = store.feature_frame(ts_code)
        quote_row = quote_lookup.get(ts_code)
        if quote_row is None:
            carry_forward_count += 1
            baseline = history.copy()
            baseline["trade_date"] = baseline["trade_date"].astype(str).str.zfill(8)
            baseline = baseline[baseline["trade_date"] <= trade_date]
            latest = baseline.iloc[-1].to_dict() if not baseline.empty else history.iloc[-1].to_dict()
            quote_row = {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "close": latest.get("close"),
                "price": latest.get("close"),
                "pre_close": latest.get("close"),
                "pct_chg": latest.get("pct_chg"),
                "vol": 0.0,
                "amount": 0.0,
                "fetch_timestamp": synthetic_session_timestamp(trade_date, current_time, timezone),
            }

        feature_row = build_live_feature_row(history, quote_row, trade_date)
        write_feature_file(feature_csv_path(config["feature-data-path"], ts_code), feature_row)
        live_rows.append({"symbol": ts_code, **feature_row})
        snapshot_rows.append({
            "ts_code": ts_code,
            "trade_date": trade_date,
            "open": quote_row.get("open"),
            "high": quote_row.get("high"),
            "low": quote_row.get("low"),
            "close": quote_row.get("close"),
            "price": quote_row.get("price"),
            "pre_close": quote_row.get("pre_close"),
            "pct_chg": quote_row.get("pct_chg"),
            "vol": quote_row.get("vol"),
            "amount": quote_row.get("amount"),
            "fetch_timestamp": quote_row.get("fetch_timestamp"),
        })

    benchmark_history = store.benchmark_frame()
    benchmark_quote = quote_lookup.get(str(config.get("market-symbol") or "000300.SH")) or {
        "ts_code": str(config.get("market-symbol") or "000300.SH"),
        "trade_date": trade_date,
        "close": benchmark_history.iloc[-1]["close"],
        "price": benchmark_history.iloc[-1]["close"],
        "pre_close": benchmark_history.iloc[-1]["close"],
        "pct_chg": benchmark_history.iloc[-1]["pct_chg"],
        "fetch_timestamp": synthetic_session_timestamp(trade_date, current_time, timezone),
    }
    live_benchmark = build_live_benchmark_frame(benchmark_history, benchmark_quote, trade_date)
    benchmark_path = Path(config["benchmark-file"])
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    live_benchmark.to_csv(benchmark_path, index=False, float_format="%.10f")

    generated_at = current_time.isoformat()
    write_snapshot_file(Path(config["live-price-snapshot-file"]), generated_at, snapshot_rows)
    archive_quotes(Path(config["daily-quote-archive-path"]), trade_date, pd.DataFrame(snapshot_rows))

    report = {
        "trade_date": trade_date,
        "generated_at": generated_at,
        "bridge_report": {
            "mode": selected_mode,
            "historical_feature_path": str(config["historical-feature-path"]),
            "feature_data_path": str(config["feature-data-path"]),
            "benchmark_file": str(config["benchmark-file"]),
            "resolved_symbol_count": len(universe),
            "written_symbol_count": len(live_rows),
            "carry_forward_symbol_count": carry_forward_count,
            "missing_symbol_count": max(0, len(universe) - len(live_rows)),
        },
        "live_quote_report": {
            "requested_symbol_count": len(universe),
            "received_quote_count": len(normalized_quote_rows),
            "refreshed_quote_count": int(len(filtered_fresh_quotes.index)),
            "carried_forward_quote_count": carry_forward_count,
            "generated_at": generated_at,
            "source_mode": selected_mode,
            "batch_count": metadata.get("batch_count"),
            "minute_window_count": metadata.get("minute_window_count"),
            "max_requests_per_minute": config["live-price-max-requests-per-minute"],
            "shared_market_requested_symbol_count": int(((shared_snapshot.get("snapshot_payload") or {}).get("requested_symbol_count") or 0)),
            "shared_market_received_quote_count": int(((shared_snapshot.get("snapshot_payload") or {}).get("received_quote_count") or 0)),
            "shared_market_refreshed_quote_count": int(((shared_snapshot.get("snapshot_payload") or {}).get("refreshed_quote_count") or 0)),
            "shared_market_cached": bool(shared_snapshot.get("used_cached_snapshot")),
            "shared_live_market_snapshot_file": config.get("shared-live-market-snapshot-file"),
        },
        "market_preview": {
            "trade_date": trade_date,
            "rows": snapshot_rows[:4],
        },
    }
    Path(config["live-bridge-report-file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["live-bridge-report-file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_clients(config: dict):
    token = resolve_tushare_token(config)
    realtime_client = None
    if token:
        realtime_client = live_market_cache.create_realtime_client(config)
    simulated_client = live_market_cache.create_simulated_client(config)
    return realtime_client, simulated_client


def print_cycle_summary(report: dict) -> None:
    bridge_report = report.get("bridge_report") or {}
    quote_report = report.get("live_quote_report") or {}
    print(
        f"[live bridge] {report.get('generated_at', '-')} "
        f"trade_date={report.get('trade_date', '-')} "
        f"mode={bridge_report.get('mode', '-')} "
        f"quotes={quote_report.get('received_quote_count', 0)}/{quote_report.get('requested_symbol_count', 0)} "
        f"carry_forward={quote_report.get('carried_forward_quote_count', 0)} "
        f"features={bridge_report.get('written_symbol_count', 0)}/{bridge_report.get('resolved_symbol_count', 0)}",
        flush=True,
    )


def run_live_bridge(config: dict, once: bool = False) -> int:
    realtime_client, simulated_client = build_clients(config)
    store = HistoricalFeatureStore(
        feature_root=Path(config["historical-feature-path"]),
        benchmark_path=Path(config["historical-benchmark-file"]),
    )

    while True:
        market_context = resolve_market_data_context(config)
        trade_date = resolve_live_trade_date(config, now=market_context["now"])
        report = materialize_live_snapshot(config, trade_date, market_context, store, realtime_client, simulated_client)
        print_cycle_summary(report)
        if once:
            return 0
        time.sleep(float(config["live-factor-poll-interval-seconds"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize live A-share LLM quant feature files for LEAN live-paper.")
    parser.add_argument("--config")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_live_bridge_config(args.config)
    return run_live_bridge(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
