#!/usr/bin/env python3

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer
from export_ashare_stock_data import load_stock_universe

PATH_KEYS = {
    'tushare-data-path',
    'dataset-catalog',
    'report-file',
    'trade-report-file',
    'daily-summary-file',
    'summary-file',
}
LOT_SIZE = 100


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data_v2',
        'dataset-catalog': str(root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'),
        'start-date': '20200101',
        'end-date': '20251231',
        'lookback-period': 20,
        'entry-threshold': -2.0,
        'exit-threshold': 0.0,
        'max-positions': 10,
        'position-size': 0.1,
        'initial-capital': 1000000,
        'fee-rate': 0.0013,
        'report-file': str(root / 'Results' / 'ashare-t1-backtest-report.md'),
        'trade-report-file': str(root / 'Results' / 'ashare-t1-trades.csv'),
        'daily-summary-file': str(root / 'Results' / 'ashare-t1-daily.csv'),
        'summary-file': str(root / 'Results' / 'ashare-t1-summary.json'),
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


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors='coerce')


def prepare_symbol_frame(
    ts_code: str,
    frame: pd.DataFrame,
    lookback_period: int = 20,
    entry_threshold: float = -2.0,
    exit_threshold: float = 0.0,
) -> pd.DataFrame:
    data = frame.copy()
    data['trade_date'] = data['trade_date'].astype(str).str.zfill(8)
    data = data.sort_values('trade_date').reset_index(drop=True)
    data['symbol'] = ts_code
    data['close'] = _numeric(data['close'])
    data['open'] = _numeric(data['open']) if 'open' in data.columns else data['close']
    rolling_mean = data['close'].rolling(int(lookback_period)).mean()
    rolling_std = data['close'].rolling(int(lookback_period)).std(ddof=0).replace(0, float('nan'))
    data['zscore'] = (data['close'] - rolling_mean) / rolling_std
    data['signal_enter'] = data['zscore'] <= float(entry_threshold)
    data['signal_exit'] = data['zscore'] >= float(exit_threshold)
    if 'pct_chg' in data.columns:
        data['daily_return'] = _numeric(data['pct_chg']) / 100.0
    else:
        data['daily_return'] = data['close'].pct_change().fillna(0.0)
    return data


def build_prepared_panel(
    layer: TushareDataLayer,
    universe: list[str],
    start_date: str | None,
    end_date: str | None,
    lookback_period: int,
    entry_threshold: float,
    exit_threshold: float,
) -> pd.DataFrame:
    frames = []
    for ts_code in universe:
        frame = layer.load_dataset(
            'daily',
            symbol=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=['open', 'high', 'low', 'close', 'pct_chg'],
        )
        if frame.empty:
            continue
        prepared = prepare_symbol_frame(ts_code, frame, lookback_period, entry_threshold, exit_threshold)
        frames.append(prepared)

    if not frames:
        return pd.DataFrame(columns=['trade_date', 'symbol', 'close', 'open', 'zscore', 'signal_enter', 'signal_exit'])

    return pd.concat(frames, ignore_index=True).sort_values(['trade_date', 'symbol']).reset_index(drop=True)


def _lot_quantity(cash_budget: float, price: float) -> int:
    if cash_budget <= 0 or price <= 0:
        return 0
    return int(math.floor(cash_budget / (price * LOT_SIZE)) * LOT_SIZE)


