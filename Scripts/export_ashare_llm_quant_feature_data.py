#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_llm_quant_backtest import (
    build_dataset_catalog,
    build_factor_panel,
    latest_snapshot_by_date,
    load_benchmark_frame,
    load_index_weight_frame,
    warmup_start_date,
)
from tushare_data_layer import TushareDataLayer

PATH_KEYS = {
    'tushare-data-path',
    'dataset-catalog',
    'feature-data-path',
    'benchmark-file',
    'report-file',
}

FEATURE_COLUMNS = [
    'trade_date',
    'close',
    'pct_chg',
    'pb',
    'ps_ttm',
    'dv_ttm',
    'turnover_rate_f',
    'circ_mv',
    'momentum_120_20',
    'return_5',
    'volatility_20',
    'flow_ratio',
    'big_flow_ratio',
    'in_universe',
]

BENCHMARK_COLUMNS = [
    'trade_date',
    'close',
    'pct_chg',
    'ma_window',
    'momentum_window',
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    feature_root = root / 'Data' / 'alternative' / 'ashare-llm-quant-features'
    return {
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data_v2',
        'dataset-catalog': str(root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'),
        'feature-data-path': str(feature_root),
        'benchmark-file': str(feature_root / 'benchmark' / '000300.SH.csv'),
        'report-file': str(root / 'Results' / 'ashare-llm-quant-feature-export-report.json'),
        'start-date': '20200101',
        'end-date': '20251231',
        'benchmark-symbol': '000300.SH',
        'index-code': '000300.SH',
        'benchmark-ma-window': 120,
        'benchmark-momentum-window': 60,
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
        loaded = json.loads(config_path.read_text(encoding='utf-8'))
        config.update(resolve_config_paths(loaded, config_path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def ts_code_to_feature_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = ts_code.split('.')
    return ticker, 'sse' if suffix.upper() == 'SH' else 'szse'


def feature_daily_path(feature_data_path: str | Path, ts_code: str) -> Path:
    ticker, market = ts_code_to_feature_parts(ts_code)
    return Path(feature_data_path) / market / 'daily' / f'{ticker}.csv'


def benchmark_csv_path(config: dict) -> Path:
    return Path(config['benchmark-file'])


def build_membership_frame(benchmark: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    if benchmark.empty or weights.empty:
        return pd.DataFrame(columns=['trade_date', 'symbol', 'in_universe'])

    trade_dates = benchmark['trade_date'].astype(str).dropna().tolist()
    snapshot_dates = weights['trade_date'].astype(str).dropna().unique().tolist()
    mapping = latest_snapshot_by_date(trade_dates, snapshot_dates)
    snapshots = {
        trade_date: group['con_code'].dropna().astype(str).tolist()
        for trade_date, group in weights.groupby('trade_date', sort=True)
    }

    rows = []
    for trade_date, snapshot_date in mapping.items():
        for symbol in snapshots.get(snapshot_date, []):
            rows.append({
                'trade_date': trade_date,
                'symbol': symbol,
                'in_universe': 1,
            })

    if not rows:
        return pd.DataFrame(columns=['trade_date', 'symbol', 'in_universe'])

    return pd.DataFrame(rows)


def build_export_panel(config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    layer = TushareDataLayer(config['tushare-data-path'], build_dataset_catalog(config))
    benchmark = load_benchmark_frame(layer, config)
    weights = load_index_weight_frame(layer, config)
    universe = sorted(weights['con_code'].dropna().astype(str).unique().tolist()) if not weights.empty else []
    panel = build_factor_panel(
        layer,
        universe,
        warmup_start_date(benchmark, config),
        config['end-date'],
    )
    panel = panel[panel['trade_date'] >= config['start-date']].copy()
    membership = build_membership_frame(benchmark, weights)
    if membership.empty:
        panel['in_universe'] = 0
    else:
        panel = panel.merge(membership, on=['trade_date', 'symbol'], how='left')
        panel['in_universe'] = panel['in_universe'].fillna(0).astype(int)

    benchmark_frame = benchmark[BENCHMARK_COLUMNS].copy()
    return panel, benchmark_frame


def export_feature_universe(config: dict) -> dict:
    panel, benchmark = build_export_panel(config)

    exported_symbols = []
    for symbol, frame in panel.groupby('symbol', sort=True):
        path = feature_daily_path(config['feature-data-path'], symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = frame[FEATURE_COLUMNS].copy()
        ordered.to_csv(path, index=False, float_format='%.10f')
        exported_symbols.append(symbol)

    benchmark_path = benchmark_csv_path(config)
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    benchmark.to_csv(benchmark_path, index=False, float_format='%.10f')

    report = {
        'start_date': config['start-date'],
        'end_date': config['end-date'],
        'benchmark_symbol': config['benchmark-symbol'],
        'index_code': config['index-code'],
        'exported_symbol_count': len(exported_symbols),
        'feature_row_count': int(len(panel)),
        'feature_trade_date_count': int(panel['trade_date'].nunique()) if not panel.empty else 0,
        'benchmark_row_count': int(len(benchmark)),
        'feature_data_path': config['feature-data-path'],
        'benchmark_file': str(benchmark_path),
        'exported_symbols': exported_symbols,
    }

    report_file = config.get('report-file')
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Export A-share LLM multi-factor feature data for LEAN backtests')
    parser.add_argument('--config')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--feature-data-path')
    parser.add_argument('--benchmark-file')
    parser.add_argument('--report-file')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'start-date': args.start_date,
        'end-date': args.end_date,
        'feature-data-path': args.feature_data_path,
        'benchmark-file': args.benchmark_file,
        'report-file': args.report_file,
    }
    report = export_feature_universe(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
