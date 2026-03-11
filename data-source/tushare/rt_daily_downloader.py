#!/usr/bin/env python3
"""
Tushare rt_etf_k and rt_k daily downloader.

This module fetches daily bars from the realtime `rt_etf_k` (ETF) and `rt_k` (stock) endpoints
and provides aggregated quotes for the live feature bridge.
"""
import sys
import threading
from pathlib import Path
from typing import Sequence

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
    ):
        self._token = (token or config.TUSHARE_TOKEN).strip()
        self._batch_size = max(1, int(batch_size))
        self._http_url = (http_url or getattr(config, "TUSHARE_API_URL", "") or "").strip()
        self._api = None
        self._api_lock = threading.Lock()

    def _get_api(self):
        if self._api is not None:
            return self._api

        import tushare as ts  # type: ignore

        api = ts.pro_api(
            self._token,
            timeout=getattr(config, "TUSHARE_TIMEOUT_SECONDS", 15),
        )
        api._DataApi__token = self._token
        if self._http_url:
            api._DataApi__http_url = self._http_url
        api._DataApi__timeout = getattr(config, "TUSHARE_TIMEOUT_SECONDS", 15)
        self._api = api
        return self._api

    @staticmethod
    def _is_etf(ts_code: str) -> bool:
        """Check if ts_code is an ETF based on suffix."""
        code = str(ts_code).strip().upper()
        # ETF codes typically end with .SH or .SZ
        return code.endswith('.SH') or code.endswith('.SZ')

    def _call_rt_etf_k(self, api, ts_code: str):
        """Call rt_etf_k API for ETF daily data."""
        with self._api_lock:
            return api.rt_etf_k(ts_code=ts_code)

    def _call_rt_k(self, api, ts_code: str):
        """Call rt_k API for stock daily data."""
        with self._api_lock:
            return api.rt_k(ts_code=ts_code)

    def _fetch_single_quote(self, api, ts_code: str) -> pd.DataFrame | None:
        """Fetch quote for a single symbol, using rt_etf_k for ETF and rt_k for stocks."""
        try:
            # ETF codes must use rt_etf_k
            if self._is_etf(ts_code):
                data = self._call_rt_etf_k(api, ts_code)
                if data is not None and not data.empty:
                    return data
                return None

            # Stock codes use rt_k
            data = self._call_rt_k(api, ts_code)
            if data is not None and not data.empty:
                return data

        except Exception as e:
            print(f"⚠️  Failed to fetch {ts_code}: {e}", file=sys.stderr)

        return None

    def fetch_quotes(self, ts_codes: Sequence[str], trade_date: str | None = None) -> pd.DataFrame:
        """
        Fetch realtime daily quotes for multiple symbols.

        Args:
            ts_codes: List of Tushare codes (e.g., ['510300.SH', '000001.SZ'])
            trade_date: Optional trade date filter (not used for realtime APIs)

        Returns:
            DataFrame with columns: ts_code, trade_date, open, high, low, close,
                                   pre_close, pct_chg, vol, amount, etc.
        """
        api = self._get_api()
        frames: list[pd.DataFrame] = []
        symbols = [str(ts_code).strip() for ts_code in ts_codes if str(ts_code).strip()]

        if not symbols:
            return pd.DataFrame()

        print(f"\n📊 Fetching daily data for {len(symbols)} symbols...")

        for idx, ts_code in enumerate(symbols, 1):
            print(f"  [{idx}/{len(symbols)}] {ts_code}...", end=" ", flush=True)

            data = self._fetch_single_quote(api, ts_code)
            if data is not None and not data.empty:
                frames.append(data)
                # Display key metrics
                if 'close' in data.columns and 'pct_chg' in data.columns:
                    close = data['close'].iloc[0] if len(data) > 0 else None
                    pct_chg = data['pct_chg'].iloc[0] if len(data) > 0 else None
                    if close is not None and pct_chg is not None:
                        arrow = "📈" if pct_chg > 0 else "📉" if pct_chg < 0 else "➡️"
                        print(f"{arrow} ¥{close:.2f} ({pct_chg:+.2f}%)")
                    else:
                        print("✅")
                else:
                    print("✅")
            else:
                print("❌ No data")

        if not frames:
            print("\n⚠️  No data fetched for any symbol")
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        print(f"\n✅ Successfully fetched {len(combined)} records\n")

        # Normalize column names
        combined.columns = [str(col).strip().lower() for col in combined.columns]

        # Ensure required columns exist
        if 'ts_code' in combined.columns:
            combined['ts_code'] = combined['ts_code'].astype(str).str.strip()

        return combined
