#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
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
from rt_daily_downloader import TushareRtDailyClient


PATH_KEYS = {
    "tushare-data-path",
    "factor-data-path",
    "live-factor-report-file",
    "live-price-snapshot-file",
    "daily-quote-archive-path",
    "external-factor-path",
}
DATA_RELATIVE_KEYS = {"factor-data-path", "daily-quote-archive-path"}
RESULTS_RELATIVE_KEYS = {"live-factor-report-file", "live-price-snapshot-file"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug"


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "factor-data-path": str(root / "Data" / "alternative" / "barra-cne5-live-factors"),
        "live-factor-report-file": str(root / "Results" / "barra-cne5-live-bridge-report.json"),
        "live-price-snapshot-file": str(root / "Results" / "barra-cne5-live-price-snapshot.json"),
        "daily-quote-archive-path": str(root / "Data" / "archive" / "barra-cne5-live-daily-quotes"),
        "factor-source-mode": "random",
        "random-factor-seed": 42,
        "external-factor-path": None,
        "universe": "csi300",
        "index-code": "000300.SH",
        "market-symbol": "000300.SH",
        "symbols": None,
        "tushare-token": "",
        "tushare-http-url": "",
        "tushare-token-env-var": "TUSHARE_TOKEN",
        "live-price-batch-size": 25,
        "live-price-max-workers": 6,
        "live-price-poll-interval-seconds": 60,
        "factor-worker-count": "auto",
        "parallel-date-block-size": 1,
        "progress-interval-symbols": 100,
        "progress-interval-files": 50,
        "live-factor-poll-interval-seconds": 60,
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
    config["live-factor-poll-interval-seconds"] = max(1, coerce_int(config.get("live-factor-poll-interval-seconds"), 60))
    config["live-price-batch-size"] = max(1, coerce_int(config.get("live-price-batch-size"), 25))
    config["live-price-max-workers"] = max(1, coerce_int(config.get("live-price-max-workers"), 6))
    config["live-price-poll-interval-seconds"] = max(
        1,
        coerce_int(config.get("live-price-poll-interval-seconds"), config["live-factor-poll-interval-seconds"]),
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
    normalized["fetch_timestamp"] = fetch_timestamp.astimezone(ZoneInfo(str(fetch_timestamp.tzinfo or "Asia/Shanghai"))).isoformat()
    normalized["source_api"] = normalized["ts_code"].map(
        lambda ts_code: "rt_etf_k" if TushareRtDailyClient.is_etf_code(ts_code) else "rt_k"
    )

    normalized = normalized.drop_duplicates(subset=["ts_code"], keep="last").sort_values("ts_code").reset_index(drop=True)
    return normalized.loc[:, columns]


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


def write_live_price_snapshot(config: dict, trade_date: str, quotes: pd.DataFrame, fetch_timestamp: datetime) -> dict:
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
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_live_market_preview(trade_date: str, requested_count: int, quotes: pd.DataFrame, sample_size: int = 6) -> dict:
    rows: list[dict[str, object]] = []
    if not quotes.empty:
        preview_rows = quotes.head(sample_size).to_dict(orient="records")
        for row in preview_rows:
            rows.append({
                "symbol": row.get("ts_code"),
                "asset_type": "etf" if row.get("source_api") == "rt_etf_k" else "equity",
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
        "rows": rows,
    }


def print_live_market_preview(preview: dict) -> None:
    rows = list(preview.get("rows") or [])
    print(
        f"[live bridge][market] trade_date={preview.get('trade_date')} "
        f"source=tushare rt_k/rt_etf_k requested={preview.get('universe_size', 0)} "
        f"received={preview.get('received_count', 0)} preview_rows={len(rows)}",
        flush=True,
    )
    if not rows:
        print("[live bridge][market] no realtime quotes received in this cycle", flush=True)
        return
    for index, row in enumerate(rows, start=1):
        print(
            f"[live bridge][daily {index}/{len(rows)}] {row.get('symbol')} "
            f"type={row.get('asset_type')} status={row.get('status')} "
            f"close={format_price(row.get('close'))} pct_chg={format_percent(row.get('pct_chg'))} "
            f"source={row.get('source_api', '-')}"
            f" turnover={format_percent(row.get('turnover_rate'))} total_mv={format_market_value(row.get('total_mv'))} "
            f"vol={format_volume(row.get('vol'))}",
            flush=True,
        )


def print_live_quote_flow(trade_date: str, quotes: pd.DataFrame) -> None:
    if quotes.empty:
        print(f"[live bridge][quote flow] trade_date={trade_date} status=empty", flush=True)
        return

    total = len(quotes.index)
    print(f"[live bridge][quote flow] trade_date={trade_date} rows={total}", flush=True)
    for index, row in enumerate(quotes.to_dict(orient="records"), start=1):
        print(
            f"[live bridge][quote {index}/{total}] {row.get('ts_code')} "
            f"api={row.get('source_api', '-')} close={format_price(row.get('close') or row.get('price'))} "
            f"pct_chg={format_percent(row.get('pct_chg'))} vol={format_volume(row.get('vol'))} "
            f"fetch={row.get('fetch_timestamp', '-')}",
            flush=True,
        )


def build_quote_progress_logger() -> callable:
    def emit(event: dict) -> None:
        event_type = event.get("event")
        if event_type == "start":
            print(
                f"[live bridge][download] stage=start total={event.get('total', 0)} "
                f"batch_size={event.get('batch_size', 0)} batches={event.get('batch_count', 0)} "
                f"workers={event.get('max_workers', 0)} "
                f"trade_date={event.get('trade_date')}",
                flush=True,
            )
            return
        if event_type == "finish":
            print(
                f"[live bridge][download] stage=finish total={event.get('total', 0)} "
                f"received={event.get('received', 0)} trade_date={event.get('trade_date')}",
                flush=True,
            )
            return
        if event_type != "quote":
            return

        close_text = format_price(event.get("close"))
        pct_text = format_percent(event.get("pct_chg"))
        detail = event.get("error")
        if detail:
            detail = str(detail).replace("\n", " ").strip()
        suffix = f" detail={detail}" if detail else ""
        print(
            f"[live bridge][download {event.get('index', 0)}/{event.get('total', 0)}] "
            f"req={event.get('request_index', '-')}"
            f" batch={event.get('batch_index', 0)}/{event.get('batch_count', 0)} "
            f"{event.get('ts_code')} api={event.get('api_name')} status={event.get('status')} "
            f"close={close_text} pct_chg={pct_text}{suffix}",
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
    if latest_snapshot:
        summary_parts.append(f"rows={latest_snapshot.get('rows', 0)}")
        if latest_snapshot.get("coverage") is not None:
            summary_parts.append(f"coverage={float(latest_snapshot.get('coverage', 0.0)) * 100.0:.2f}%")
    return " ".join(summary_parts)


def run_live_bridge(config: dict, once: bool = False, quote_client=None) -> int:
    poll_interval = float(
        config.get("live-price-poll-interval-seconds")
        or config.get("live-factor-poll-interval-seconds", 300)
    )
    last_trade_date = None
    last_bridge_report: dict | None = None
    cycle = 0
    timezone = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))

    if quote_client is None:
        token = resolve_tushare_token(config)
        quote_client = TushareRtDailyClient(
            token=token,
            batch_size=config.get("live-price-batch-size", 25),
            http_url=config.get("tushare-http-url"),
            verbose=False,
            max_workers=config.get("live-price-max-workers", 6),
        )

    print("=" * 80)
    print("Barra CNE5 Live Bridge", flush=True)
    print("=" * 80)
    print("Bridge purpose     : materialize live Barra factors and realtime daily quote snapshot", flush=True)
    print("Market data mode   : direct Tushare rt_k/rt_etf_k realtime daily bars", flush=True)
    print("Order mode         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source mode: {config.get('factor-source-mode')}", flush=True)
    print(f"Factor output path : {config.get('factor-data-path')}", flush=True)
    print(f"Bridge report path : {config.get('live-factor-report-file')}", flush=True)
    print(f"Price snapshot path: {config.get('live-price-snapshot-file')}", flush=True)
    print(f"Quote archive path : {config.get('daily-quote-archive-path')}", flush=True)
    print(f"Universe           : {config.get('symbols') or config.get('universe')}", flush=True)
    print(f"rt_k poll interval : {int(poll_interval)} seconds", flush=True)
    print("=" * 80)

    try:
        while True:
            cycle += 1
            now = datetime.now(timezone)
            print("-" * 80)
            print(f"[live bridge][cycle {cycle}] {now.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
            trade_date = resolve_live_trade_date(config, now=now)
            print(f"[live bridge][calendar] resolved live trade_date={trade_date}", flush=True)

            loader = BarraCNE5DataLoader(config["tushare-data-path"])
            universe = barra_cne5_factor_bridge.build_universe(loader, config, trade_date)
            print(
                f"[live bridge][market] stage=download trade_date={trade_date} "
                f"requested_symbols={len(universe)} batch_size={config.get('live-price-batch-size', 25)}",
                flush=True,
            )
            raw_quotes = fetch_live_quotes(
                quote_client,
                universe,
                trade_date,
                progress_callback=build_quote_progress_logger(),
            )
            normalized_quotes = normalize_live_quotes(raw_quotes, trade_date, now)

            archived_file = archive_live_quotes(config, normalized_quotes, trade_date)
            snapshot_payload = write_live_price_snapshot(config, trade_date, normalized_quotes, now)
            market_preview = build_live_market_preview(trade_date, len(universe), normalized_quotes)
            print_live_quote_flow(trade_date, normalized_quotes)
            print_live_market_preview(market_preview)

            if trade_date != last_trade_date or last_bridge_report is None:
                print(
                    f"[live bridge][factor] stage=materialize trade_date={trade_date} "
                    f"factor_source_mode={config.get('factor-source-mode')}",
                    flush=True,
                )
                bridge_config = build_bridge_factor_config(config, trade_date)
                last_bridge_report = barra_cne5_factor_bridge.run_factor_bridge(bridge_config)
                print(f"[live bridge][factor] {summarize_bridge_report(last_bridge_report)}", flush=True)
                last_trade_date = trade_date
            else:
                print(
                    f"[live bridge][factor] trade_date={trade_date} already materialized; "
                    f"next_refresh_in={int(max(1.0, poll_interval))}s",
                    flush=True,
                )

            live_quote_report = {
                "status": "ok" if not normalized_quotes.empty else "no_quotes",
                "requested_symbol_count": len(universe),
                "received_quote_count": int(len(normalized_quotes.index)),
                "missing_quote_count": max(0, len(universe) - int(len(normalized_quotes.index))),
                "source_api_counts": normalized_quotes["source_api"].value_counts(dropna=False).to_dict()
                if "source_api" in normalized_quotes.columns and not normalized_quotes.empty
                else {},
                "snapshot_quote_count": int(snapshot_payload.get("quote_count", 0)),
                "live_price_snapshot_file": config.get("live-price-snapshot-file"),
                "daily_quote_archive_file": archived_file,
                "generated_at": now.isoformat(),
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
            print(f"[live bridge][sleep] polling again in {int(max(1.0, poll_interval))} seconds", flush=True)
            time.sleep(max(1.0, poll_interval))
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
    parser.add_argument("--live-price-poll-interval-seconds", type=int)
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
        "live-price-poll-interval-seconds": args.live_price_poll_interval_seconds,
        "live-factor-poll-interval-seconds": args.live_factor_poll_interval_seconds,
    }
    config = load_live_bridge_config(args.config, overrides)
    return run_live_bridge(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
