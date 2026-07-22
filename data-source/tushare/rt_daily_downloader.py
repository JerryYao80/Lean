#!/usr/bin/env python3
"""
Tushare rt_etf_k and rt_k daily downloader.

This module fetches realtime daily bars in batches instead of one-symbol-per-request.
The `rt_k` and `rt_etf_k` endpoints support comma-separated symbol lists, so batching is
required to stay under the per-minute request cap for CSI300-sized universes.
"""
import math
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import config


class TushareRtDailyClient:
    """Client for fetching realtime daily data from Tushare rt_etf_k and rt_k APIs."""

    def __init__(
        self,
        token: str | None = None,
        batch_size: int = 100,
        http_url: str | None = None,
        verbose: bool = True,
        max_workers: int | None = None,
        max_requests_per_minute: int | None = None,
    ):
        self._token = (token or config.TUSHARE_TOKEN).strip()
        self._batch_size = max(1, int(batch_size))
        self._http_url = (http_url or getattr(config, "TUSHARE_API_URL", "") or "").strip()
        self._verbose = bool(verbose)
        self._max_workers = max(1, int(max_workers or min(8, getattr(config, "MAX_WORKERS", 8))))
        self._max_requests_per_minute = max(
            1,
            int(max_requests_per_minute or getattr(config, "RT_DAILY_MAX_REQUESTS_PER_MINUTE", 50)),
        )
        self._thread_local = threading.local()
        self.last_fetch_metadata: dict[str, object] = {}

    def _create_api(self):
        import tushare as ts  # type: ignore

        api = ts.pro_api(
            self._token,
            timeout=getattr(config, "TUSHARE_TIMEOUT_SECONDS", 15),
        )
        api._DataApi__token = self._token
        if self._http_url:
            api._DataApi__http_url = self._http_url
        api._DataApi__timeout = getattr(config, "TUSHARE_TIMEOUT_SECONDS", 15)
        return api

    def _get_api(self):
        api = getattr(self._thread_local, "api", None)
        if api is None:
            api = self._create_api()
            self._thread_local.api = api
        return api

    @staticmethod
    def is_etf_code(ts_code: str) -> bool:
        """Classify whether the symbol should use rt_etf_k instead of rt_k."""
        text = str(ts_code).strip().upper()
        if not text or "." not in text:
            return False

        code, exchange = text.split(".", 1)
        if exchange == "SH":
            return code.startswith("5")
        if exchange == "SZ":
            return code.startswith(("15", "16", "18"))
        return False

    def _call_rt_etf_k(self, api, ts_codes: Sequence[str]):
        """Call rt_etf_k API for ETF daily data."""
        return api.rt_etf_k(ts_code=",".join(ts_codes))

    def _call_rt_k(self, api, ts_codes: Sequence[str]):
        """Call rt_k API for stock daily data."""
        return api.rt_k(ts_code=",".join(ts_codes))

    def _normalize_frame(self, data: pd.DataFrame | None) -> pd.DataFrame | None:
        if data is None or data.empty:
            return None
        normalized = data.copy()
        normalized.columns = [str(col).strip().lower() for col in normalized.columns]
        if "ts_code" in normalized.columns:
            normalized["ts_code"] = normalized["ts_code"].astype(str).str.strip().str.upper()
        return normalized

    def _fetch_quote_batch(self, api, api_name: str, ts_codes: Sequence[str]) -> tuple[pd.DataFrame | None, str | None]:
        """Fetch quotes for a batch of symbols from a single realtime endpoint."""
        try:
            if api_name == "rt_etf_k":
                data = self._call_rt_etf_k(api, ts_codes)
            else:
                data = self._call_rt_k(api, ts_codes)
            if data is not None and not data.empty:
                return self._normalize_frame(data), None
        except Exception as e:
            return None, str(e)
        return None, None

    @staticmethod
    def _emit_progress(progress_callback: Callable[[dict], None] | None, **payload) -> None:
        if progress_callback is None:
            return
        progress_callback(payload)

    def _chunk_symbols(self, symbols: Sequence[str]) -> list[list[str]]:
        return [
            list(symbols[index:index + self._batch_size])
            for index in range(0, len(symbols), self._batch_size)
        ]

    def _build_request_specs(self, symbols: Sequence[str]) -> list[dict]:
        stock_symbols = [symbol for symbol in symbols if not self.is_etf_code(symbol)]
        etf_symbols = [symbol for symbol in symbols if self.is_etf_code(symbol)]
        specs: list[dict] = []
        for api_name, api_symbols in [("rt_k", stock_symbols), ("rt_etf_k", etf_symbols)]:
            for chunk in self._chunk_symbols(api_symbols):
                specs.append({
                    "api_name": api_name,
                    "symbols": chunk,
                })
        return specs

    def _fetch_quote_batch_task(self, request_index: int, symbols: Sequence[str], api_name: str, trade_date: str | None) -> dict:
        try:
            api = self._get_api()
            data, error = self._fetch_quote_batch(api, api_name, symbols)
        except Exception as exc:
            data, error = None, str(exc)
        return {
            "request_index": request_index,
            "symbols": list(symbols),
            "trade_date": trade_date,
            "api_name": api_name,
            "data": data,
            "error": error,
        }

    def fetch_quotes(
        self,
        ts_codes: Sequence[str],
        trade_date: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> pd.DataFrame:
        """
        Fetch realtime daily quotes for multiple symbols.

        Args:
            ts_codes: List of Tushare codes (e.g., ['510300.SH', '000001.SZ'])
            trade_date: Optional trade date filter (not used for realtime APIs)

        Returns:
            DataFrame with columns: ts_code, trade_date, open, high, low, close,
                                   pre_close, pct_chg, vol, amount, etc.
        """
        frames: list[pd.DataFrame] = []
        symbols = [str(ts_code).strip() for ts_code in ts_codes if str(ts_code).strip()]

        if not symbols:
            self.last_fetch_metadata = {
                "source_mode": "tushare-realtime",
                "quote_count": 0,
                "generated_at": None,
            }
            self._emit_progress(
                progress_callback,
                event="finish",
                trade_date=trade_date,
                total=0,
                received=0,
                batch_size=self._batch_size,
                batch_count=0,
                minute_window_size=0,
                minute_window_count=0,
                max_requests_per_minute=self._max_requests_per_minute,
            )
            return pd.DataFrame()

        request_specs = self._build_request_specs(symbols)
        request_count = len(request_specs)
        batch_count = request_count
        minute_window_size = min(request_count, self._max_requests_per_minute)
        minute_window_count = ceil(request_count / minute_window_size) if minute_window_size > 0 else 0
        self._emit_progress(
            progress_callback,
            event="start",
            trade_date=trade_date,
            total=len(symbols),
            received=0,
            batch_size=self._batch_size,
            batch_count=batch_count,
            max_workers=min(self._max_workers, minute_window_size),
            minute_window_size=minute_window_size,
            minute_window_count=minute_window_count,
            max_requests_per_minute=self._max_requests_per_minute,
            estimated_round_minutes=minute_window_count,
            request_count=request_count,
        )
        self.last_fetch_metadata = {
            "source_mode": "tushare-realtime",
            "quote_count": 0,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "request_count": request_count,
            "batch_count": batch_count,
            "minute_window_count": minute_window_count,
        }
        if self._verbose:
            print(
                f"\n[rt daily] fetching realtime daily bars for {len(symbols)} symbols "
                f"using {request_count} batched requests with {self._max_requests_per_minute} requests/minute",
                flush=True,
            )

        completed = 0
        worker_count = min(self._max_workers, minute_window_size)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for minute_window_index, window_start in enumerate(range(0, request_count, minute_window_size), start=1):
                window_specs = request_specs[window_start: window_start + minute_window_size]
                request_start = window_start + 1
                request_end = window_start + len(window_specs)
                self._emit_progress(
                    progress_callback,
                    event="minute_window_start",
                    trade_date=trade_date,
                    minute_window_index=minute_window_index,
                    minute_window_count=minute_window_count,
                    minute_window_size=len(window_specs),
                    request_start=request_start,
                    request_end=request_end,
                    total=len(symbols),
                )
                window_started_at = time.monotonic()
                futures = {
                    executor.submit(
                        self._fetch_quote_batch_task,
                        request_start + offset,
                        spec["symbols"],
                        spec["api_name"],
                        trade_date,
                    ): spec
                    for offset, spec in enumerate(window_specs)
                }
                for future in as_completed(futures):
                    result = future.result()
                    data = result["data"]
                    error = result["error"]
                    request_index = int(result["request_index"])
                    request_symbols = [str(symbol).strip().upper() for symbol in result["symbols"]]
                    api_name = str(result["api_name"])
                    batch_index = request_index

                    data_lookup: dict[str, dict] = {}
                    if data is not None and not data.empty:
                        frames.append(data)
                        if "ts_code" in data.columns:
                            for row in data.to_dict(orient="records"):
                                ts_code = str(row.get("ts_code") or "").strip().upper()
                                if ts_code:
                                    data_lookup[ts_code] = row

                    for ts_code in request_symbols:
                        completed += 1
                        row = data_lookup.get(ts_code)
                        if row is not None:
                            close = row.get("close")
                            pct_chg = row.get("pct_chg")
                            self._emit_progress(
                                progress_callback,
                                event="quote",
                                trade_date=trade_date,
                                index=completed,
                                request_index=request_index,
                                total=len(symbols),
                                batch_index=batch_index,
                                batch_count=batch_count,
                                minute_window_index=minute_window_index,
                                minute_window_count=minute_window_count,
                                ts_code=ts_code,
                                api_name=api_name,
                                status="ok",
                                close=close,
                                pct_chg=pct_chg,
                            )
                            if self._verbose:
                                close_text = "-" if close is None else f"{float(close):.2f}"
                                pct_text = "-" if pct_chg is None else f"{float(pct_chg):+.2f}%"
                                print(
                                    f"  [{completed}/{len(symbols)}] minute={minute_window_index}/{minute_window_count} "
                                    f"req={request_index} batch={batch_index}/{batch_count} {ts_code} "
                                    f"api={api_name} close={close_text} pct_chg={pct_text}",
                                    flush=True,
                                )
                        else:
                            status = "error" if error else "missing"
                            self._emit_progress(
                                progress_callback,
                                event="quote",
                                trade_date=trade_date,
                                index=completed,
                                request_index=request_index,
                                total=len(symbols),
                                batch_index=batch_index,
                                batch_count=batch_count,
                                minute_window_index=minute_window_index,
                                minute_window_count=minute_window_count,
                                ts_code=ts_code,
                                api_name=api_name,
                                status=status,
                                error=error,
                            )
                            if self._verbose:
                                message = error or "no data"
                                print(
                                    f"  [{completed}/{len(symbols)}] minute={minute_window_index}/{minute_window_count} "
                                    f"req={request_index} batch={batch_index}/{batch_count} {ts_code} "
                                    f"api={api_name} status={status} detail={message}",
                                    flush=True,
                                )

                if minute_window_index < minute_window_count:
                    elapsed_seconds = time.monotonic() - window_started_at
                    wait_seconds = max(0.0, 60.0 - elapsed_seconds)
                    self._emit_progress(
                        progress_callback,
                        event="minute_window_wait",
                        trade_date=trade_date,
                        minute_window_index=minute_window_index,
                        minute_window_count=minute_window_count,
                        next_minute_window_index=minute_window_index + 1,
                        wait_seconds=wait_seconds,
                        total=len(symbols),
                    )
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)

        if not frames:
            self.last_fetch_metadata = {
                "source_mode": "tushare-realtime",
                "quote_count": 0,
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "request_count": request_count,
                "batch_count": batch_count,
                "minute_window_count": minute_window_count,
            }
            self._emit_progress(
                progress_callback,
                event="finish",
                trade_date=trade_date,
                total=len(symbols),
                received=0,
                batch_size=self._batch_size,
                batch_count=batch_count,
                minute_window_size=minute_window_size,
                minute_window_count=minute_window_count,
                max_requests_per_minute=self._max_requests_per_minute,
            )
            if self._verbose:
                print("\n[rt daily] no realtime bars returned")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        self._emit_progress(
            progress_callback,
            event="finish",
            trade_date=trade_date,
            total=len(symbols),
            received=len(combined.index),
            batch_size=self._batch_size,
            batch_count=batch_count,
            minute_window_size=minute_window_size,
            minute_window_count=minute_window_count,
            max_requests_per_minute=self._max_requests_per_minute,
        )
        if self._verbose:
            print(f"\n[rt daily] completed with {len(combined)} records\n")
        normalized_combined = self._normalize_frame(combined)
        if normalized_combined is None:
            return pd.DataFrame()
        self.last_fetch_metadata = {
            "source_mode": "tushare-realtime",
            "quote_count": int(len(normalized_combined.index)),
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "request_count": request_count,
            "batch_count": batch_count,
            "minute_window_count": minute_window_count,
        }

        return normalized_combined


