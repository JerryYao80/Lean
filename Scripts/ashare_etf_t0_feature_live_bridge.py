#!/usr/bin/env python3

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
TUSHARE_MODULE_DIR = Path(__file__).resolve().parents[1] / 'data-source' / 'tushare'
if str(TUSHARE_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(TUSHARE_MODULE_DIR))

from ashare_etf_t0_feature_backtest import build_etf_metadata_lookup
import ashare_live_market_cache as live_market_cache
from export_ashare_etf_t0_feature_data import FEATURE_COLUMNS, build_feature_frame, feature_daily_path
from rt_daily_downloader import TushareRtDailyClient
from tushare_data_layer import TushareDataLayer
from tushare_lean_export import load_registry_universe


PATH_KEYS = {
    'registry-file',
    'tushare-data-path',
    'dataset-catalog',
    'feature-data-path',
    'live-feature-report-file',
    'daily-quote-archive-path',
    'shared-live-market-snapshot-file',
    'shared-live-market-report-file',
    'shared-live-market-archive-path',
}
LIVE_FEATURE_COLUMNS = [*FEATURE_COLUMNS, 'feature_timestamp']
SIGNAL_SOURCE_FIELDS = {
    'signal_momentum_20': 'momentum_20',
    'signal_momentum_5': 'momentum_5',
    'signal_liquidity_5': 'liquidity_5',
    'signal_close_location': 'close_location',
    'signal_volatility_10': 'volatility_10',
    'signal_gap_abs': 'gap_abs',
    'signal_nav_premium_1': 'nav_premium_1',
    'signal_nav_premium_z20': 'nav_premium_z20',
    'signal_share_change_5': 'share_change_5',
    'signal_size_change_5': 'size_change_5',
    'signal_excess_gap': 'excess_gap',
    'signal_excess_intraday': 'excess_intraday',
    'signal_tracking_error_10': 'tracking_error_10',
    'signal_index_momentum_5': 'index_momentum_5',
}
CARRY_FORWARD_FIELDS = [
    'unit_nav',
    'adj_nav',
    'total_share',
    'total_size',
    'nav_premium_1',
    'nav_premium_z20',
    'share_change_5',
    'size_change_5',
    'index_gap_return',
    'index_trade_return',
    'index_close_return_1',
    'index_momentum_5',
    'excess_gap',
    'excess_intraday',
    'tracking_error_10',
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_workdir() -> Path:
    return repo_root() / 'Launcher' / 'bin' / 'Debug'


def default_config() -> dict:
    root = repo_root()
    return {
        'registry-file': str(root / 'Common' / 'Securities' / 'Equity' / 'AShareETFMetadata.cs'),
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data',
        'dataset-catalog': str(root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'),
        'feature-data-path': str(root / 'Data' / 'alternative' / 'ashare-etf-t0-live-features'),
        'daily-quote-archive-path': str(root / 'Data' / 'archive' / 'daily_quotes'),
        'shared-live-market-snapshot-file': str(live_market_cache.default_shared_snapshot_file()),
        'shared-live-market-report-file': str(live_market_cache.default_shared_report_file()),
        'shared-live-market-archive-path': str(live_market_cache.default_shared_archive_path()),
        'start-date': '20240101',
        'end-date': '20261231',
        'exclude-money-market-etfs': True,
        'tushare-token': '',
        'tushare-http-url': '',
        'tushare-token-env-var': 'TUSHARE_TOKEN',
        'live-feature-poll-interval-seconds': 30,
        'live-feature-quote-batch-size': 25,
        'live-feature-minute-frequency': '1MIN',
        'shared-live-market-refresh-interval-seconds': 60,
        'shared-live-market-batch-size': 200,
        'shared-live-market-max-workers': 1,
        'shared-live-market-max-requests-per-minute': 50,
        'live-price-source-mode': 'auto',
        'market-symbol': '000300.SH',
        'simulated-live-price-random-seed': 20260317,
        'simulated-live-price-lookback-days': 60,
        'simulated-live-price-min-history-days': 20,
        'simulated-live-price-trading-minutes-per-day': 240,
        'simulated-live-price-volatility-scale': 8.0,
        'simulated-live-price-min-daily-volatility': 0.80,
        'simulated-live-price-jump-probability': 0.22,
        'simulated-live-price-jump-scale': 0.10,
        'timezone': 'Asia/Shanghai',
        'live-feature-bootstrap-history': True,
        'live-feature-report-file': str(root / 'Results' / 'ashare-etf-t0-feature-live-bridge-report.json'),
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


def _coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _coerce_int(value, default: int) -> int:
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
        loaded = json.loads(path.read_text(encoding='utf-8'))
        if 'parameters' in loaded and isinstance(loaded['parameters'], dict):
            loaded = loaded['parameters']
            config.update(resolve_config_paths(loaded, launcher_workdir()))
        else:
            config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    config = resolve_config_paths(config, repo_root())
    config['exclude-money-market-etfs'] = _coerce_bool(config.get('exclude-money-market-etfs'), True)
    config['live-feature-bootstrap-history'] = _coerce_bool(config.get('live-feature-bootstrap-history'), True)
    config['live-feature-poll-interval-seconds'] = _coerce_int(config.get('live-feature-poll-interval-seconds'), 30)
    config['live-feature-quote-batch-size'] = _coerce_int(config.get('live-feature-quote-batch-size'), 25)
    config['live-feature-minute-frequency'] = str(config.get('live-feature-minute-frequency') or '1MIN').strip().upper()
    config['shared-live-market-refresh-interval-seconds'] = _coerce_int(config.get('shared-live-market-refresh-interval-seconds'), 60)
    config['shared-live-market-batch-size'] = _coerce_int(config.get('shared-live-market-batch-size'), 200)
    config['shared-live-market-max-workers'] = _coerce_int(config.get('shared-live-market-max-workers'), 1)
    config['shared-live-market-max-requests-per-minute'] = _coerce_int(config.get('shared-live-market-max-requests-per-minute'), 50)
    return config


def resolve_tushare_token(config: dict, explicit_token: str | None = None) -> str:
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()

    configured_token = str(config.get('tushare-token') or '').strip()
    if configured_token:
        return configured_token

    env_var = str(config.get('tushare-token-env-var') or 'TUSHARE_TOKEN')
    token = os.getenv(env_var, '').strip()
    if token:
        return token

    raise RuntimeError(f'Missing Tushare token. Please set {env_var}, configure `tushare-token`, or pass --token.')


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_positive_number(value) -> bool:
    numeric = _to_float(value)
    return numeric is not None and numeric > 0


def _ratio_minus_one(numerator, denominator) -> float | None:
    numer = _to_float(numerator)
    denom = _to_float(denominator)
    if numer is None or denom is None or math.isclose(denom, 0.0):
        return None
    return numer / denom - 1.0


def _rolling_std(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    series = pd.Series(values[-window:], dtype='float64').dropna()
    if len(series) < window:
        return None
    return float(series.std(ddof=0))


def _rolling_mean(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    series = pd.Series(values[-window:], dtype='float64').dropna()
    if len(series) < window:
        return None
    return float(series.mean())


def _window_return(values: list[float], periods: int, current_value: float | None) -> float | None:
    if current_value is None or len(values) < periods:
        return None
    denominator = _to_float(values[-periods])
    if denominator is None or math.isclose(denominator, 0.0):
        return None
    return current_value / denominator - 1.0


def _format_trade_date(value: str | datetime | None = None) -> str:
    if isinstance(value, datetime):
        return value.strftime('%Y%m%d')
    if value is None:
        return datetime.now().strftime('%Y%m%d')
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return pd.Timestamp(text).strftime('%Y%m%d')


def _format_timestamp(value: str | datetime | None = None) -> str:
    if value is None:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    text = str(value).strip()
    if not text:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return pd.Timestamp(text).strftime('%Y-%m-%d %H:%M:%S')


def _carry_forward(previous_row: pd.Series, field: str):
    return previous_row.get(field) if field in previous_row.index else None


TushareRealtimeQuoteClient = TushareRtDailyClient


class TushareRealtimeMinuteClient:
    """Compatibility shim retained for minute-bar tests and older tooling."""

    def __init__(self, token: str, batch_size: int = 25, frequency: str = '1MIN', http_url: str | None = None):
        self._token = token
        self._batch_size = max(1, int(batch_size))
        self._frequency = str(frequency or '1MIN').strip().upper()
        self._http_url = http_url
        self._api = None

    def _create_api(self):
        import tushare as ts  # type: ignore

        api = ts.pro_api(self._token)
        api._DataApi__token = self._token
        if self._http_url:
            api._DataApi__http_url = self._http_url
        return api

    @property
    def api(self):
        if self._api is None:
            self._api = self._create_api()
        return self._api

    def _fetch_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        return self.api.rt_min(ts_code=','.join(ts_codes), freq=self._frequency)

    def fetch_quotes(self, ts_codes: list[str]) -> pd.DataFrame:
        symbols = [str(ts_code).strip().upper() for ts_code in ts_codes if str(ts_code).strip()]
        frames: list[pd.DataFrame] = []
        for start in range(0, len(symbols), self._batch_size):
            batch = symbols[start:start + self._batch_size]
            try:
                frame = self._fetch_batch(batch)
                if frame is not None and not frame.empty:
                    frames.append(frame)
                continue
            except Exception:
                pass

            for symbol in batch:
                frame = self.api.rt_min(ts_code=symbol, freq=self._frequency)
                if frame is not None and not frame.empty:
                    frames.append(frame)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        combined.columns = [str(column).strip().lower() for column in combined.columns]
        if 'ts_code' in combined.columns:
            combined['ts_code'] = combined['ts_code'].astype(str).str.strip().str.upper()
        return combined

    @staticmethod
    def aggregate_minute_bars(minute_frame: pd.DataFrame) -> pd.DataFrame:
        if minute_frame is None or minute_frame.empty:
            return pd.DataFrame()
        frame = minute_frame.copy()
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        frame['ts_code'] = frame['ts_code'].astype(str).str.strip().str.upper()
        frame['trade_time'] = pd.to_datetime(frame['trade_time'])
        grouped_rows: list[dict[str, object]] = []
        for ts_code, group in frame.sort_values(['ts_code', 'trade_time']).groupby('ts_code'):
            grouped_rows.append({
                'ts_code': ts_code,
                'trade_date': group['trade_time'].dt.strftime('%Y%m%d').iloc[-1],
                'feature_timestamp': group['trade_time'].dt.strftime('%Y-%m-%d %H:%M:%S').iloc[-1],
                'open': float(group['open'].iloc[0]),
                'high': float(group['high'].max()),
                'low': float(group['low'].min()),
                'close': float(group['close'].iloc[-1]),
                'price': float(group['close'].iloc[-1]),
                'vol': float(group['vol'].sum()),
                'amount': float(group['amount'].sum()),
            })
        return pd.DataFrame(grouped_rows).sort_values('ts_code').reset_index(drop=True)


def build_history_cache(config: dict, session_date: str | None = None) -> dict[str, pd.DataFrame]:
    layer = TushareDataLayer(config['tushare-data-path'], config['dataset-catalog'])
    metadata_lookup = build_etf_metadata_lookup(layer)
    universe = load_registry_universe(
        config['registry-file'],
        exclude_money_market=config.get('exclude-money-market-etfs', True),
    )
    effective_end_date = session_date or config.get('end-date')
    cache: dict[str, pd.DataFrame] = {}
    for ts_code in universe:
        frame = build_feature_frame(
            layer,
            ts_code,
            start_date=config.get('start-date'),
            end_date=effective_end_date,
            metadata_lookup=metadata_lookup,
        )
        if not frame.empty:
            cache[ts_code] = frame
    return cache


def prepare_live_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    if prepared.empty:
        prepared = pd.DataFrame(columns=LIVE_FEATURE_COLUMNS)

    if 'trade_date' in prepared.columns:
        prepared['trade_date'] = prepared['trade_date'].astype(str).str.zfill(8)

    for column in LIVE_FEATURE_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = None

    return prepared[LIVE_FEATURE_COLUMNS].copy()


def feature_path_to_ts_code(feature_file: str | Path) -> str:
    path = Path(feature_file)
    ticker = path.stem.strip()
    market = path.parent.parent.name.lower()

    if not ticker:
        raise ValueError(f'Unable to determine ticker from feature path: {path}')
    if market == 'sse':
        return f'{ticker}.SH'
    if market == 'szse':
        return f'{ticker}.SZ'
    raise ValueError(f'Unable to determine market from feature path: {path}')


def merge_live_feature_history(base_history: pd.DataFrame, existing_frame: pd.DataFrame | None = None) -> pd.DataFrame:
    frames = []
    if base_history is not None:
        frames.append(prepare_live_feature_frame(base_history))
    if existing_frame is not None and not existing_frame.empty:
        frames.append(prepare_live_feature_frame(existing_frame))

    if not frames:
        return prepare_live_feature_frame(pd.DataFrame())

    merged = pd.concat(frames, ignore_index=True)
    merged['trade_date'] = merged['trade_date'].astype(str).str.zfill(8)
    merged['feature_timestamp'] = merged['feature_timestamp'].fillna('').astype(str)
    merged = merged.sort_values(['trade_date', 'feature_timestamp'], kind='stable')
    merged = merged.drop_duplicates(subset=['trade_date'], keep='last').reset_index(drop=True)
    return merged[LIVE_FEATURE_COLUMNS].copy()


def bootstrap_live_feature_files(config: dict, history_cache: dict[str, pd.DataFrame]) -> int:
    written = 0
    for ts_code, base_history in history_cache.items():
        if base_history is None or base_history.empty:
            continue
        feature_path = feature_daily_path(config['feature-data-path'], ts_code)
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        existing = pd.read_csv(feature_path, dtype={'trade_date': str}) if feature_path.exists() else pd.DataFrame()
        prepared = merge_live_feature_history(base_history, existing)
        prepared.to_csv(feature_path, index=False, float_format='%.10f')
        written += 1
    return written


def build_live_feature_row(base_history: pd.DataFrame, quote: dict, session_date: str | None = None, feature_timestamp: str | None = None) -> dict | None:
    if base_history is None or base_history.empty:
        return None

    history = base_history.copy()
    history['trade_date'] = history['trade_date'].astype(str).str.zfill(8)

    session_trade_date = _format_trade_date(session_date or quote.get('trade_date') or feature_timestamp)
    feature_time = _format_timestamp(feature_timestamp or quote.get('feature_timestamp'))

    prior_candidates = history[history['trade_date'] < session_trade_date]
    if prior_candidates.empty:
        prior_candidates = history
    if prior_candidates.empty:
        return None

    previous_row = prior_candidates.iloc[-1]
    close_history = [_to_float(value) for value in prior_candidates['close'].tolist() if _to_float(value) is not None]
    pct_history = [_to_float(value) for value in prior_candidates['pct_chg'].tolist() if _to_float(value) is not None]
    amount_history = [_to_float(value) for value in prior_candidates['amount'].tolist() if _to_float(value) is not None]

    pre_close = _to_float(quote.get('pre_close')) or _to_float(previous_row.get('close'))
    open_price = _to_float(quote.get('open')) or pre_close
    close_price = _to_float(quote.get('price') or quote.get('close')) or open_price
    high_price = _to_float(quote.get('high'))
    low_price = _to_float(quote.get('low'))

    if high_price is None:
        high_price = max(value for value in [open_price, close_price] if _is_positive_number(value)) if _is_positive_number(open_price) or _is_positive_number(close_price) else None
    if low_price is None:
        low_price = min(value for value in [open_price, close_price] if _is_positive_number(value)) if _is_positive_number(open_price) or _is_positive_number(close_price) else None

    pct_chg = _to_float(quote.get('pct_chg'))
    if pct_chg is None:
        trade_pct = _ratio_minus_one(close_price, pre_close)
        pct_chg = trade_pct * 100.0 if trade_pct is not None else None

    amount = _to_float(quote.get('amount'))
    volume = _to_float(quote.get('vol') or quote.get('volume'))
    trade_return = _ratio_minus_one(close_price, open_price)
    gap_return = _ratio_minus_one(open_price, pre_close)

    price_range = None
    if _is_positive_number(high_price) and _is_positive_number(low_price):
        price_range = float(high_price) - float(low_price)
    if price_range is None or math.isclose(price_range, 0.0):
        close_location = 0.5
    else:
        close_location = min(max((float(close_price) - float(low_price)) / price_range, 0.0), 1.0)

    range_pct = None
    if _is_positive_number(pre_close) and _is_positive_number(high_price) and _is_positive_number(low_price):
        range_pct = (float(high_price) / float(pre_close) - 1.0) - (float(low_price) / float(pre_close) - 1.0)

    momentum_5 = _window_return(close_history, 5, close_price)
    momentum_20 = _window_return(close_history, 20, close_price)
    volatility_10 = _rolling_std([*pct_history, pct_chg] if pct_chg is not None else pct_history, 10)
    liquidity_5 = _rolling_mean([*amount_history, amount] if amount is not None else amount_history, 5)
    gap_abs = abs(gap_return) if gap_return is not None else None

    row = {
        'trade_date': session_trade_date,
        'pre_close': pre_close,
        'open': open_price,
        'high': high_price,
        'low': low_price,
        'close': close_price,
        'pct_chg': pct_chg,
        'amount': amount,
        'vol': volume,
        'trade_return': trade_return,
        'gap_return': gap_return,
        'close_location': close_location,
        'range_pct': range_pct,
        'momentum_5': momentum_5,
        'momentum_20': momentum_20,
        'volatility_10': volatility_10,
        'liquidity_5': liquidity_5,
        'gap_abs': gap_abs,
        'feature_timestamp': feature_time,
    }

    for field in CARRY_FORWARD_FIELDS:
        row[field] = _carry_forward(previous_row, field)

    unit_nav = _to_float(row.get('unit_nav'))
    if unit_nav is not None and unit_nav > 0 and close_price is not None:
        row['nav_premium_1'] = close_price / unit_nav - 1.0

    if row.get('index_gap_return') is not None and gap_return is not None:
        row['excess_gap'] = gap_return - float(row['index_gap_return'])
    if row.get('index_trade_return') is not None and trade_return is not None:
        row['excess_intraday'] = trade_return - float(row['index_trade_return'])

    for signal_field, source_field in SIGNAL_SOURCE_FIELDS.items():
        row[signal_field] = _carry_forward(previous_row, source_field)

    for column in LIVE_FEATURE_COLUMNS:
        row.setdefault(column, None)

    return row


def upsert_live_feature_file(feature_path: str | Path, base_history: pd.DataFrame, live_row: dict) -> pd.DataFrame:
    target = Path(feature_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        frame = pd.read_csv(target, dtype={'trade_date': str})
    else:
        frame = base_history.copy()

    frame = prepare_live_feature_frame(frame)

    trade_date = str(live_row['trade_date']).zfill(8)
    frame = frame[frame['trade_date'] != trade_date].copy()
    row_values = {column: live_row.get(column) for column in LIVE_FEATURE_COLUMNS}

    if frame.empty:
        frame = pd.DataFrame([row_values], columns=LIVE_FEATURE_COLUMNS)
    else:
        records = frame.to_dict('records')
        records.append(row_values)
        frame = pd.DataFrame.from_records(records, columns=LIVE_FEATURE_COLUMNS)

    frame['trade_date'] = frame['trade_date'].astype(str).str.zfill(8)
    frame = frame.sort_values(['trade_date', 'feature_timestamp'], kind='stable').drop_duplicates(subset=['trade_date'], keep='last').reset_index(drop=True)
    frame.to_csv(target, index=False, float_format='%.10f')
    return frame


def refresh_live_feature_snapshots(
    config: dict,
    quote_client=None,
    history_cache: dict[str, pd.DataFrame] | None = None,
    market_context: dict | None = None,
    realtime_client=None,
    simulated_client=None,
    current_time: datetime | None = None,
) -> dict:
    now = current_time or datetime.now()
    session_date = now.strftime('%Y%m%d')
    history_cache = history_cache or {}
    universe = sorted(history_cache.keys())

    print(f"\n{'='*80}")
    print(f"🔄 Refreshing live features at {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 Session: {session_date} | Universe: {len(universe)} symbols")
    print(f"{'='*80}")

    shared_snapshot = None
    if market_context is not None:
        shared_snapshot = live_market_cache.ensure_full_market_snapshot(
            config,
            session_date,
            market_context,
            realtime_client=realtime_client if realtime_client is not None else quote_client,
            simulated_client=simulated_client,
        )
        quotes = live_market_cache.filter_quotes(shared_snapshot.get('quotes'), universe)
        refreshed_quotes = live_market_cache.filter_quotes(shared_snapshot.get('fresh_quotes'), universe)
    else:
        if quote_client is None:
            raise RuntimeError('quote_client is required when market_context is not provided.')
        quotes = quote_client.fetch_quotes(universe)
        refreshed_quotes = quotes.copy()

    if quotes.empty:
        print("\n⚠️  No quotes received from API")
        report = {
            'status': 'no_quotes',
            'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
            'session_date': session_date,
            'quote_count': 0,
            'written_count': 0,
            'skipped_symbols': universe,
            'feature_data_path': config['feature-data-path'],
        }
        return report

    if 'ts_code' not in quotes.columns:
        raise RuntimeError('Realtime quote response must include ts_code column.')

    written = 0
    skipped: list[str] = []
    updated: list[str] = []

    print(f"\n📝 Processing {len(quotes)} quotes...")
    print(f"{'─'*80}")

    for idx, (_, quote_row) in enumerate(quotes.iterrows(), 1):
        ts_code = str(quote_row.get('ts_code') or '').strip()
        if not ts_code or ts_code not in history_cache:
            if ts_code:
                skipped.append(ts_code)
            continue

        live_row = build_live_feature_row(history_cache[ts_code], quote_row.to_dict(), session_date=session_date)
        if live_row is None:
            skipped.append(ts_code)
            print(f"  [{idx}/{len(quotes)}] {ts_code:12s} ❌ Failed to build feature row")
            continue

        feature_path = feature_daily_path(config['feature-data-path'], ts_code)
        upsert_live_feature_file(feature_path, history_cache[ts_code], live_row)
        written += 1
        updated.append(ts_code)

        # Display feature summary
        close = live_row.get('close')
        pct_chg = live_row.get('pct_chg')
        momentum_5 = live_row.get('momentum_5')
        volatility_10 = live_row.get('volatility_10')

        status_line = f"  [{idx}/{len(quotes)}] {ts_code:12s} ✅"
        if close is not None:
            status_line += f" ¥{close:8.2f}"
        if pct_chg is not None:
            arrow = "📈" if pct_chg > 0 else "📉" if pct_chg < 0 else "➡️"
            status_line += f" {arrow} {pct_chg:+6.2f}%"
        if momentum_5 is not None:
            status_line += f" | M5: {momentum_5*100:+6.2f}%"
        if volatility_10 is not None:
            status_line += f" | Vol: {volatility_10:5.2f}"

        print(status_line)

    print(f"{'─'*80}")
    print(f"✅ Written: {written} | ⏭️  Skipped: {len(skipped)}")
    print(f"{'='*80}\n")

    # Archive raw quotes for permanent storage
    archive_daily_quotes(config, quotes, session_date)

    # Preview trading signals (read-only, no actual trading)
    signal_preview = preview_trading_signals(config, Path(config['feature-data-path']), quotes)

    report = {
        'status': 'ok',
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'session_date': session_date,
        'quote_count': int(len(quotes.index)),
        'refreshed_quote_count': int(len(refreshed_quotes.index)),
        'written_count': written,
        'updated_symbols': updated,
        'skipped_symbols': skipped,
        'feature_data_path': config['feature-data-path'],
        'signal_preview': signal_preview,
    }
    if shared_snapshot is not None:
        report.update({
            'shared_market_requested_symbol_count': int(((shared_snapshot.get('snapshot_payload') or {}).get('requested_symbol_count') or 0)),
            'shared_market_received_quote_count': int(((shared_snapshot.get('snapshot_payload') or {}).get('received_quote_count') or 0)),
            'shared_market_refreshed_quote_count': int(((shared_snapshot.get('snapshot_payload') or {}).get('refreshed_quote_count') or 0)),
            'shared_market_cached': bool(shared_snapshot.get('used_cached_snapshot')),
            'shared_live_market_snapshot_file': config.get('shared-live-market-snapshot-file'),
        })
    return report


def write_report(config: dict, report: dict) -> None:
    report_path = Path(config['live-feature-report-file'])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def archive_daily_quotes(config: dict, quotes: pd.DataFrame, session_date: str) -> None:
    """Archive raw daily quotes to permanent storage."""
    if quotes.empty:
        return

    archive_root = Path(config.get('daily-quote-archive-path', repo_root() / 'Data' / 'archive' / 'daily_quotes'))
    archive_root.mkdir(parents=True, exist_ok=True)

    # Save as parquet partitioned by date
    date_partition = archive_root / f"date={session_date}"
    date_partition.mkdir(parents=True, exist_ok=True)

    archive_file = date_partition / f"quotes_{session_date}.parquet"

    # If file exists, merge with existing data
    if archive_file.exists():
        existing = pd.read_parquet(archive_file)
        combined = pd.concat([existing, quotes], ignore_index=True)
        # Deduplicate by ts_code, keep latest
        combined = combined.drop_duplicates(subset=['ts_code'], keep='last')
        combined.to_parquet(archive_file, engine='pyarrow', index=False)
    else:
        quotes.to_parquet(archive_file, engine='pyarrow', index=False)


def preview_trading_signals(config: dict, feature_data_path: Path, quotes: pd.DataFrame) -> dict:
    """
    Preview trading signals based on current features (read-only, no trading).
    Uses the same logic as the C# algorithm for consistency.
    """
    print(f"\n{'='*120}")
    print(f"📊 SIGNAL GENERATION & PORTFOLIO PREVIEW")
    print(f"{'='*120}")

    # Load configuration
    top_n = int(config.get('top-n', 2))
    min_score_spread = float(config.get('min-score-spread', 0.7))
    max_average_gap_abs = config.get('max-average-gap-abs')
    if max_average_gap_abs is not None:
        max_average_gap_abs = float(max_average_gap_abs)

    target_exposure = float(config.get('target-portfolio-exposure', 0.95))
    initial_capital = 1_000_000.0  # 初始资金 100万

    # Signal weights (matching C# algorithm)
    signal_weights = {
        'signal_momentum_20': 0.25,
        'signal_momentum_5': 0.20,
        'signal_liquidity_5': 0.15,
        'signal_close_location': 0.10,
        'signal_volatility_10': -0.10,
        'signal_nav_premium_z20': -0.10,
        'signal_excess_intraday': 0.10,
        'signal_index_momentum_5': 0.10,
    }

    # Load ETF registry for names
    etf_names = {}
    try:
        from tushare_lean_export import load_registry_universe
        registry_file = config.get('registry-file')
        if registry_file:
            # Parse registry file for ETF names (simplified)
            pass
    except:
        pass

    # Load all feature files
    scored_symbols = []
    feature_files = list(feature_data_path.glob('*/*/*.csv'))

    if not feature_files:
        print("\n⚠️  No feature files found")
        print(f"{'='*120}\n")
        return {'status': 'no_features', 'signals': []}

    print(f"\n🔍 Analyzing {len(feature_files)} symbols...")

    for feature_file in feature_files:
        try:
            df = pd.read_csv(feature_file, dtype={'trade_date': str})
            if df.empty:
                continue

            # Get latest row
            latest = df.iloc[-1]
            ts_code = feature_path_to_ts_code(feature_file)

            # Filter by gap_abs if configured
            gap_abs = latest.get('gap_abs')
            if max_average_gap_abs is not None and gap_abs is not None:
                if not pd.isna(gap_abs) and float(gap_abs) > max_average_gap_abs:
                    continue

            # Calculate composite score
            score = 0.0
            total_weight = 0.0
            feature_values = {}

            for feature_name, weight in signal_weights.items():
                value = latest.get(feature_name)
                if value is not None and not pd.isna(value):
                    score += float(value) * weight
                    total_weight += abs(weight)
                    feature_values[feature_name] = float(value)

            if total_weight > 0:
                score = score / total_weight

            scored_symbols.append({
                'ts_code': ts_code,
                'score': score,
                'close': latest.get('close'),
                'pct_chg': latest.get('pct_chg'),
                'features': feature_values,
            })

        except Exception as e:
            continue

    if not scored_symbols:
        print("\n⚠️  No valid symbols after filtering")
        print(f"{'='*120}\n")
        return {'status': 'no_valid_symbols', 'signals': []}

    # Sort by score descending
    scored_symbols.sort(key=lambda x: x['score'], reverse=True)

    # Display top symbols
    print(f"\n📈 SYMBOL RANKING (Total: {len(scored_symbols)})")
    print(f"{'─'*120}")
    print(f"  {'Rank':>4s} | {'Code':12s} | {'Score':>10s} | {'Price':>10s} | {'Change':>8s} | {'Signal'}")
    print(f"{'─'*120}")

    signals = []
    buy_signals = []
    sell_signals = []

    # Determine buy signals
    if len(scored_symbols) >= top_n:
        top_score = scored_symbols[0]['score']
        nth_score = scored_symbols[top_n - 1]['score']
        score_spread = top_score - nth_score

        if score_spread >= min_score_spread:
            # Strong signals - buy top N
            target_weight = 1.0 / top_n
            for i in range(top_n):
                symbol = scored_symbols[i]
                buy_signals.append({
                    'ts_code': symbol['ts_code'],
                    'signal': 'BUY',
                    'rank': i + 1,
                    'score': symbol['score'],
                    'target_weight': target_weight,
                    'close': symbol['close'],
                    'pct_chg': symbol['pct_chg'],
                })
                signals.append(buy_signals[-1])

            # Sell signals for others
            for i in range(top_n, len(scored_symbols)):
                symbol = scored_symbols[i]
                sell_signals.append({
                    'ts_code': symbol['ts_code'],
                    'signal': 'SELL',
                    'rank': i + 1,
                    'score': symbol['score'],
                    'target_weight': 0.0,
                    'close': symbol['close'],
                    'pct_chg': symbol['pct_chg'],
                })
                signals.append(sell_signals[-1])

            print(f"\n  ✅ Score spread: {score_spread:.4f} >= {min_score_spread:.4f} (threshold)")
            print(f"  🎯 Generating BUY signals for top {top_n} symbols")
        else:
            print(f"\n  ⚠️  Score spread: {score_spread:.4f} < {min_score_spread:.4f} (threshold)")
            print(f"  ⏸️  No trading signals - spread too narrow")
    else:
        print(f"\n  ⚠️  Only {len(scored_symbols)} symbols available (need {top_n})")
        print(f"  ⏸️  No trading signals - insufficient symbols")

    # Display top symbols
    display_count = min(20, len(scored_symbols))
    for i in range(display_count):
        symbol = scored_symbols[i]
        rank = i + 1

        # Determine signal
        signal_str = "HOLD"
        signal_emoji = "⚪"
        if any(s['ts_code'] == symbol['ts_code'] and s['signal'] == 'BUY' for s in buy_signals):
            signal_str = "BUY"
            signal_emoji = "🟢"
        elif any(s['ts_code'] == symbol['ts_code'] and s['signal'] == 'SELL' for s in sell_signals):
            signal_str = "SELL"
            signal_emoji = "🔴"

        close = symbol.get('close')
        pct_chg = symbol.get('pct_chg')

        close_str = f"¥{close:8.2f}" if close is not None else "N/A"
        pct_str = f"{pct_chg:+6.2f}%" if pct_chg is not None else "N/A"

        print(f"  {rank:4d} | {symbol['ts_code']:12s} | {symbol['score']:10.4f} | {close_str:>10s} | {pct_str:>8s} | {signal_emoji} {signal_str}")

    if len(scored_symbols) > display_count:
        print(f"  ... and {len(scored_symbols) - display_count} more symbols")

    print(f"{'─'*120}")

    # Calculate portfolio statistics
    print(f"\n{'='*120}")
    print(f"💰 PORTFOLIO STATISTICS")
    print(f"{'='*120}")

    total_capital = initial_capital
    cash = initial_capital * (1 - target_exposure)
    invested_capital = initial_capital * target_exposure

    print(f"\n📊 Capital Allocation:")
    print(f"  Total Capital:    ¥{total_capital:15,.2f}")
    print(f"  Target Exposure:  {target_exposure:6.1%}")
    print(f"  Cash Reserve:     ¥{cash:15,.2f} ({(1-target_exposure):6.1%})")
    print(f"  To Invest:        ¥{invested_capital:15,.2f} ({target_exposure:6.1%})")

    # Summary
    if buy_signals:
        print(f"\n🟢 BUY SIGNALS ({len(buy_signals)}):")
        print(f"{'─'*120}")
        print(f"  {'Code':12s} | {'Weight':>8s} | {'Amount':>15s} | {'Price':>10s} | {'Quantity':>10s} | {'Change':>8s}")
        print(f"{'─'*120}")

        for sig in buy_signals:
            close_val = sig.get('close')
            pct_val = sig.get('pct_chg')

            if close_val is not None and close_val > 0:
                allocation = invested_capital * sig['target_weight']
                quantity = int(allocation / close_val / 100) * 100  # Round to lot size (100 shares)
                actual_amount = quantity * close_val

                close_str = f"¥{close_val:8.2f}"
                pct_str = f"{pct_val:+6.2f}%" if pct_val is not None else "N/A"

                print(f"  {sig['ts_code']:12s} | {sig['target_weight']:7.2%} | ¥{actual_amount:14,.2f} | {close_str:>10s} | {quantity:10,d} | {pct_str:>8s}")
            else:
                print(f"  {sig['ts_code']:12s} | {sig['target_weight']:7.2%} | N/A")

        # Portfolio summary
        total_invested = sum(
            int(invested_capital * s['target_weight'] / s['close'] / 100) * 100 * s['close']
            for s in buy_signals if s.get('close') and s['close'] > 0
        )
        remaining_cash = total_capital - total_invested

        print(f"{'─'*120}")
        print(f"\n📈 Portfolio Summary:")
        print(f"  Positions:        {len(buy_signals)}")
        print(f"  Total Invested:   ¥{total_invested:15,.2f} ({total_invested/total_capital:6.1%})")
        print(f"  Remaining Cash:   ¥{remaining_cash:15,.2f} ({remaining_cash/total_capital:6.1%})")
        print(f"  Total Value:      ¥{total_capital:15,.2f}")

    else:
        print(f"\n⚪ NO BUY SIGNALS - HOLDING CASH")
        print(f"  Cash Position:    ¥{total_capital:15,.2f} (100.0%)")
        if len(scored_symbols) < top_n:
            print(f"  Reason: Only {len(scored_symbols)} symbols available (need {top_n})")
        elif len(scored_symbols) >= top_n:
            top_score = scored_symbols[0]['score']
            nth_score = scored_symbols[top_n - 1]['score']
            score_spread = top_score - nth_score
            print(f"  Reason: Score spread {score_spread:.4f} < threshold {min_score_spread:.4f}")

    print(f"{'='*120}\n")

    return {
        'status': 'ok',
        'total_symbols': len(scored_symbols),
        'buy_signals': len(buy_signals),
        'sell_signals': len(sell_signals),
        'signals': signals,
        'portfolio': {
            'total_capital': total_capital,
            'cash': cash if not buy_signals else remaining_cash if buy_signals else total_capital,
            'invested': total_invested if buy_signals else 0,
            'positions': len(buy_signals),
        }
    }


def get_history_cache_latest_trade_date(history_cache: dict[str, pd.DataFrame]) -> str | None:
    latest_trade_date = None
    for frame in history_cache.values():
        if frame is None or frame.empty or 'trade_date' not in frame.columns:
            continue
        candidate = frame['trade_date'].astype(str).str.zfill(8).max()
        if candidate and (latest_trade_date is None or candidate > latest_trade_date):
            latest_trade_date = candidate
    return latest_trade_date


def is_china_market_session_open(current_time: datetime) -> bool:
    if current_time.weekday() >= 5:
        return False
    current_hhmm = current_time.hour * 100 + current_time.minute
    return (930 <= current_hhmm < 1130) or (1300 <= current_hhmm < 1500)


def run_live_bridge(
    config: dict,
    quote_client=None,
    once: bool = False,
    run_outside_market_hours: bool = False,
) -> int:
    injected_quote_client = quote_client is not None
    quote_client = quote_client or live_market_cache.create_realtime_client(config)
    simulated_client = live_market_cache.create_simulated_client(config)
    timezone = ZoneInfo(str(config.get('timezone') or 'Asia/Shanghai'))
    current_session = datetime.now().strftime('%Y%m%d')
    history_cache = build_history_cache(config, session_date=current_session)
    bootstrap_written = 0
    if config.get('live-feature-bootstrap-history', True):
        print(f"\n🔧 Bootstrapping historical features...")
        bootstrap_written = bootstrap_live_feature_files(config, history_cache)
        print(f"✅ Bootstrapped {bootstrap_written} feature files\n")

    iteration = 0
    while True:
        iteration += 1
        now = datetime.now(timezone)
        session_date = now.strftime('%Y%m%d')
        if session_date != current_session:
            print(f"\n📆 New trading session detected: {session_date}")
            current_session = session_date
            history_cache = build_history_cache(config, session_date=current_session)
            bootstrap_written = 0
            if config.get('live-feature-bootstrap-history', True):
                print(f"🔧 Bootstrapping historical features for new session...")
                bootstrap_written = bootstrap_live_feature_files(config, history_cache)
                print(f"✅ Bootstrapped {bootstrap_written} feature files\n")

        try:
            if injected_quote_client:
                market_context = {
                    'requested_mode': 'override',
                    'selected_mode': 'tushare-realtime',
                    'session_state': 'explicit-override',
                    'market_open': True,
                    'reason': 'run_live_bridge received explicit quote_client override',
                    'now': now,
                    'evaluated_at': now.isoformat(),
                }
            else:
                market_context = live_market_cache.resolve_market_data_context(
                    config['tushare-data-path'],
                    requested_mode=config.get('live-price-source-mode', 'auto'),
                    market_symbol=str(config.get('market-symbol') or '000300.SH'),
                    now=now,
                    timezone=str(config.get('timezone') or 'Asia/Shanghai'),
                )
            if run_outside_market_hours or once or market_context.get('market_open'):
                report = refresh_live_feature_snapshots(
                    config,
                    quote_client=quote_client,
                    history_cache=history_cache,
                    market_context=market_context,
                    realtime_client=quote_client,
                    simulated_client=simulated_client,
                    current_time=now,
                )
            else:
                market_status = "⏸️  Market closed - pausing updates"
                print(f"\n{market_status} (iteration {iteration})")
                report = {
                    'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'session_date': session_date,
                    'status': 'paused_outside_market_hours',
                    'feature_data_path': config['feature-data-path'],
                }
        except Exception as exc:
            print(f"\n❌ Error during iteration {iteration}: {exc}", file=sys.stderr)
            report = {
                'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
                'session_date': session_date,
                'status': 'error',
                'error': str(exc),
                'feature_data_path': config['feature-data-path'],
                'bootstrap_history_written_count': bootstrap_written,
                'history_cache_latest_trade_date': get_history_cache_latest_trade_date(history_cache),
            }
            write_report(config, report)
            if once:
                return 1
        else:
            report['bootstrap_history_written_count'] = bootstrap_written
            report['history_cache_latest_trade_date'] = get_history_cache_latest_trade_date(history_cache)
            write_report(config, report)

        if once:
            return 0

        poll_interval = max(1, int(config['live-feature-poll-interval-seconds']))
        if not once:
            print(f"⏳ Sleeping {poll_interval}s until next poll (iteration {iteration})...")
        time.sleep(poll_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Poll Tushare realtime ETF/stock daily quotes and materialize live feature CSV files for LEAN live-paper.')
    parser.add_argument('--config', default='Launcher/config/config-ashare-etf-t0-feature-live-paper.json')
    parser.add_argument('--token')
    parser.add_argument('--feature-data-path')
    parser.add_argument('--report-file')
    parser.add_argument('--poll-interval-seconds', type=int)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--include-money-market-etfs', action='store_true')
    parser.add_argument('--run-outside-market-hours', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'feature-data-path': args.feature_data_path,
        'live-feature-report-file': args.report_file,
        'live-feature-poll-interval-seconds': args.poll_interval_seconds,
    }
    if args.include_money_market_etfs:
        overrides['exclude-money-market-etfs'] = False

    config = load_live_bridge_config(args.config, overrides)
    if args.token:
        os.environ[str(config.get('tushare-token-env-var') or 'TUSHARE_TOKEN')] = args.token

    return run_live_bridge(
        config,
        once=args.once,
        run_outside_market_hours=args.run_outside_market_hours,
    )


if __name__ == '__main__':
    raise SystemExit(main())
