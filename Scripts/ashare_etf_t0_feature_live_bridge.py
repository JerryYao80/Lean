#!/usr/bin/env python3

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_etf_t0_feature_backtest import build_etf_metadata_lookup
from export_ashare_etf_t0_feature_data import FEATURE_COLUMNS, build_feature_frame, feature_daily_path
from tushare_data_layer import TushareDataLayer
from tushare_lean_export import load_registry_universe


PATH_KEYS = {
    'registry-file',
    'tushare-data-path',
    'dataset-catalog',
    'feature-data-path',
    'live-feature-report-file',
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
        'start-date': '20240101',
        'end-date': '20261231',
        'exclude-money-market-etfs': True,
        'tushare-token': '',
        'tushare-http-url': '',
        'tushare-token-env-var': 'TUSHARE_TOKEN',
        'live-feature-poll-interval-seconds': 30,
        'live-feature-minute-frequency': '1MIN',
        'live-feature-quote-batch-size': 25,
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


def _coerce_minute_frequency(value, default: str = '1MIN') -> str:
    if value is None:
        return default
    text = str(value).strip().upper()
    if not text:
        return default
    if text.endswith('MIN') and text[:-3].isdigit():
        return text
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
    config['live-feature-minute-frequency'] = _coerce_minute_frequency(config.get('live-feature-minute-frequency'), '1MIN')
    config['live-feature-quote-batch-size'] = _coerce_int(config.get('live-feature-quote-batch-size'), 25)
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


class TushareRealtimeMinuteClient:
    MAX_RT_MIN_ROWS_PER_REQUEST = 1000
    TRADING_SESSION_MINUTES = 240

    def __init__(self, token: str, batch_size: int = 25, http_url: str | None = None, frequency: str = '1MIN'):
        self._token = token
        self._batch_size = max(1, int(batch_size))
        self._http_url = (http_url or '').strip()
        self._frequency = _coerce_minute_frequency(frequency, '1MIN')
        self._api = None
        self._supports_batch_queries = True

    def _get_api(self):
        if self._api is not None:
            return self._api

        try:
            import tushare as ts  # type: ignore
        except ImportError as exc:
            raise RuntimeError('Python package `tushare` is required for live minute polling.') from exc

        self._api = ts.pro_api(self._token)
        self._api._DataApi__token = self._token
        if self._http_url:
            self._api._DataApi__http_url = self._http_url
        return self._api

    @staticmethod
    def _frequency_minutes(frequency: str) -> int:
        text = _coerce_minute_frequency(frequency, '1MIN')
        return max(1, int(text[:-3]))

    def _effective_symbols_per_request(self) -> int:
        minutes = self._frequency_minutes(self._frequency)
        bars_per_symbol = max(1, math.ceil(self.TRADING_SESSION_MINUTES / minutes))
        safe_limit = max(1, self.MAX_RT_MIN_ROWS_PER_REQUEST // bars_per_symbol)
        return max(1, min(self._batch_size, safe_limit))

    @staticmethod
    def _normalize_trade_time_text(value) -> str:
        text = str(value or '').strip()
        if not text:
            return text
        if ' ' in text or '-' in text or '/' in text or ':' in text:
            return text
        if text.isdigit() and len(text) == 6:
            return f'{text[:2]}:{text[2:4]}:{text[4:6]}'
        if text.isdigit() and len(text) == 4:
            return f'{text[:2]}:{text[2:4]}:00'
        return text

    @staticmethod
    def _should_retry_symbols_individually(error: Exception) -> bool:
        message = str(error)
        keywords = (
            '不支持的市场后缀',
            '检查代码格式',
            'ts_code',
            'code format',
            'market suffix',
        )
        return any(keyword in message for keyword in keywords)

    def _call_rt_min(self, api, ts_code: str):
        return api.rt_min(ts_code=ts_code, freq=self._frequency)

    def _fetch_batch_frames(self, api, batch: list[str]) -> list[pd.DataFrame]:
        if self._supports_batch_queries:
            query = ','.join(batch)
            try:
                data = self._call_rt_min(api, query)
                if data is None or data.empty:
                    return []
                return [data]
            except TypeError:
                try:
                    data = api.rt_min(ts_code=batch, freq=self._frequency)
                    if data is None or data.empty:
                        return []
                    return [data]
                except Exception as exc:
                    if not self._should_retry_symbols_individually(exc):
                        raise
                    self._supports_batch_queries = False
            except Exception as exc:
                if not self._should_retry_symbols_individually(exc):
                    raise
                self._supports_batch_queries = False

        frames: list[pd.DataFrame] = []
        for ts_code in batch:
            data = self._call_rt_min(api, ts_code)
            if data is None or data.empty:
                continue
            frames.append(data)
        return frames

    @classmethod
    def _parse_trade_timestamps(cls, values: pd.Series) -> pd.Series:
        normalized = values.astype(str).map(cls._normalize_trade_time_text)
        parsed = pd.to_datetime(normalized, errors='coerce')
        missing = parsed.isna()
        if missing.any():
            base_date = datetime.now().strftime('%Y-%m-%d')
            parsed.loc[missing] = pd.to_datetime(base_date + ' ' + normalized.loc[missing], errors='coerce')
        return parsed

    @classmethod
    def normalize_minute_bars(cls, frame: pd.DataFrame) -> pd.DataFrame:
        data = frame.copy()
        data.columns = [str(column).strip().lower() for column in data.columns]
        rename_map = {
            'volume': 'vol',
            'datetime': 'feature_timestamp',
        }
        data = data.rename(columns={key: value for key, value in rename_map.items() if key in data.columns})

        for column in ('open', 'high', 'low', 'close', 'vol', 'amount'):
            if column in data.columns:
                data[column] = pd.to_numeric(data[column], errors='coerce')

        if 'trade_time' not in data.columns and 'time' in data.columns:
            data['trade_time'] = data['time']

        if 'feature_timestamp' not in data.columns:
            if 'trade_time' in data.columns:
                timestamps = cls._parse_trade_timestamps(data['trade_time'])
            else:
                timestamps = pd.Series(pd.Timestamp(datetime.now()), index=data.index)
            data['feature_timestamp'] = timestamps.dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            timestamps = pd.to_datetime(data['feature_timestamp'], errors='coerce')

        if 'trade_date' not in data.columns:
            data['trade_date'] = timestamps.dt.strftime('%Y%m%d')
        else:
            data['trade_date'] = data['trade_date'].astype(str).str.replace('-', '', regex=False).str.slice(0, 8)

        if 'price' not in data.columns and 'close' in data.columns:
            data['price'] = data['close']

        if 'ts_code' in data.columns:
            data['ts_code'] = data['ts_code'].astype(str)

        return data

    @classmethod
    def aggregate_minute_bars(cls, frame: pd.DataFrame) -> pd.DataFrame:
        data = cls.normalize_minute_bars(frame)
        if data.empty:
            return pd.DataFrame()

        data['parsed_feature_timestamp'] = pd.to_datetime(data['feature_timestamp'], errors='coerce')
        data = data.dropna(subset=['parsed_feature_timestamp'])
        if data.empty:
            return pd.DataFrame()

        data = data.sort_values(['ts_code', 'parsed_feature_timestamp'], kind='stable').reset_index(drop=True)
        snapshots: list[dict] = []

        for ts_code, group in data.groupby('ts_code', sort=False):
            if not str(ts_code).strip():
                continue

            open_values = group['open'].dropna() if 'open' in group.columns else pd.Series(dtype='float64')
            high_values = group['high'].dropna() if 'high' in group.columns else pd.Series(dtype='float64')
            low_values = group['low'].dropna() if 'low' in group.columns else pd.Series(dtype='float64')
            close_values = group['close'].dropna() if 'close' in group.columns else pd.Series(dtype='float64')
            vol_values = group['vol'].dropna() if 'vol' in group.columns else pd.Series(dtype='float64')
            amount_values = group['amount'].dropna() if 'amount' in group.columns else pd.Series(dtype='float64')
            last_row = group.iloc[-1]

            trade_date = str(last_row.get('trade_date') or '').strip()
            if len(trade_date) != 8 or not trade_date.isdigit():
                trade_date = pd.Timestamp(last_row['parsed_feature_timestamp']).strftime('%Y%m%d')

            snapshot = {
                'ts_code': str(ts_code).strip(),
                'trade_date': trade_date,
                'feature_timestamp': pd.Timestamp(last_row['parsed_feature_timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
                'minute_bar_count': int(len(group.index)),
            }
            if not open_values.empty:
                snapshot['open'] = float(open_values.iloc[0])
            if not high_values.empty:
                snapshot['high'] = float(high_values.max())
            if not low_values.empty:
                snapshot['low'] = float(low_values.min())
            if not close_values.empty:
                snapshot['close'] = float(close_values.iloc[-1])
                snapshot['price'] = snapshot['close']
            if not vol_values.empty:
                snapshot['vol'] = float(vol_values.sum())
            if not amount_values.empty:
                snapshot['amount'] = float(amount_values.sum())

            snapshots.append(snapshot)

        return pd.DataFrame(snapshots)

    def fetch_quotes(self, ts_codes: list[str]) -> pd.DataFrame:
        api = self._get_api()
        frames: list[pd.DataFrame] = []
        symbols_per_request = self._effective_symbols_per_request()

        for start in range(0, len(ts_codes), symbols_per_request):
            batch = ts_codes[start:start + symbols_per_request]
            frames.extend(self._fetch_batch_frames(api, batch))

        if not frames:
            return pd.DataFrame()

        return self.aggregate_minute_bars(pd.concat(frames, ignore_index=True))


TushareRealtimeQuoteClient = TushareRealtimeMinuteClient


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


def bootstrap_live_feature_files(config: dict, history_cache: dict[str, pd.DataFrame]) -> int:
    written = 0
    for ts_code, base_history in history_cache.items():
        if base_history is None or base_history.empty:
            continue
        feature_path = feature_daily_path(config['feature-data-path'], ts_code)
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        prepared = prepare_live_feature_frame(base_history)
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
    frame.loc[len(frame)] = [live_row.get(column) for column in LIVE_FEATURE_COLUMNS]
    frame['trade_date'] = frame['trade_date'].astype(str).str.zfill(8)
    frame = frame.sort_values(['trade_date', 'feature_timestamp'], kind='stable').drop_duplicates(subset=['trade_date'], keep='last').reset_index(drop=True)
    frame.to_csv(target, index=False, float_format='%.10f')
    return frame


def refresh_live_feature_snapshots(
    config: dict,
    quote_client,
    history_cache: dict[str, pd.DataFrame],
    current_time: datetime | None = None,
) -> dict:
    now = current_time or datetime.now()
    session_date = now.strftime('%Y%m%d')
    universe = sorted(history_cache.keys())
    quotes = quote_client.fetch_quotes(universe)

    if quotes.empty:
        report = {
            'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
            'session_date': session_date,
            'quote_count': 0,
            'written_count': 0,
            'skipped_symbols': universe,
            'feature_data_path': config['feature-data-path'],
        }
        return report

    if 'ts_code' not in quotes.columns:
        raise RuntimeError('Realtime minute response must include ts_code column.')

    written = 0
    skipped: list[str] = []
    updated: list[str] = []

    for _, quote_row in quotes.iterrows():
        ts_code = str(quote_row.get('ts_code') or '').strip()
        if not ts_code or ts_code not in history_cache:
            if ts_code:
                skipped.append(ts_code)
            continue

        live_row = build_live_feature_row(history_cache[ts_code], quote_row.to_dict(), session_date=session_date)
        if live_row is None:
            skipped.append(ts_code)
            continue

        feature_path = feature_daily_path(config['feature-data-path'], ts_code)
        upsert_live_feature_file(feature_path, history_cache[ts_code], live_row)
        written += 1
        updated.append(ts_code)

    report = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'session_date': session_date,
        'quote_count': int(len(quotes.index)),
        'written_count': written,
        'updated_symbols': updated,
        'skipped_symbols': skipped,
        'feature_data_path': config['feature-data-path'],
    }
    return report


def write_report(config: dict, report: dict) -> None:
    report_path = Path(config['live-feature-report-file'])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


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
    token = resolve_tushare_token(config)
    quote_client = quote_client or TushareRealtimeMinuteClient(
        token,
        batch_size=config['live-feature-quote-batch-size'],
        http_url=config.get('tushare-http-url'),
        frequency=config.get('live-feature-minute-frequency', '1MIN'),
    )
    current_session = datetime.now().strftime('%Y%m%d')
    history_cache = build_history_cache(config, session_date=current_session)
    bootstrap_written = 0
    if config.get('live-feature-bootstrap-history', True):
        bootstrap_written = bootstrap_live_feature_files(config, history_cache)

    while True:
        now = datetime.now()
        session_date = now.strftime('%Y%m%d')
        if session_date != current_session:
            current_session = session_date
            history_cache = build_history_cache(config, session_date=current_session)
            bootstrap_written = 0
            if config.get('live-feature-bootstrap-history', True):
                bootstrap_written = bootstrap_live_feature_files(config, history_cache)

        if run_outside_market_hours or once or is_china_market_session_open(now):
            report = refresh_live_feature_snapshots(config, quote_client, history_cache, current_time=now)
            report['bootstrap_history_written_count'] = bootstrap_written
            write_report(config, report)
        else:
            report = {
                'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
                'session_date': session_date,
                'status': 'paused_outside_market_hours',
                'feature_data_path': config['feature-data-path'],
                'bootstrap_history_written_count': bootstrap_written,
            }
            write_report(config, report)

        if once:
            return 0

        time.sleep(max(1, int(config['live-feature-poll-interval-seconds'])))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Poll Tushare realtime ETF quotes and materialize live feature CSV files for LEAN live-paper.')
    parser.add_argument('--config', default='Launcher/config/config-ashare-etf-t0-feature-live-paper.json')
    parser.add_argument('--token')
    parser.add_argument('--feature-data-path')
    parser.add_argument('--report-file')
    parser.add_argument('--poll-interval-seconds', type=int)
    parser.add_argument('--minute-frequency')
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
        'live-feature-minute-frequency': args.minute_frequency,
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
