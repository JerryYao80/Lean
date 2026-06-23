#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import ashare_live_market_cache as live_market_cache


PATH_KEYS = {
    "tushare-data-path",
    "factor-data-path",
    "live-factor-report-file",
    "live-price-snapshot-file",
    "external-factor-path",
    "shared-live-market-snapshot-file",
    "shared-live-market-report-file",
}
DATA_RELATIVE_KEYS = {
    "factor-data-path",
    "external-factor-path",
}
RESULTS_RELATIVE_KEYS = {
    "live-factor-report-file",
    "live-price-snapshot-file",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_live_config_path() -> Path:
    return repo_root() / "Launcher" / "config" / "config-ashare-multi-family-live-paper.json"


def resolve_live_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path).resolve() if config_path else default_live_config_path().resolve()


def load_live_bridge_config(config_path: str | Path | None = None) -> dict:
    config_file = resolve_live_config_path(config_path)
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    parameters = payload.get("parameters") if isinstance(payload, dict) else None
    if not isinstance(parameters, dict):
        parameters = {}

    launcher_workdir = repo_root() / "Launcher" / "bin" / "Debug"
    data_base = launcher_workdir
    results_base = launcher_workdir

    data_folder = payload.get("data-folder") if isinstance(payload, dict) else None
    if data_folder:
        path = Path(str(data_folder))
        data_base = path if path.is_absolute() else (launcher_workdir / path).resolve()

    results_folder = payload.get("results-destination-folder") if isinstance(payload, dict) else None
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
        "tushare-data-path": str(parameters.get("tushare-data-path") or "/home/project/tushare-downloader/tushare_data_v2"),
        "factor-data-path": str(resolve_path(parameters.get("factor-data-path"), root / "Data" / "alternative" / "ashare-multi-family-live-factors", data_base)),
        "external-factor-path": str(resolve_path(parameters.get("external-factor-path"), root / "Data" / "alternative" / "ashare-multi-family-features", data_base)),
        "live-factor-report-file": str(resolve_path(parameters.get("live-factor-report-file"), root / "Results" / "multi-family-live-bridge-report.json", results_base)),
        "live-price-snapshot-file": str(resolve_path(parameters.get("live-price-snapshot-file"), root / "Results" / "multi-family-live-price-snapshot.json", results_base)),
        "shared-live-market-snapshot-file": str(resolve_path(parameters.get("shared-live-market-snapshot-file"), root / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json", results_base)),
        "shared-live-market-report-file": str(resolve_path(parameters.get("shared-live-market-report-file"), root / "Results" / "shared-live-market" / "ashare-live-market-report.json", results_base)),
        "universe": str(parameters.get("universe") or "csi300"),
        "live-price-poll-interval-seconds": str(parameters.get("live-price-poll-interval-seconds") or "60"),
        "live-price-source-mode": str(parameters.get("live-price-source-mode") or "auto"),
    }


def load_field_mapping(path: str | None = None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_latest_factor_csv(external_path: Path, ticker: str, market: str) -> Path | None:
    csv_path = external_path / market / "daily" / f"{ticker}.csv"
    if csv_path.exists():
        return csv_path
    return None


def extract_factor_snapshot(csv_path: Path, target_date: str | None = None) -> dict | None:
    if not csv_path.exists():
        return None
    try:
        lines = csv_path.read_text(encoding="utf-8").strip().split("\n")
    except Exception:
        return None
    if len(lines) < 2:
        return None

    header = lines[0].split(",")
    if target_date:
        for line in reversed(lines[1:]):
            if not line.strip() or line.startswith("trade_date"):
                continue
            csv = line.split(",")
            if csv[0] <= target_date:
                return {"header": header, "data": csv}
    else:
        return {"header": header, "data": lines[-1].split(",")}
    return None


def run_live_bridge(config: dict) -> dict:
    from export_ashare_multi_family_feature_data import discover_universe_symbols
    from tushare_data_layer import TushareDataLayer

    factor_data_path = Path(config["factor-data-path"])
    external_factor_path = Path(config["external-factor-path"])
    snapshot_path = Path(config["live-price-snapshot-file"])
    report_path = Path(config["live-factor-report-file"])

    for subdir in ["sse/daily", "szse/daily"]:
        (factor_data_path / subdir).mkdir(parents=True, exist_ok=True)

    tushare = TushareDataLayer(config["tushare-data-path"])
    trade_date = datetime.now().strftime("%Y%m%d")

    symbols = discover_universe_symbols(config.get("universe", "csi300"), tushare, external_factor_path=config.get("external-factor-path"))
    written_count = 0
    missing_count = 0
    quotes = []

    for ts_code in symbols:
        parts = ts_code.split(".")
        if len(parts) != 2:
            continue
        ticker = parts[0]
        market = "sse" if parts[1] == "SH" else "szse"

        src_csv = find_latest_factor_csv(external_factor_path, ticker, market)
        if src_csv is None:
            missing_count += 1
            continue

        snapshot = extract_factor_snapshot(src_csv, target_date=trade_date)
        if snapshot is None:
            snapshot = extract_factor_snapshot(src_csv)

        if snapshot is None:
            missing_count += 1
            continue

        header = snapshot["header"]
        data = snapshot["data"]

        dst_csv = factor_data_path / market / "daily" / f"{ticker}.csv"

        if dst_csv.exists():
            existing_lines = dst_csv.read_text(encoding="utf-8").strip().split("\n")
            if len(existing_lines) > 1 and existing_lines[-1].split(",")[0] == data[0]:
                existing_lines[-1] = ",".join(data)
            else:
                existing_lines.append(",".join(data))
            dst_csv.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
        else:
            dst_csv.write_text(",".join(header) + "\n" + ",".join(data) + "\n", encoding="utf-8")

        written_count += 1

        close_val = None
        for i, col in enumerate(header):
            if col == "close" and i < len(data) and data[i]:
                try:
                    close_val = float(data[i])
                except ValueError:
                    pass
                break

        if close_val and close_val > 0:
            quotes.append({
                "ts_code": ts_code,
                "trade_date": data[0] if data else trade_date,
                "close": close_val,
            })

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_payload = {
        "trade_date": trade_date,
        "quote_count": len(quotes),
        "quotes": quotes,
    }
    snapshot_path.write_text(json.dumps(snapshot_payload, ensure_ascii=False), encoding="utf-8")

    report = {
        "trade_date": trade_date,
        "bridge_report": {
            "mode": "live",
            "written_symbol_count": written_count,
            "missing_symbol_count": missing_count,
            "resolved_symbol_count": len(symbols),
            "output_path": str(factor_data_path),
            "external_factor_path": str(external_factor_path),
        },
        "live_quote_report": {
            "requested_symbol_count": len(symbols),
            "received_quote_count": len(quotes),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-family live bridge: refresh live factor CSVs and price snapshot")
    parser.add_argument("--config", default=str(default_live_config_path()))
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_live_bridge_config(args.config)

    if args.once:
        report = run_live_bridge(config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    poll_seconds = max(10, int(config.get("live-price-poll-interval-seconds", "60")))
    print(f"[bridge] starting continuous mode: poll_interval={poll_seconds}s", flush=True)

    while True:
        try:
            report = run_live_bridge(config)
            print(
                f"[bridge] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                f"trade_date={report.get('trade_date')} "
                f"written={report.get('bridge_report', {}).get('written_symbol_count', 0)} "
                f"quotes={report.get('live_quote_report', {}).get('received_quote_count', 0)}",
                flush=True,
            )
        except Exception as ex:
            print(f"[bridge] error: {ex}", flush=True)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
