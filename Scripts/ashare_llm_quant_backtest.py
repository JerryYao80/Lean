#!/usr/bin/env python3

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer

PATH_KEYS = {
    'tushare-data-path',
    'dataset-catalog',
    'report-file',
    'daily-nav-file',
    'rebalance-file',
    'summary-file',
}

FACTOR_WEIGHT_KEYS = {
    'momentum',
    'value-pb',
    'value-ps',
    'dividend',
    'flow',
    'big-flow',
    'low-vol',
    'short-reversal',
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data',
        'dataset-catalog': str(root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'),
        'start-date': '20200101',
        'end-date': '20251231',
        'benchmark-symbol': '000300.SH',
        'index-code': '000300.SH',
        'top-n': 10,
        'initial-capital': 1000000,
        'fee-rate': 0.0013,
        'min-price': 5.0,
        'min-circ-mv': 1000000.0,
        'min-turnover-rate-f': 0.3,
        'max-vol20': 0.045,
        'require-positive-flow': True,
        'require-positive-momentum': False,
        'risk-on-exposure': 1.0,
        'risk-off-exposure': 0.4,
        'benchmark-ma-window': 120,
        'benchmark-momentum-window': 60,
        'benchmark-momentum-gate': -0.02,
        'factor-weights': {
            'momentum': 1.2,
            'value-pb': 0.6,
            'value-ps': 0.2,
            'dividend': 0.2,
            'flow': 0.8,
            'big-flow': 0.6,
            'low-vol': 1.0,
            'short-reversal': 0.2,
        },
        'report-file': str(root / 'Results' / 'ashare-llm-quant-report.md'),
        'daily-nav-file': str(root / 'Results' / 'ashare-llm-quant-daily.csv'),
        'rebalance-file': str(root / 'Results' / 'ashare-llm-quant-rebalances.csv'),
        'summary-file': str(root / 'Results' / 'ashare-llm-quant-summary.json'),
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

    weights = dict(default_config()['factor-weights'])
    weights.update(config.get('factor-weights') or {})
    config['factor-weights'] = {
        key: float(weights.get(key, 0.0) or 0.0)
        for key in FACTOR_WEIGHT_KEYS
    }
    return resolve_config_paths(config, repo_root())


def build_dataset_catalog(config: dict) -> dict:
    raw = json.loads(Path(config['dataset-catalog']).read_text(encoding='utf-8'))
    datasets = dict(raw['datasets'])
    datasets.setdefault(
        'moneyflow',
        {
            'path': 'moneyflow/ts_code={symbol}/data.parquet',
            'date_field': 'trade_date',
            'symbol_field': 'ts_code',
        },
    )
    datasets.setdefault(
        'index_weight',
        {
            'path': 'index_weight/date=*/data.parquet',
            'date_field': 'trade_date',
            'symbol_field': 'con_code',
        },
    )
    return {'datasets': datasets}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors='coerce')


def _zscore(series: pd.Series) -> pd.Series:
    values = _numeric(series).replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5:
        return pd.Series(np.nan, index=series.index)
    std = values.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (values - values.mean()) / std


def load_benchmark_frame(layer: TushareDataLayer, config: dict) -> pd.DataFrame:
    frame = layer.load_dataset(
        'index_daily',
        symbol=config['benchmark-symbol'],
        start_date=config['start-date'],
        end_date=config['end-date'],
        fields=['open', 'high', 'low', 'close', 'pct_chg', 'amount'],
    )
    if frame.empty:
        return pd.DataFrame(columns=['trade_date', 'close', 'pct_chg'])

    data = frame.copy()
    data['trade_date'] = data['trade_date'].astype(str).str.zfill(8)
    data = data.sort_values('trade_date').reset_index(drop=True)
    data['close'] = _numeric(data['close'])
    data['pct_chg'] = _numeric(data['pct_chg'])
    data['benchmark_return'] = data['pct_chg'].fillna(0.0) / 100.0
    ma_window = int(config.get('benchmark-ma-window', 120) or 120)
    momentum_window = int(config.get('benchmark-momentum-window', 60) or 60)
    data['ma_window'] = data['close'].rolling(ma_window).mean()
    data['momentum_window'] = data['close'].pct_change(momentum_window)
    return data


def latest_snapshot_by_date(rebalance_dates: list[str], snapshot_dates: list[str]) -> dict[str, str]:
    if not snapshot_dates:
        return {}

    snapshots = np.array(sorted(snapshot_dates), dtype=object)
    mapping = {}
    for trade_date in rebalance_dates:
        index = int(np.searchsorted(snapshots, trade_date, side='right') - 1)
        if index >= 0:
            mapping[trade_date] = str(snapshots[index])
    return mapping


def load_index_weight_frame(layer: TushareDataLayer, config: dict) -> pd.DataFrame:
    frame = layer.load_dataset(
        'index_weight',
        start_date=config['start-date'],
        end_date=config['end-date'],
        fields=['index_code', 'con_code', 'weight'],
    )
    if frame.empty:
        return pd.DataFrame(columns=['trade_date', 'index_code', 'con_code', 'weight'])

    data = frame.copy()
    data['trade_date'] = data['trade_date'].astype(str).str.zfill(8)
    data = data[data['index_code'] == config['index-code']].copy()
    data['weight'] = _numeric(data['weight'])
    data['con_code'] = data['con_code'].astype(str)
    return data.sort_values(['trade_date', 'weight'], ascending=[True, False]).reset_index(drop=True)


def build_constituent_schedule(benchmark: pd.DataFrame, weights: pd.DataFrame) -> tuple[list[str], dict[str, str], dict[str, pd.DataFrame]]:
    if benchmark.empty or weights.empty:
        return [], {}, {}

    rebalance_dates = benchmark.groupby(benchmark['trade_date'].str[:6])['trade_date'].last().tolist()
    snapshot_dates = weights['trade_date'].dropna().astype(str).unique().tolist()
    rebalance_to_snapshot = latest_snapshot_by_date(rebalance_dates, snapshot_dates)
    snapshots = {
        trade_date: group[['con_code', 'weight']].copy().reset_index(drop=True)
        for trade_date, group in weights.groupby('trade_date', sort=True)
    }
    rebalance_dates = [trade_date for trade_date in rebalance_dates if trade_date in rebalance_to_snapshot]
    return rebalance_dates, rebalance_to_snapshot, snapshots


def warmup_start_date(benchmark: pd.DataFrame, config: dict) -> str:
    if benchmark.empty:
        return config['start-date']
    lookback = max(
        int(config.get('benchmark-ma-window', 120) or 120),
        int(config.get('benchmark-momentum-window', 60) or 60),
        140,
    )
    dates = benchmark['trade_date'].tolist()
    if not dates:
        return config['start-date']
    start_date = config['start-date']
    if start_date not in set(dates):
        return dates[0]
    start_index = dates.index(start_date)
    warmup_index = max(0, start_index - lookback)
    return dates[warmup_index]


def load_symbol_feature_frame(
    layer: TushareDataLayer,
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    daily = layer.load_dataset(
        'daily',
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        fields=['close', 'pct_chg', 'amount'],
    )
    if daily.empty:
        return pd.DataFrame()

    basic = layer.load_dataset(
        'daily_basic',
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        fields=['pb', 'ps_ttm', 'dv_ttm', 'turnover_rate_f', 'circ_mv'],
    )
    moneyflow = layer.load_dataset(
        'moneyflow',
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        fields=['net_mf_amount', 'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount'],
    )

    data = daily.copy()
    data['trade_date'] = data['trade_date'].astype(str).str.zfill(8)
    if not basic.empty:
        basic = basic.copy()
        basic['trade_date'] = basic['trade_date'].astype(str).str.zfill(8)
        data = data.merge(basic, on='trade_date', how='left')
    if not moneyflow.empty:
        moneyflow = moneyflow.copy()
        moneyflow['trade_date'] = moneyflow['trade_date'].astype(str).str.zfill(8)
        data = data.merge(moneyflow, on='trade_date', how='left')

    data = data.sort_values('trade_date').reset_index(drop=True)
    data['symbol'] = symbol
    data['close'] = _numeric(data['close'])
    data['pct_chg'] = _numeric(data['pct_chg'])
    data['amount'] = _numeric(data['amount']).replace(0.0, np.nan)
    data['pb'] = _numeric(data.get('pb'))
    data['ps_ttm'] = _numeric(data.get('ps_ttm'))
    data['dv_ttm'] = _numeric(data.get('dv_ttm'))
    data['turnover_rate_f'] = _numeric(data.get('turnover_rate_f'))
    data['circ_mv'] = _numeric(data.get('circ_mv'))
    data['net_mf_amount'] = _numeric(data.get('net_mf_amount'))
    data['buy_lg_amount'] = _numeric(data.get('buy_lg_amount'))
    data['sell_lg_amount'] = _numeric(data.get('sell_lg_amount'))
    data['buy_elg_amount'] = _numeric(data.get('buy_elg_amount'))
    data['sell_elg_amount'] = _numeric(data.get('sell_elg_amount'))
    data['momentum_120_20'] = data['close'].shift(20) / data['close'].shift(120) - 1.0
    data['return_5'] = data['close'].pct_change(5)
    data['volatility_20'] = data['pct_chg'].div(100.0).rolling(20).std()
    data['flow_ratio'] = data['net_mf_amount'] / data['amount']
    data['big_flow_ratio'] = (
        (data['buy_lg_amount'].fillna(0.0) + data['buy_elg_amount'].fillna(0.0))
        - (data['sell_lg_amount'].fillna(0.0) + data['sell_elg_amount'].fillna(0.0))
    ) / data['amount']

    keep = [
        'trade_date',
        'symbol',
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
    ]
    return data[keep]


def build_factor_panel(
    layer: TushareDataLayer,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        frame = load_symbol_feature_frame(layer, symbol, start_date, end_date)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=['trade_date', 'symbol'])
    return pd.concat(frames, ignore_index=True).sort_values(['trade_date', 'symbol']).reset_index(drop=True)


def score_cross_section(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    if frame.empty:
        return frame

    data = frame.copy()
    data = data[
        (data['close'] >= float(config.get('min-price', 5.0) or 5.0))
        & (data['circ_mv'] >= float(config.get('min-circ-mv', 1000000.0) or 1000000.0))
        & (data['turnover_rate_f'] >= float(config.get('min-turnover-rate-f', 0.3) or 0.3))
    ].copy()

    if bool(config.get('require-positive-flow', True)):
        data = data[data['flow_ratio'] > 0.0]

    if bool(config.get('require-positive-momentum', False)):
        data = data[data['momentum_120_20'] > 0.0]

    max_vol = config.get('max-vol20')
    if max_vol is not None:
        data = data[data['volatility_20'] <= float(max_vol)]

    if data.empty:
        return data

    weights = config['factor-weights']
    score = (
        weights['momentum'] * _zscore(data['momentum_120_20'])
        + weights['value-pb'] * _zscore(-np.log(data['pb'].clip(lower=0.05)))
        + weights['value-ps'] * _zscore(-np.log(data['ps_ttm'].clip(lower=0.05)))
        + weights['dividend'] * _zscore(data['dv_ttm'])
        + weights['flow'] * _zscore(data['flow_ratio'])
        + weights['big-flow'] * _zscore(data['big_flow_ratio'])
        + weights['low-vol'] * _zscore(-data['volatility_20'])
        + weights['short-reversal'] * _zscore(-data['return_5'])
    )
    data['score'] = score
    data = data.dropna(subset=['score']).sort_values('score', ascending=False).reset_index(drop=True)
    top_n = int(config.get('top-n', 10) or 10)
    if top_n <= 0:
        return data.iloc[0:0]
    return data.head(top_n).copy()


def build_target_specs(
    benchmark: pd.DataFrame,
    panel: pd.DataFrame,
    rebalance_dates: list[str],
    rebalance_to_snapshot: dict[str, str],
    snapshots: dict[str, pd.DataFrame],
    config: dict,
) -> dict[str, dict]:
    if panel.empty:
        return {}

    benchmark_map = benchmark.set_index('trade_date')
    by_date = {
        trade_date: group.set_index('symbol')
        for trade_date, group in panel.groupby('trade_date', sort=True)
    }
    target_specs = {}

    for signal_date in rebalance_dates:
        frame = by_date.get(signal_date)
        snapshot_date = rebalance_to_snapshot.get(signal_date)
        snapshot = snapshots.get(snapshot_date or '')
        if frame is None or snapshot is None or snapshot.empty:
            continue

        symbols = snapshot['con_code'].dropna().astype(str).tolist()
        cross_section = frame.loc[frame.index.intersection(symbols)].reset_index()
        selected = score_cross_section(cross_section, config)
        benchmark_row = benchmark_map.loc[signal_date]
        momentum_gate = float(config.get('benchmark-momentum-gate', -0.02) or -0.02)
        risk_on = (
            pd.notna(benchmark_row['ma_window'])
            and float(benchmark_row['close']) > float(benchmark_row['ma_window'])
            and pd.notna(benchmark_row['momentum_window'])
            and float(benchmark_row['momentum_window']) > momentum_gate
        )
        target_exposure = float(
            config.get('risk-on-exposure', 1.0) if risk_on else config.get('risk-off-exposure', 0.4)
        )

        target_weights = {}
        if target_exposure > 0.0 and not selected.empty:
            weight = target_exposure / len(selected)
            target_weights = {
                symbol: weight
                for symbol in selected['symbol'].astype(str).tolist()
            }

        target_specs[signal_date] = {
            'signal_date': signal_date,
            'snapshot_date': snapshot_date,
            'risk_state': 'RISK_ON' if risk_on else 'RISK_OFF',
            'target_exposure': target_exposure,
            'selected_count': len(selected),
            'selected_symbols': ';'.join(selected['symbol'].astype(str).tolist()),
            'target_weights': target_weights,
        }

    return target_specs


def simulate_portfolio(
    trade_dates: list[str],
    daily_frames: dict[str, pd.DataFrame],
    rebalance_targets: dict[str, dict],
    fee_rate: float,
    initial_capital: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not trade_dates:
        return (
            pd.DataFrame(columns=['trade_date', 'equity', 'nav', 'daily_return', 'holdings_count']),
            pd.DataFrame(columns=['signal_date', 'effective_date', 'turnover', 'fee', 'holdings_count']),
        )

    next_trade_date = {
        trade_dates[index]: trade_dates[index + 1] if index + 1 < len(trade_dates) else None
        for index in range(len(trade_dates))
    }
    pending = {}
    current_weights: dict[str, float] = {}
    prior_weights: dict[str, float] = {}
    equity = float(initial_capital)
    daily_rows = []
    rebalance_rows = []

    for trade_date in trade_dates:
        scheduled = pending.pop(trade_date, None)
        if scheduled is not None:
            target_weights = dict(scheduled['target_weights'])
            union_symbols = set(prior_weights) | set(target_weights)
            turnover = sum(abs(target_weights.get(symbol, 0.0) - prior_weights.get(symbol, 0.0)) for symbol in union_symbols)
            fee = equity * turnover * float(fee_rate)
            equity -= fee
            current_weights = target_weights
            prior_weights = target_weights.copy()
            rebalance_rows.append({
                'signal_date': scheduled['signal_date'],
                'effective_date': trade_date,
                'snapshot_date': scheduled['snapshot_date'],
                'risk_state': scheduled['risk_state'],
                'target_exposure': scheduled['target_exposure'],
                'turnover': turnover,
                'fee': fee,
                'holdings_count': len(target_weights),
                'selected_count': scheduled['selected_count'],
                'selected_symbols': scheduled['selected_symbols'],
            })

        frame = daily_frames.get(trade_date)
        day_return = 0.0
        if frame is not None and current_weights:
            for symbol, weight in current_weights.items():
                if symbol not in frame.index:
                    continue
                pct_chg = frame.at[symbol, 'pct_chg']
                if pd.notna(pct_chg):
                    day_return += float(weight) * (float(pct_chg) / 100.0)
        equity *= (1.0 + day_return)
        daily_rows.append({
            'trade_date': trade_date,
            'equity': equity,
            'nav': equity / float(initial_capital) if initial_capital else 0.0,
            'daily_return': day_return,
            'holdings_count': len(current_weights),
        })

        spec = rebalance_targets.get(trade_date)
        if spec is not None:
            effective_date = next_trade_date.get(trade_date)
            if effective_date is not None:
                pending[effective_date] = spec

    return pd.DataFrame(daily_rows), pd.DataFrame(rebalance_rows)


def compute_max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    drawdown = nav / nav.cummax() - 1.0
    return float(drawdown.min())


def annualized_return(total_return: float, trade_days: int) -> float:
    if trade_days <= 0:
        return 0.0
    return math.pow(1.0 + total_return, 252.0 / trade_days) - 1.0 if total_return > -1.0 else -1.0


def build_report_text(summary: dict, config: dict) -> str:
    lines = [
        'A-Share Multi-Factor LLM Quant Backtest',
        f"Date Range: {summary['start_date']} -> {summary['end_date']}",
        f"Benchmark: {summary['benchmark_symbol']}",
        f"Universe Index: {summary['index_code']}",
        f"Top N Holdings: {summary['top_n']}",
        f"Initial Capital: {summary['initial_capital']:,.2f}",
        f"Fee Rate: {summary['fee_rate']:.4f}",
        'Results:',
        f"- Strategy Total Return: {summary['strategy_total_return']:.2%}",
        f"- Benchmark Total Return: {summary['benchmark_total_return']:.2%}",
        f"- Excess Return: {summary['excess_return']:.2%}",
        f"- Strategy Max Drawdown: {summary['strategy_max_drawdown']:.2%}",
        f"- Benchmark Max Drawdown: {summary['benchmark_max_drawdown']:.2%}",
        f"- Drawdown Advantage: {summary['drawdown_advantage']:.2%}",
        f"- Strategy Annualized Return: {summary['strategy_annualized_return']:.2%}",
        f"- Benchmark Annualized Return: {summary['benchmark_annualized_return']:.2%}",
        f"- Rebalance Count: {summary['rebalance_count']}",
        f"- Average Turnover: {summary['average_turnover']:.2f}",
        'Strategy Constraints:',
        f"- Positive Flow Required: {bool(config.get('require-positive-flow', True))}",
        f"- Positive Momentum Required: {bool(config.get('require-positive-momentum', False))}",
        f"- Min Price: {float(config.get('min-price', 5.0)):.2f}",
        f"- Min Circ MV: {float(config.get('min-circ-mv', 1000000.0)):.0f}",
        f"- Min Turnover Rate F: {float(config.get('min-turnover-rate-f', 0.3)):.2f}",
        f"- Max 20d Volatility: {float(config.get('max-vol20', 0.045)):.4f}",
        f"- Risk Off Exposure: {float(config.get('risk-off-exposure', 0.4)):.2f}",
    ]
    return '\n'.join(lines) + '\n'


def run_backtest(config: dict) -> dict:
    layer = TushareDataLayer(config['tushare-data-path'], build_dataset_catalog(config))
    benchmark = load_benchmark_frame(layer, config)
    weights = load_index_weight_frame(layer, config)
    rebalance_dates, rebalance_to_snapshot, snapshots = build_constituent_schedule(benchmark, weights)
    universe = sorted(weights['con_code'].dropna().astype(str).unique().tolist()) if not weights.empty else []
    panel = build_factor_panel(layer, universe, warmup_start_date(benchmark, config), config['end-date'])
    panel = panel[panel['trade_date'] >= config['start-date']].copy()
    by_date = {
        trade_date: group.set_index('symbol')
        for trade_date, group in panel.groupby('trade_date', sort=True)
    }
    target_specs = build_target_specs(benchmark, panel, rebalance_dates, rebalance_to_snapshot, snapshots, config)
    daily, rebalances = simulate_portfolio(
        benchmark['trade_date'].tolist(),
        by_date,
        target_specs,
        fee_rate=float(config.get('fee-rate', 0.0013) or 0.0013),
        initial_capital=float(config.get('initial-capital', 1000000) or 1000000),
    )

    benchmark_nav = (1.0 + benchmark['benchmark_return'].fillna(0.0)).cumprod()
    benchmark_nav.index = benchmark['trade_date']
    if not daily.empty:
        daily = daily.merge(
            benchmark[['trade_date', 'benchmark_return']],
            on='trade_date',
            how='left',
        )
        daily['benchmark_nav'] = benchmark_nav.reindex(daily['trade_date']).to_numpy()
        daily['strategy_drawdown'] = daily['nav'] / daily['nav'].cummax() - 1.0
        daily['benchmark_drawdown'] = daily['benchmark_nav'] / daily['benchmark_nav'].cummax() - 1.0

    strategy_total_return = float(daily['nav'].iloc[-1] - 1.0) if not daily.empty else 0.0
    benchmark_total_return = float(benchmark_nav.iloc[-1] - 1.0) if not benchmark_nav.empty else 0.0
    strategy_max_drawdown = compute_max_drawdown(daily['nav']) if not daily.empty else 0.0
    benchmark_max_drawdown = compute_max_drawdown(benchmark_nav) if not benchmark_nav.empty else 0.0
    trade_days = int(len(daily))
    summary = {
        'start_date': config['start-date'],
        'end_date': config['end-date'],
        'benchmark_symbol': config['benchmark-symbol'],
        'index_code': config['index-code'],
        'top_n': int(config.get('top-n', 10) or 10),
        'initial_capital': float(config.get('initial-capital', 1000000) or 1000000),
        'fee_rate': float(config.get('fee-rate', 0.0013) or 0.0013),
        'universe_size': len(universe),
        'trade_days': trade_days,
        'rebalance_count': int(len(rebalances)),
        'strategy_total_return': strategy_total_return,
        'benchmark_total_return': benchmark_total_return,
        'excess_return': strategy_total_return - benchmark_total_return,
        'strategy_max_drawdown': strategy_max_drawdown,
        'benchmark_max_drawdown': benchmark_max_drawdown,
        'drawdown_advantage': abs(benchmark_max_drawdown) - abs(strategy_max_drawdown),
        'strategy_annualized_return': annualized_return(strategy_total_return, trade_days),
        'benchmark_annualized_return': annualized_return(benchmark_total_return, trade_days),
        'average_turnover': float(rebalances['turnover'].mean()) if not rebalances.empty else 0.0,
        'loaded_symbol_count': int(panel['symbol'].nunique()) if not panel.empty else 0,
    }

    daily_nav_file = config.get('daily-nav-file')
    if daily_nav_file:
        path = Path(daily_nav_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(path, index=False, float_format='%.10f')

    rebalance_file = config.get('rebalance-file')
    if rebalance_file:
        path = Path(rebalance_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        rebalances.to_csv(path, index=False, float_format='%.10f')

    summary_file = config.get('summary-file')
    if summary_file:
        path = Path(summary_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    report_file = config.get('report-file')
    if report_file:
        path = Path(report_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_report_text(summary, config), encoding='utf-8')

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run A-share multi-factor quant backtest on Tushare parquet data')
    parser.add_argument('--config')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--report-file')
    parser.add_argument('--daily-nav-file')
    parser.add_argument('--rebalance-file')
    parser.add_argument('--summary-file')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'start-date': args.start_date,
        'end-date': args.end_date,
        'report-file': args.report_file,
        'daily-nav-file': args.daily_nav_file,
        'rebalance-file': args.rebalance_file,
        'summary-file': args.summary_file,
    }
    summary = run_backtest(load_pipeline_config(args.config, overrides))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
