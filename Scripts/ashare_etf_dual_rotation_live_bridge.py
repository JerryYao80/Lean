#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import ashare_etf_dual_rotation_pipeline as dual_pipeline
import ashare_live_market_cache as live_market_cache


PATH_KEYS = {
    "tushare-data-path",
    "dataset-catalog",
    "pipeline-config",
    "plan-directory",
    "plan-file",
    "benchmark-file",
    "daily-nav-file",
    "rebalance-file",
    "summary-file",
    "live-bridge-report-file",
    "shared-live-market-snapshot-file",
    "shared-live-market-report-file",
    "shared-live-market-archive-path",
}
DATA_RELATIVE_KEYS = {
    "plan-directory",
    "plan-file",
    "benchmark-file",
    "tushare-data-path",
    "shared-live-market-archive-path",
}
RESULTS_RELATIVE_KEYS = {
    "daily-nav-file",
    "rebalance-file",
    "summary-file",
    "live-bridge-report-file",
    "shared-live-market-snapshot-file",
    "shared-live-market-report-file",
}
CONFIG_RELATIVE_KEYS = {
    "dataset-catalog",
    "pipeline-config",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug"


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-etf-dual-rotation-live-paper.json"


def default_config() -> dict:
    root = repo_root()
    plan_root = root / "Data" / "alternative" / "ashare-etf-dual-rotation-live"
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "pipeline-config": str(root / "Launcher" / "config" / "config-ashare-etf-dual-rotation-pipeline.json"),
        "plan-directory": str(plan_root),
        "plan-file": str(plan_root / "dual_rotation.csv"),
        "benchmark-file": str(plan_root / "benchmark" / "000300.SH.csv"),
        "daily-nav-file": str(root / "Results" / "ashare-etf-dual-rotation-live-daily.csv"),
        "rebalance-file": str(root / "Results" / "ashare-etf-dual-rotation-live-rebalances.csv"),
        "summary-file": str(root / "Results" / "ashare-etf-dual-rotation-live-summary.json"),
        "live-bridge-report-file": str(root / "Results" / "ashare-etf-dual-rotation-live-bridge-report.json"),
        "shared-live-market-snapshot-file": str(live_market_cache.default_shared_snapshot_file()),
        "shared-live-market-report-file": str(live_market_cache.default_shared_report_file()),
        "shared-live-market-archive-path": str(live_market_cache.default_shared_archive_path()),
        "variant-name": "dual_rotation",
        "benchmark-symbol": "000300.SH",
        "market-symbol": "000300.SH",
        "start-date": "20200101",
        "live-price-source-mode": "auto",
        "live-plan-poll-interval-seconds": 180,
        "live-history-lookback-days": 260,
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
        "tushare-token": "c735900235cd005d4a32c7fad8ef9bec4dbec1e4df8030d49f3c050a53bf",
        "tushare-http-url": "http://106.54.191.157:5000",
        "tushare-token-env-var": "TUSHARE_TOKEN",
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
        path = Path(str(data_folder))
        data_base = path if path.is_absolute() else (launcher_base / path).resolve()

    results_folder = config_payload.get("results-destination-folder")
    if results_folder:
        path = Path(str(results_folder))
        results_base = path if path.is_absolute() else (launcher_base / path).resolve()

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
        elif key in CONFIG_RELATIVE_KEYS:
            resolved[key] = str((launcher_base / path).resolve())
        else:
            resolved[key] = str((config_root / path).resolve())
    return resolved


def safe_int(value, default: int) -> int:
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
    config["live-plan-poll-interval-seconds"] = max(30, safe_int(config.get("live-plan-poll-interval-seconds"), 180))
    config["live-history-lookback-days"] = max(60, safe_int(config.get("live-history-lookback-days"), 260))
    config["shared-live-market-refresh-interval-seconds"] = max(1, safe_int(config.get("shared-live-market-refresh-interval-seconds"), 60))
    config["shared-live-market-batch-size"] = max(1, safe_int(config.get("shared-live-market-batch-size"), 200))
    config["shared-live-market-max-workers"] = max(1, safe_int(config.get("shared-live-market-max-workers"), 1))
    config["shared-live-market-max-requests-per-minute"] = max(1, safe_int(config.get("shared-live-market-max-requests-per-minute"), 50))
    return config


def write_bridge_report(path: str | Path, payload: dict) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_pipeline_config(config: dict, trade_date: str) -> dict:
    lookback_days = int(config.get("live-history-lookback-days", 260))
    trade_dt = datetime.strptime(str(trade_date), "%Y%m%d")
    start_date = max(
        str(config.get("start-date") or ""),
        (trade_dt - timedelta(days=lookback_days)).strftime("%Y%m%d"),
    )
    overrides = {
        "tushare-data-path": config["tushare-data-path"],
        "dataset-catalog": config["dataset-catalog"],
        "start-date": start_date,
        "end-date": trade_date,
        "variant-name": config["variant-name"],
        "plan-directory": config["plan-directory"],
        "benchmark-file": config["benchmark-file"],
        "daily-nav-file": config["daily-nav-file"],
        "rebalance-file": config["rebalance-file"],
        "summary-file": config["summary-file"],
        "live-price-snapshot-file": config["shared-live-market-snapshot-file"],
        "live-trade-date": trade_date,
        "append-live-trade-date": True,
    }
    return dual_pipeline.load_pipeline_config(config.get("pipeline-config"), overrides)


def refresh_live_plan(config: dict, realtime_client=None, simulated_client=None) -> dict:
    market_context = live_market_cache.resolve_market_data_context(
        config["tushare-data-path"],
        requested_mode=config.get("live-price-source-mode", "auto"),
        market_symbol=config.get("market-symbol", "000300.SH"),
        timezone=config.get("timezone", "Asia/Shanghai"),
    )
    trade_date = live_market_cache.resolve_trade_date(
        config["tushare-data-path"],
        market_symbol=config.get("market-symbol", "000300.SH"),
        timezone=config.get("timezone", "Asia/Shanghai"),
    )
    market_result = live_market_cache.ensure_full_market_snapshot(
        config,
        trade_date,
        market_context,
        realtime_client=realtime_client,
        simulated_client=simulated_client,
    )
    pipeline_report = dual_pipeline.run_pipeline(build_pipeline_config(config, trade_date))
    plan_file = Path(config["plan-file"])
    variant_summary = dict((pipeline_report.get("summary_by_variant") or {}).get(config["variant-name"]) or {})
    bridge_report = {
        "status": "ok" if plan_file.exists() else "missing-plan",
        "generated_at": datetime.now().astimezone().isoformat(),
        "trade_date": trade_date,
        "variant-name": config["variant-name"],
        "plan-file": str(plan_file),
        "benchmark-file": str(config["benchmark-file"]),
        "summary-file": str(config["summary-file"]),
        "live_quote_report": market_result.get("report") or {},
        "pipeline_report": {
            "plan_files": pipeline_report.get("plan_files") or {},
            "summary_text": pipeline_report.get("summary_text") or "",
            "summary_by_variant": {config["variant-name"]: variant_summary} if variant_summary else {},
        },
    }
    write_bridge_report(config["live-bridge-report-file"], bridge_report)
    print(
        f"[etf dual bridge] trade_date={trade_date} variant={config['variant-name']} "
        f"quotes={market_result.get('report', {}).get('received_quote_count', 0)}/"
        f"{market_result.get('report', {}).get('requested_symbol_count', 0)} "
        f"source={market_context.get('selected_mode', '-')}",
        flush=True,
    )
    return bridge_report


def run_bridge(config_path: str | Path | None = None, once: bool = False) -> int:
    config = load_live_bridge_config(config_path)
    realtime_client = live_market_cache.create_realtime_client(config)
    simulated_client = live_market_cache.create_simulated_client(config)

    while True:
        refresh_live_plan(config, realtime_client=realtime_client, simulated_client=simulated_client)
        if once:
            return 0
        time.sleep(float(config["live-plan-poll-interval-seconds"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh live ETF dual-rotation plan files from full-market realtime daily bars")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_bridge(args.config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
