#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

CURRENT_DIR = Path(__file__).resolve().parent
DATA_SOURCE_DIR = CURRENT_DIR.parent / "data-source" / "tushare"
for candidate in [CURRENT_DIR, DATA_SOURCE_DIR]:
    candidate_text = str(candidate)
    if candidate_text not in sys.path:
        sys.path.insert(0, candidate_text)

import barra_cne5_factor_bridge
from barra_cne5_data_loader import BarraCNE5DataLoader


PATH_KEYS = {
    "tushare-data-path",
    "factor-data-path",
    "live-factor-report-file",
    "external-factor-path",
}
DATA_RELATIVE_KEYS = {"factor-data-path"}
RESULTS_RELATIVE_KEYS = {"live-factor-report-file"}


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
        "factor-source-mode": "random",
        "random-factor-seed": 42,
        "external-factor-path": None,
        "universe": "csi300",
        "index-code": "000300.SH",
        "market-symbol": "000300.SH",
        "symbols": None,
        "factor-worker-count": "auto",
        "parallel-date-block-size": 1,
        "progress-interval-symbols": 100,
        "progress-interval-files": 50,
        "live-factor-poll-interval-seconds": 300,
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
    config["live-factor-poll-interval-seconds"] = max(1, coerce_int(config.get("live-factor-poll-interval-seconds"), 300))
    return config


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


def write_live_bridge_report(config: dict, trade_date: str, bridge_report: dict) -> None:
    report_path = Path(config["live-factor-report-file"])
    payload = {
        "status": "ok",
        "generated_at": datetime.now(ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))).isoformat(),
        "trade_date": trade_date,
        "factor_data_path": config["factor-data-path"],
        "bridge_report": bridge_report,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_live_bridge(config: dict, once: bool = False) -> int:
    poll_interval = float(config.get("live-factor-poll-interval-seconds", 300))
    last_trade_date = None

    print("=" * 80)
    print("Barra CNE5 Live Bridge", flush=True)
    print("=" * 80)
    print("Bridge purpose     : materialize today's Barra factor snapshot for live-paper", flush=True)
    print("Market data mode   : live-paper consumes Tushare realtime daily bars", flush=True)
    print("Order mode         : synthetic internal execution only; no real Lean orders", flush=True)
    print(f"Factor source mode: {config.get('factor-source-mode')}", flush=True)
    print(f"Factor output path : {config.get('factor-data-path')}", flush=True)
    print(f"Bridge report path : {config.get('live-factor-report-file')}", flush=True)
    print(f"Universe           : {config.get('symbols') or config.get('universe')}", flush=True)
    print(f"Poll interval      : {int(poll_interval)} seconds", flush=True)
    print("=" * 80)

    try:
        while True:
            trade_date = resolve_live_trade_date(config)
            if trade_date != last_trade_date:
                print(f"[live bridge] materializing factor snapshot for {trade_date}", flush=True)
                bridge_config = build_bridge_factor_config(config, trade_date)
                bridge_report = barra_cne5_factor_bridge.run_factor_bridge(bridge_config)
                write_live_bridge_report(config, trade_date, bridge_report)
                print(
                    f"[live bridge] trade_date={trade_date} mode={bridge_report.get('mode')} "
                    f"written_symbols={bridge_report.get('written_symbol_count', 0)}",
                    flush=True,
                )
                last_trade_date = trade_date
            else:
                print(f"[live bridge] trade_date={trade_date} already materialized; sleeping", flush=True)

            if once:
                return 0
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
    parser.add_argument("--external-factor-path")
    parser.add_argument("--universe")
    parser.add_argument("--index-code")
    parser.add_argument("--market-symbol")
    parser.add_argument("--symbols")
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
        "external-factor-path": args.external_factor_path,
        "universe": args.universe,
        "index-code": args.index_code,
        "market-symbol": args.market_symbol,
        "symbols": args.symbols,
        "live-factor-poll-interval-seconds": args.live_factor_poll_interval_seconds,
    }
    config = load_live_bridge_config(args.config, overrides)
    return run_live_bridge(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
