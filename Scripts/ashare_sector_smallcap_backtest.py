#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_backtest_config_path() -> Path:
    return repo_root() / 'Launcher' / 'config' / 'config-ashare-sector-smallcap-backtest.json'


def default_factor_csv_path() -> Path:
    return repo_root() / 'local_data' / 'ashare-sector-smallcap-factors.csv'


def default_results_dir() -> Path:
    return repo_root() / 'Results' / 'ashare-sector-smallcap' / 'backtest'


def run_factor_preprocessing(
    tushare_data_path: str | Path | None = None,
    output_path: str | Path | None = None,
    start_date: str = '2019-01-01',
    end_date: str = '2025-12-31',
) -> int:
    script = repo_root() / 'Scripts' / 'ashare_sector_smallcap_factor.py'
    tushare = str(tushare_data_path or '/home/project/tushare-downloader/tushare_data')
    output = str(output_path or default_factor_csv_path())
    command = [
        sys.executable, str(script),
        '--tushare-data', tushare,
        '--output', output,
        '--start-date', start_date,
        '--end-date', end_date,
    ]
    print(f'[sector-smallcap] Running factor preprocessing: {" ".join(command)}', flush=True)
    result = subprocess.run(command, cwd=str(repo_root()), check=False)
    return result.returncode


def build_lean() -> int:
    dotnet = str(orchestrator.default_config()['lean']['dotnet-binary'])
    command = [dotnet, 'build', 'Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj', '-c', 'Debug']
    print(f'[sector-smallcap] Building LEAN: {" ".join(command)}', flush=True)
    result = subprocess.run(command, cwd=str(repo_root()), check=False)
    return result.returncode


def run_lean_backtest(config_path: str | Path | None = None) -> int:
    config_path = Path(config_path).resolve() if config_path else default_backtest_config_path().resolve()
    command, cwd = orchestrator.build_lean_launcher_command(str(config_path))
    print(f'[sector-smallcap] Running LEAN backtest: {" ".join(command)}', flush=True)
    result = subprocess.run(command, cwd=str(cwd), check=False)
    return result.returncode


def load_backtest_summary(results_dir: str | Path | None = None) -> dict:
    d = Path(results_dir) if results_dir else default_results_dir()
    summary_path = d / 'summary.json'
    if summary_path.exists():
        return orchestrator.load_json_payload(summary_path)
    return {}


def print_backtest_report(summary: dict) -> None:
    if not summary:
        print('[sector-smallcap] No backtest summary found.')
        return
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='A-share Sector SmallCap backtest runner')
    parser.add_argument('--config', default=str(default_backtest_config_path()))
    parser.add_argument('--tushare-data', default='/home/project/tushare-downloader/tushare_data')
    parser.add_argument('--factor-output', default=str(default_factor_csv_path()))
    parser.add_argument('--start-date', default='2019-01-01')
    parser.add_argument('--end-date', default='2025-12-31')
    parser.add_argument('--skip-factor', action='store_true', help='Skip factor preprocessing')
    parser.add_argument('--skip-build', action='store_true', help='Skip LEAN build')
    parser.add_argument('--results-dir', default=str(default_results_dir()))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.skip_factor:
        rc = run_factor_preprocessing(
            tushare_data_path=args.tushare_data,
            output_path=args.factor_output,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        if rc != 0:
            print(f'[sector-smallcap] Factor preprocessing failed (rc={rc})', file=sys.stderr)
            return rc

    if not args.skip_build:
        rc = build_lean()
        if rc != 0:
            print(f'[sector-smallcap] LEAN build failed (rc={rc})', file=sys.stderr)
            return rc

    rc = run_lean_backtest(args.config)
    if rc != 0:
        print(f'[sector-smallcap] LEAN backtest failed (rc={rc})', file=sys.stderr)
        return rc

    summary = load_backtest_summary(args.results_dir)
    print_backtest_report(summary)

    return 0 if summary.get('total_return') is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