def backtest_from_prepared_data(
    panel: pd.DataFrame,
    initial_capital: float,
    max_positions: int,
    position_size: float,
    fee_rate: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if panel.empty:
        daily = pd.DataFrame(columns=['trade_date', 'cash', 'market_value', 'equity', 'position_count', 'daily_return'])
        trades = pd.DataFrame(columns=['trade_date', 'action', 'symbol', 'price', 'quantity', 'fee', 'zscore'])
        return daily, trades, {
            'trade_days': 0,
            'final_equity': float(initial_capital),
            'total_return': 0.0,
            'completed_trades': 0,
            'open_positions': 0,
        }

    cash = float(initial_capital)
    positions: dict[str, dict] = {}
    daily_rows = []
    trade_rows = []
    prior_equity = float(initial_capital)
    completed_trades = 0

    for trade_date, group in panel.groupby('trade_date', sort=True):
        group = group.sort_values('zscore', ascending=True).reset_index(drop=True)
        row_by_symbol = {row['symbol']: row for _, row in group.iterrows()}

        for symbol, position in list(positions.items()):
            row = row_by_symbol.get(symbol)
            if row is not None:
                position['last_price'] = float(row['close'])
                position['zscore'] = float(row['zscore']) if pd.notna(row['zscore']) else 0.0

        for symbol, position in list(positions.items()):
            row = row_by_symbol.get(symbol)
            if row is None:
                continue
            if position['holding_days'] < 1:
                continue
            if not bool(row['signal_exit']):
                continue

            price = float(row['close'])
            quantity = int(position['quantity'])
            fee = price * quantity * float(fee_rate)
            cash += price * quantity - fee
            trade_rows.append({
                'trade_date': trade_date,
                'action': 'SELL',
                'symbol': symbol,
                'price': price,
                'quantity': quantity,
                'fee': fee,
                'zscore': float(row['zscore']) if pd.notna(row['zscore']) else None,
            })
            completed_trades += 1
            positions.pop(symbol, None)

        portfolio_value = cash + sum(position['quantity'] * position['last_price'] for position in positions.values())
        available_slots = max(0, int(max_positions) - len(positions))
        candidates = group[(group['signal_enter']) & (~group['symbol'].isin(list(positions.keys())))]
        for _, row in candidates.head(available_slots).iterrows():
            target_notional = portfolio_value * float(position_size)
            price = float(row['close'])
            quantity = _lot_quantity(min(cash, target_notional), price)
            if quantity <= 0:
                continue
            fee = price * quantity * float(fee_rate)
            cash -= price * quantity + fee
            positions[row['symbol']] = {
                'quantity': quantity,
                'entry_date': trade_date,
                'entry_price': price,
                'last_price': price,
                'holding_days': 0,
                'zscore': float(row['zscore']) if pd.notna(row['zscore']) else 0.0,
            }
            trade_rows.append({
                'trade_date': trade_date,
                'action': 'BUY',
                'symbol': row['symbol'],
                'price': price,
                'quantity': quantity,
                'fee': fee,
                'zscore': float(row['zscore']) if pd.notna(row['zscore']) else None,
            })

        market_value = sum(position['quantity'] * position['last_price'] for position in positions.values())
        equity = cash + market_value
        daily_return = equity / prior_equity - 1 if prior_equity else 0.0
        daily_rows.append({
            'trade_date': trade_date,
            'cash': cash,
            'market_value': market_value,
            'equity': equity,
            'position_count': len(positions),
            'daily_return': daily_return,
        })
        prior_equity = equity

        for position in positions.values():
            position['holding_days'] += 1

    daily = pd.DataFrame(daily_rows)
    trades = pd.DataFrame(trade_rows)
    final_equity = float(daily.iloc[-1]['equity']) if not daily.empty else float(initial_capital)
    summary = {
        'trade_days': int(len(daily)),
        'final_equity': final_equity,
        'total_return': final_equity / float(initial_capital) - 1 if initial_capital else 0.0,
        'completed_trades': int(completed_trades),
        'open_positions': int(len(positions)),
    }
    return daily, trades, summary


def build_report_text(summary: dict, config: dict, universe_count: int, loaded_symbol_count: int) -> str:
    lines = [
        'AShare T+1 Mean Reversion Backtest',
        f"Date Range: {config['start-date']} -> {config['end-date']}",
        f'Universe Count: {universe_count}',
        f'Loaded Symbols: {loaded_symbol_count}',
        f"Lookback: {config['lookback-period']}",
        f"Entry Threshold: {config['entry-threshold']}",
        f"Exit Threshold: {config['exit-threshold']}",
        f"Max Positions: {config['max-positions']}",
        f"Position Size: {float(config['position-size']):.2%}",
        f"Initial Capital: {float(config['initial-capital']):,.2f}",
        'Results:',
        f"- Trade Days: {summary['trade_days']}",
        f"- Completed Trades: {summary['completed_trades']}",
        f"- Open Positions: {summary['open_positions']}",
        f"- Final Equity: {summary['final_equity']:.6f}",
        f"- Total Return: {summary['total_return']:.2%}",
    ]
    return '\n'.join(lines) + '\n'


def run_backtest(config: dict) -> dict:
    layer = TushareDataLayer(config['tushare-data-path'], config['dataset-catalog'])
    universe = load_stock_universe(layer, explicit_universe=config.get('universe'))
    panel = build_prepared_panel(
        layer,
        universe,
        config.get('start-date'),
        config.get('end-date'),
        int(config.get('lookback-period', 20) or 20),
        float(config.get('entry-threshold', -2.0) or -2.0),
        float(config.get('exit-threshold', 0.0) or 0.0),
    )
    daily, trades, summary = backtest_from_prepared_data(
        panel,
        initial_capital=float(config.get('initial-capital', 1000000) or 1000000),
        max_positions=int(config.get('max-positions', 10) or 10),
        position_size=float(config.get('position-size', 0.1) or 0.1),
        fee_rate=float(config.get('fee-rate', 0.0013) or 0.0013),
    )

    trade_report_file = config.get('trade-report-file')
    if trade_report_file:
        path = Path(trade_report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(path, index=False, float_format='%.10f')

    daily_summary_file = config.get('daily-summary-file')
    if daily_summary_file:
        path = Path(daily_summary_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(path, index=False, float_format='%.10f')

    summary = {
        **summary,
        'universe_count': len(universe),
        'loaded_symbol_count': int(panel['symbol'].nunique()) if not panel.empty else 0,
        'start_date': config.get('start-date'),
        'end_date': config.get('end-date'),
        'lookback_period': int(config.get('lookback-period', 20) or 20),
        'entry_threshold': float(config.get('entry-threshold', -2.0) or -2.0),
        'exit_threshold': float(config.get('exit-threshold', 0.0) or 0.0),
        'max_positions': int(config.get('max-positions', 10) or 10),
        'position_size': float(config.get('position-size', 0.1) or 0.1),
        'initial_capital': float(config.get('initial-capital', 1000000) or 1000000),
        'fee_rate': float(config.get('fee-rate', 0.0013) or 0.0013),
    }

    summary_file = config.get('summary-file')
    if summary_file:
        path = Path(summary_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    report_file = config.get('report-file')
    if report_file:
        path = Path(report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_report_text(summary, config, len(universe), summary['loaded_symbol_count']), encoding='utf-8')

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run A-share T+1 mean-reversion backtest')
    parser.add_argument('--config')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--report-file')
    parser.add_argument('--trade-report-file')
    parser.add_argument('--daily-summary-file')
    parser.add_argument('--summary-file')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'start-date': args.start_date,
        'end-date': args.end_date,
        'report-file': args.report_file,
        'trade-report-file': args.trade_report_file,
        'daily-summary-file': args.daily_summary_file,
        'summary-file': args.summary_file,
    }
    summary = run_backtest(load_pipeline_config(args.config, overrides))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
