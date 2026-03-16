#!/usr/bin/env python3
"""
Tushare rt_etf_k and rt_k daily downloader.

This module fetches daily bars from the realtime `rt_etf_k` (ETF) and `rt_k` (stock) endpoints
and provides aggregated quotes for the live feature bridge.
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from pathlib import Path
from typing import Callable, Sequence

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
        batch_size: int = 25,
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

    def _call_rt_etf_k(self, api, ts_code: str):
        """Call rt_etf_k API for ETF daily data."""
        return api.rt_etf_k(ts_code=ts_code)

    def _call_rt_k(self, api, ts_code: str):
        """Call rt_k API for stock daily data."""
        return api.rt_k(ts_code=ts_code)

    def _fetch_single_quote(self, api, ts_code: str) -> tuple[pd.DataFrame | None, str | None]:
        """Fetch quote for a single symbol, using rt_etf_k for ETF and rt_k for stocks."""
        try:
            # ETF codes must use rt_etf_k
            if self.is_etf_code(ts_code):
                data = self._call_rt_etf_k(api, ts_code)
                if data is not None and not data.empty:
                    return data, None
                return None, None

            # Stock codes use rt_k
            data = self._call_rt_k(api, ts_code)
            if data is not None and not data.empty:
                return data, None

        except Exception as e:
            return None, str(e)

        return None, None

    @staticmethod
    def _emit_progress(progress_callback: Callable[[dict], None] | None, **payload) -> None:
        if progress_callback is None:
            return
        progress_callback(payload)

    def _fetch_single_quote_task(self, request_index: int, ts_code: str, trade_date: str | None) -> dict:
        api_name = "rt_etf_k" if self.is_etf_code(ts_code) else "rt_k"
        batch_index = (request_index - 1) // self._batch_size + 1
        try:
            api = self._get_api()
            data, error = self._fetch_single_quote(api, ts_code)
        except Exception as exc:
            data, error = None, str(exc)
        return {
            "request_index": request_index,
            "ts_code": ts_code,
            "trade_date": trade_date,
            "api_name": api_name,
            "batch_index": batch_index,
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

        batch_count = ceil(len(symbols) / self._batch_size)
        minute_window_size = min(len(symbols), self._max_requests_per_minute)
        minute_window_count = ceil(len(symbols) / minute_window_size)
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
        )
        if self._verbose:
            print(
                f"\n[rt daily] fetching realtime daily bars for {len(symbols)} symbols "
                f"with {self._max_requests_per_minute} requests/minute",
                flush=True,
            )

        completed = 0
        worker_count = min(self._max_workers, minute_window_size)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for minute_window_index, window_start in enumerate(range(0, len(symbols), minute_window_size), start=1):
                window_symbols = symbols[window_start: window_start + minute_window_size]
                request_start = window_start + 1
                request_end = window_start + len(window_symbols)
                self._emit_progress(
                    progress_callback,
                    event="minute_window_start",
                    trade_date=trade_date,
                    minute_window_index=minute_window_index,
                    minute_window_count=minute_window_count,
                    minute_window_size=len(window_symbols),
                    request_start=request_start,
                    request_end=request_end,
                    total=len(symbols),
                )
                window_started_at = time.monotonic()
                futures = {
                    executor.submit(
                        self._fetch_single_quote_task,
                        request_start + offset,
                        ts_code,
                        trade_date,
                    ): ts_code
                    for offset, ts_code in enumerate(window_symbols)
                }
                for future in as_completed(futures):
                    completed += 1
                    result = future.result()
                    data = result["data"]
                    error = result["error"]
                    request_index = int(result["request_index"])
                    ts_code = str(result["ts_code"])
                    api_name = str(result["api_name"])
                    batch_index = int(result["batch_index"])

                    if data is not None and not data.empty:
                        frames.append(data)
                        close = data["close"].iloc[0] if "close" in data.columns and len(data.index) > 0 else None
                        pct_chg = data["pct_chg"].iloc[0] if "pct_chg" in data.columns and len(data.index) > 0 else None
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

        # Normalize column names
        combined.columns = [str(col).strip().lower() for col in combined.columns]

        # Ensure required columns exist
        if 'ts_code' in combined.columns:
            combined['ts_code'] = combined['ts_code'].astype(str).str.strip()

        return combined