@dataclass
class SyntheticQuoteCalibration:
    ts_code: str
    pre_close: float
    drift: float
    volatility: float
    avg_daily_volume: float
    volume_log_sigma: float
    amount_price_ratio: float


@dataclass
class SyntheticQuoteState:
    trade_date: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    cumulative_volume: float
    cumulative_amount: float
    steps: int = 0


class GbmSyntheticRtDailyClient:
    """Generate off-hours realtime-like daily bars from historical daily bars via GBM."""

    def __init__(
        self,
        tushare_data_path: str | Path,
        batch_size: int = 100,
        poll_interval_seconds: int = 60,
        lookback_days: int = 60,
        min_history_days: int = 20,
        trading_minutes_per_day: int = 240,
        random_seed: int = 42,
        volatility_scale: float = 8.0,
        min_daily_volatility: float = 0.80,
        jump_probability: float = 0.22,
        jump_scale: float = 0.10,
        timezone: str = "Asia/Shanghai",
        verbose: bool = False,
    ):
        self._data_path = Path(tushare_data_path).resolve()
        self._batch_size = max(1, int(batch_size))
        self._poll_interval_seconds = max(1, int(poll_interval_seconds))
        self._lookback_days = max(10, int(lookback_days))
        self._min_history_days = max(5, int(min_history_days))
        self._trading_minutes_per_day = max(1, int(trading_minutes_per_day))
        self._volatility_scale = max(1.0, float(volatility_scale))
        self._min_daily_volatility = max(0.05, float(min_daily_volatility))
        self._jump_probability = max(0.0, min(1.0, float(jump_probability)))
        self._jump_scale = max(0.0, float(jump_scale))
        self._timezone = ZoneInfo(str(timezone or "Asia/Shanghai"))
        self._verbose = bool(verbose)
        self._random = random.Random(int(random_seed))
        self._calibration_cache: dict[tuple[str, str], SyntheticQuoteCalibration] = {}
        self._state_by_symbol: dict[str, SyntheticQuoteState] = {}
        self.last_fetch_metadata: dict[str, object] = {}

    @staticmethod
    def is_etf_code(ts_code: str) -> bool:
        return TushareRtDailyClient.is_etf_code(ts_code)

    @staticmethod
    def _emit_progress(progress_callback: Callable[[dict], None] | None, **payload) -> None:
        if progress_callback is None:
            return
        progress_callback(payload)

    def _resolve_daily_path(self, ts_code: str) -> Path | None:
        fund_daily = self._data_path / "fund_daily" / f"ts_code={ts_code}" / "data.parquet"
        if fund_daily.exists():
            return fund_daily
        daily = self._data_path / "daily" / f"ts_code={ts_code}" / "data.parquet"
        if daily.exists():
            return daily
        return None

    def _load_history(self, ts_code: str, trade_date: str) -> pd.DataFrame:
        path = self._resolve_daily_path(ts_code)
        columns = ["trade_date", "open", "high", "low", "close", "vol", "amount"]
        if path is None:
            return pd.DataFrame(columns=columns)
        try:
            frame = pd.read_parquet(path, columns=columns)
        except Exception:
            try:
                frame = pd.read_parquet(path)
            except Exception:
                return pd.DataFrame(columns=columns)
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column in columns:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame = frame.loc[:, columns].copy()
        frame["trade_date"] = frame["trade_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
        frame = frame[frame["trade_date"] != ""].copy()
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column in ["open", "high", "low", "close", "vol", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.sort_values("trade_date")
        eligible = frame[frame["trade_date"] <= str(trade_date)].copy()
        if eligible.empty:
            eligible = frame.copy()
        return eligible.tail(self._lookback_days + 1).reset_index(drop=True)

    def _build_calibration(self, ts_code: str, trade_date: str) -> SyntheticQuoteCalibration:
        cache_key = (ts_code, trade_date)
        cached = self._calibration_cache.get(cache_key)
        if cached is not None:
            return cached

        history = self._load_history(ts_code, trade_date)
        price_floor = 0.01
        if history.empty:
            calibration = SyntheticQuoteCalibration(
                ts_code=ts_code,
                pre_close=10.0,
                drift=0.0,
                volatility=0.02,
                avg_daily_volume=1_000_000.0,
                volume_log_sigma=0.35,
                amount_price_ratio=100.0,
            )
            self._calibration_cache[cache_key] = calibration
            return calibration

        close_series = history["close"].ffill().bfill().clip(lower=price_floor)
        latest_close = float(close_series.iloc[-1]) if not close_series.empty else 10.0
        previous_close = latest_close
        if len(close_series.index) >= 2:
            previous_close = float(close_series.iloc[-2] or latest_close)

        log_returns: list[float] = []
        closes = close_series.tolist()
        for previous, current in zip(closes[:-1], closes[1:]):
            previous_price = max(price_floor, float(previous))
            current_price = max(price_floor, float(current))
            log_returns.append(math.log(current_price / previous_price))

        usable_returns = log_returns[-self._lookback_days:]
        if len(usable_returns) >= self._min_history_days:
            drift = sum(usable_returns) / len(usable_returns)
            variance = sum((value - drift) ** 2 for value in usable_returns) / len(usable_returns)
            volatility = math.sqrt(max(variance, 1e-8))
        elif usable_returns:
            drift = sum(usable_returns) / len(usable_returns)
            volatility = 0.02
        else:
            drift = 0.0
            volatility = 0.02

        volume_series = pd.to_numeric(history["vol"], errors="coerce").dropna()
        positive_volume = [float(value) for value in volume_series.tolist() if float(value) > 0.0]
        avg_daily_volume = sum(positive_volume[-self._lookback_days:]) / len(positive_volume[-self._lookback_days:]) if positive_volume else 1_000_000.0
        log_volumes = [math.log(value) for value in positive_volume[-self._lookback_days:]]
        if len(log_volumes) >= 2:
            log_volume_mean = sum(log_volumes) / len(log_volumes)
            volume_variance = sum((value - log_volume_mean) ** 2 for value in log_volumes) / len(log_volumes)
            volume_log_sigma = math.sqrt(max(volume_variance, 1e-8))
        else:
            volume_log_sigma = 0.35

        amount_ratios: list[float] = []
        for _, row in history.tail(self._lookback_days).iterrows():
            close_price = float(row.get("close") or 0.0)
            volume_value = float(row.get("vol") or 0.0)
            amount_value = float(row.get("amount") or 0.0)
            if close_price > 0.0 and volume_value > 0.0 and amount_value > 0.0:
                amount_ratios.append(amount_value / (volume_value * close_price))
        amount_price_ratio = sum(amount_ratios) / len(amount_ratios) if amount_ratios else 100.0

        calibration = SyntheticQuoteCalibration(
            ts_code=ts_code,
            pre_close=max(price_floor, previous_close),
            drift=max(-0.10, min(0.10, drift)),
            volatility=max(0.005, min(0.12, volatility)),
            avg_daily_volume=max(100.0, avg_daily_volume),
            volume_log_sigma=max(0.05, min(1.20, volume_log_sigma)),
            amount_price_ratio=max(0.01, amount_price_ratio),
        )
        self._calibration_cache[cache_key] = calibration
        return calibration

    def _get_or_create_state(self, ts_code: str, trade_date: str, calibration: SyntheticQuoteCalibration) -> SyntheticQuoteState:
        state = self._state_by_symbol.get(ts_code)
        if state is not None and state.trade_date == trade_date:
            return state

        opening_price = max(0.01, calibration.pre_close)
        state = SyntheticQuoteState(
            trade_date=trade_date,
            open_price=opening_price,
            high_price=opening_price,
            low_price=opening_price,
            close_price=opening_price,
            cumulative_volume=0.0,
            cumulative_amount=0.0,
            steps=0,
        )
        self._state_by_symbol[ts_code] = state
        return state

    def _simulate_row(self, ts_code: str, trade_date: str, timestamp: datetime) -> dict[str, object]:
        calibration = self._build_calibration(ts_code, trade_date)
        state = self._get_or_create_state(ts_code, trade_date, calibration)
        step_fraction = self._poll_interval_seconds / 60.0 / float(self._trading_minutes_per_day)
        scaled_daily_volatility = min(1.50, max(self._min_daily_volatility, calibration.volatility * self._volatility_scale))
        scaled_drift = max(-0.25, min(0.25, calibration.drift * max(1.0, self._volatility_scale * 0.5)))
        shock = self._random.gauss(0.0, 1.0)
        jump_return = 0.0
        if self._jump_probability > 0.0 and self._random.random() < self._jump_probability:
            jump_return = self._random.gauss(0.0, self._jump_scale)
        exponent = (
            (scaled_drift - 0.5 * scaled_daily_volatility * scaled_daily_volatility) * step_fraction
            + scaled_daily_volatility * math.sqrt(step_fraction) * shock
            + jump_return
        )
        next_close = max(0.01, state.close_price * math.exp(exponent))
        intraday_span = (
            abs(self._random.gauss(0.0, 1.0)) + abs(shock)
        ) * scaled_daily_volatility * math.sqrt(step_fraction)
        state.close_price = next_close
        state.high_price = max(state.high_price, state.open_price, next_close * (1.0 + intraday_span * 0.35))
        state.low_price = min(state.low_price, state.open_price, next_close * max(0.01, 1.0 - intraday_span * 0.35))

        mean_increment_volume = max(1.0, calibration.avg_daily_volume * step_fraction)
        volume_log_mean = math.log(mean_increment_volume) - 0.5 * calibration.volume_log_sigma * calibration.volume_log_sigma
        increment_volume = math.exp(volume_log_mean + calibration.volume_log_sigma * self._random.gauss(0.0, 1.0))
        if jump_return != 0.0:
            increment_volume *= 1.0 + min(3.0, abs(jump_return) * 8.0)
        state.cumulative_volume += max(0.0, increment_volume)

        turnover_multiplier = max(0.50, min(1.50, 1.0 + self._random.gauss(0.0, 0.05)))
        increment_amount = increment_volume * next_close * calibration.amount_price_ratio * turnover_multiplier
        state.cumulative_amount += max(0.0, increment_amount)
        state.steps += 1

        source_api = "sim_rt_etf_k" if self.is_etf_code(ts_code) else "sim_rt_k"
        pct_chg = ((next_close / calibration.pre_close) - 1.0) * 100.0 if calibration.pre_close > 0 else 0.0
        return {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "open": state.open_price,
            "high": state.high_price,
            "low": state.low_price,
            "close": next_close,
            "price": next_close,
            "pre_close": calibration.pre_close,
            "pct_chg": pct_chg,
            "vol": state.cumulative_volume,
            "amount": state.cumulative_amount,
            "fetch_timestamp": timestamp.astimezone(self._timezone).isoformat(),
            "source_api": source_api,
        }

    def fetch_quotes(
        self,
        ts_codes: Sequence[str],
        trade_date: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> pd.DataFrame:
        symbols = [str(ts_code).strip().upper() for ts_code in ts_codes if str(ts_code).strip()]
        timestamp = datetime.now(self._timezone)
        batch_count = ceil(len(symbols) / self._batch_size) if symbols else 0
        self.last_fetch_metadata = {
            "source_mode": "gbm-simulated",
            "quote_count": 0,
            "generated_at": timestamp.isoformat(),
            "batch_count": batch_count,
            "minute_window_count": 1 if symbols else 0,
            "volatility_scale": self._volatility_scale,
            "min_daily_volatility": self._min_daily_volatility,
            "jump_probability": self._jump_probability,
            "jump_scale": self._jump_scale,
        }

        if not symbols:
            self._emit_progress(
                progress_callback,
                event="finish",
                trade_date=trade_date,
                total=0,
                received=0,
                batch_size=self._batch_size,
                batch_count=0,
                minute_window_size=0,
                minute_window_count=0,
                max_requests_per_minute=0,
            )
            return pd.DataFrame()

        self._emit_progress(
            progress_callback,
            event="start",
            trade_date=trade_date,
            total=len(symbols),
            received=0,
            batch_size=self._batch_size,
            batch_count=batch_count,
            max_workers=1,
            minute_window_size=batch_count,
            minute_window_count=1,
            max_requests_per_minute=len(symbols),
            estimated_round_minutes=1,
            request_count=batch_count,
        )
        self._emit_progress(
            progress_callback,
            event="minute_window_start",
            trade_date=trade_date,
            minute_window_index=1,
            minute_window_count=1,
            minute_window_size=batch_count,
            request_start=1,
            request_end=batch_count,
            total=len(symbols),
        )

        rows: list[dict[str, object]] = []
        for index, ts_code in enumerate(symbols, start=1):
            row = self._simulate_row(ts_code, str(trade_date or ""), timestamp)
            rows.append(row)
            self._emit_progress(
                progress_callback,
                event="quote",
                trade_date=trade_date,
                index=index,
                request_index=((index - 1) // self._batch_size) + 1,
                total=len(symbols),
                batch_index=((index - 1) // self._batch_size) + 1,
                batch_count=batch_count,
                minute_window_index=1,
                minute_window_count=1,
                ts_code=ts_code,
                api_name=row["source_api"],
                status="ok",
                close=row["close"],
                pct_chg=row["pct_chg"],
            )

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame = frame.sort_values("ts_code").reset_index(drop=True)
        self._emit_progress(
            progress_callback,
            event="finish",
            trade_date=trade_date,
            total=len(symbols),
            received=len(frame.index),
            batch_size=self._batch_size,
            batch_count=batch_count,
            minute_window_size=batch_count,
            minute_window_count=1,
            max_requests_per_minute=len(symbols),
        )
        self.last_fetch_metadata = {
            "source_mode": "gbm-simulated",
            "quote_count": int(len(frame.index)),
            "generated_at": timestamp.isoformat(),
            "batch_count": batch_count,
            "minute_window_count": 1,
            "volatility_scale": self._volatility_scale,
            "min_daily_volatility": self._min_daily_volatility,
            "jump_probability": self._jump_probability,
            "jump_scale": self._jump_scale,
        }
        return frame
