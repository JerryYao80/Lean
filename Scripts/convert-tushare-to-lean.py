#!/usr/bin/env python3

import argparse
import json

from tushare_lean_export import collect_coverage_report, export_registry_universe, load_pipeline_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="Launcher/config/config-ashare-data-export.json")
    parser.add_argument("--registry-file")
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--lean-data-path")
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
        "lean-data-path": args.lean_data_path,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "report-file": args.report_file,
        "dry-run": args.dry_run,
    }
    if args.include_money_market_etfs:
        overrides["exclude-money-market-etfs"] = False

    config = load_pipeline_config(args.config, overrides)
    if config.get("dry-run"):
        report = collect_coverage_report(
            registry_file=config["registry-file"],
            tushare_data_path=config["tushare-data-path"],
            lean_data_path=config["lean-data-path"],
            start_date=config.get("start-date"),
            end_date=config.get("end-date"),
            exclude_money_market=config.get("exclude-money-market-etfs", True),
        )
    else:
        report = export_registry_universe(config)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
