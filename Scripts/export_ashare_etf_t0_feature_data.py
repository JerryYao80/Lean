#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_etf_t0_feature_backtest import prepare_symbol_frame
from tushare_data_layer import TushareDataLayer
from tushare_lean_export import load_registry_universe


PATH_KEYS = {
    "registry-file",
    "tushare-data-path",
    "dataset-catalog",
    "feature-data-path",
    "report-file",
}
FEATURE_COLUMNS = [
    "trade_date",
    "pre_close",
    "open",
    "high",
    "low",
    "close",
    "pct_chg",
    "amount",
    "vol",
    "trade_return",
    "gap_return",
    "close_location",
    "range_pct",
    "momentum_5",
    "momentum_20",
    "volatility_10",
    "liquidity_5",
    "gap_abs",
    "signal_momentum_20",
    "signal_momentum_5",
    "signal_liquidity_5",
    "signal_close_location",
    "signal_volatility_10",
    "signal_gap_abs",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "registry-file": str(root / "Common" / "Securities" / "Equity" / "AShareETFMetadata.cs"),
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "feature-data-path": str(root / "Data" / "alternative" / "ashare-etf-t0-features"),
        "start-date": "20240101",
        "end-date": "20251231",
        "exclude-money-market-etfs": True,
        "report-file": str(root / "Results" / "ashare-etf-t0-feature-export-report.json"),
        "dry-run": False,
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()

    if config_path:
        config_path = Path(config_path).resolve()
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, config_path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def ts_code_to_feature_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = ts_code.split(".")
    return ticker, "sse" if suffix == "SH" else "szse"


def feature_daily_path(feature_data_path: str | Path, ts_code: str) -> Path:
    ticker, market = ts_code_to_feature_parts(ts_code)
    return Path(feature_data_path) / market / "daily" / f"{ticker}.csv"


def build_feature_frame(
    layer: TushareDataLayer,
    ts_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    frame = layer.load_dataset(
        "fund_daily",
        symbol=ts_code,
        start_date=start_date,
        end_date=end_date,
    )
    if frame.empty:
        return frame

    prepared = prepare_symbol_frame(ts_code, frame)
    available_columns = [column for column in FEATURE_COLUMNS if column in prepared.columns]
    return prepared[available_columns].reset_index(drop=True)


def collect_feature_coverage_report(
    registry_file: str | Path,
    tushare_data_path: str | Path,
    dataset_catalog: str | Path,
    feature_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
    exclude_money_market: bool = True,
) -> dict:
    universe = load_registry_universe(registry_file, exclude_money_market=exclude_money_market)
    layer = TushareDataLayer(tushare_data_path, dataset_catalog)
    parquet_available = 0
    feature_export_count = 0
    missing_parquet = []
    missing_feature_export = []

    for ts_code in universe:
        frame = build_feature_frame(layer, ts_code, start_date=start_date, end_date=end_date)
        if frame.empty:
            missing_parquet.append(ts_code)
        else:
            parquet_available += 1

        feature_path = feature_daily_path(feature_data_path, ts_code)
        if feature_path.exists():
            feature_export_count += 1
        elif not frame.empty:
            missing_feature_export.append(ts_code)

    return {
        "registry_symbol_count": len(universe),
        "parquet_available_count": parquet_available,
        "feature_export_count": feature_export_count,
        "missing_parquet_symbols": missing_parquet,
        "missing_feature_export_symbols": missing_feature_export,
        "start_date": start_date,
        "end_date": end_date,
        "exclude_money_market_etfs": exclude_money_market,
    }


def export_symbol(
    ts_code: str,
    tushare_data_path: str | Path,
    dataset_catalog: str | Path,
    feature_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
) -> bool:
    layer = TushareDataLayer(tushare_data_path, dataset_catalog)
    frame = build_feature_frame(layer, ts_code, start_date=start_date, end_date=end_date)
    if frame.empty:
        return False

    path = feature_daily_path(feature_data_path, ts_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.10f")
    return True


def export_feature_universe(config: dict) -> dict:
    universe = load_registry_universe(
        config["registry-file"],
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )

    exported_symbols = []
    for ts_code in universe:
        if export_symbol(
            ts_code,
            config["tushare-data-path"],
            config["dataset-catalog"],
            config["feature-data-path"],
            config.get("start-date"),
            config.get("end-date"),
        ):
            exported_symbols.append(ts_code)

    report = collect_feature_coverage_report(
        registry_file=config["registry-file"],
        tushare_data_path=config["tushare-data-path"],
        dataset_catalog=config["dataset-catalog"],
        feature_data_path=config["feature-data-path"],
        start_date=config.get("start-date"),
        end_date=config.get("end-date"),
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )
    report["exported_symbols"] = exported_symbols
    report["exported_count"] = len(exported_symbols)

    report_file = config.get("report-file")
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="Launcher/config/config-ashare-etf-t0-feature-export.json")
    parser.add_argument("--registry-file")
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--dataset-catalog")
    parser.add_argument("--feature-data-path")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--report-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-money-market-etfs", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    overrides = {
        "registry-file": args.registry_file,
        "tushare-data-path": args.tushare_data_path,
        "dataset-catalog": args.dataset_catalog,
        "feature-data-path": args.feature_data_path,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "report-file": args.report_file,
        "dry-run": args.dry_run,
    }
    if args.include_money_market_etfs:
        overrides["exclude-money-market-etfs"] = False

    config = load_pipeline_config(args.config, overrides)
    if config.get("dry-run"):
        report = collect_feature_coverage_report(
            registry_file=config["registry-file"],
            tushare_data_path=config["tushare-data-path"],
            dataset_catalog=config["dataset-catalog"],
            feature_data_path=config["feature-data-path"],
            start_date=config.get("start-date"),
            end_date=config.get("end-date"),
            exclude_money_market=config.get("exclude-money-market-etfs", True),
        )
    else:
        report = export_feature_universe(config)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
