#!/usr/bin/env python3
"""
Tushare rt_min minute downloader.

This module fetches raw minute bars from the realtime `rt_min` endpoint and
stores them as per-trade-date parquet partitions. It also exposes the same
aggregation used by the live feature bridge so the minute feed and the live
feature snapshots share a single normalization path.
"""
import argparse
import json
import math
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import config


DEFAULT_MAX_RT_MIN_ROWS_PER_REQUEST = 1000
DEFAULT_TRADING_SESSION_MINUTES = 240


def coerce_minute_frequency(value, default: str = "1MIN") -> str:
    if value is None:
        return default
    text = str(value).strip().upper()
    if not text:
        return default
    if text.endswith("MIN") and text[:-3].isdigit():
        return text
    return default


def _normalize_trade_time_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if " " in text or "-" in text or "/" in text or ":" in text:
        return text
    if text.isdigit() and len(text) == 6:
        return f"{text[:2]}:{text[2:4]}:{text[4:6]}"
    if text.isdigit() and len(text) == 4:
        return f"{text[:2]}:{text[2:4]}:00"
    return text


def _format_trade_date(value: str | datetime | None = None) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if value is None:
        return datetime.now().strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return pd.Timestamp(text).strftime("%Y%m%d")


def _parse_trade_timestamps(values: pd.Series, trade_date: str | None = None) -> pd.Series:
    normalized = values.astype(str).map(_normalize_trade_time_text)
    parsed = pd.to_datetime(normalized, errors="coerce")
    missing = parsed.isna()
    if missing.any():
        base_date = _format_trade_date(trade_date)
        parsed.loc[missing] = pd.to_datetime(
            pd.Series([base_date] * int(missing.sum()), index=normalized.loc[missing].index).str.replace(
                r"(\d{4})(\d{2})(\d{2})",
                r"\1-\2-\3",
                regex=True,
            ) + " " + normalized.loc[missing],
            errors="coerce",
        )
    return parsed


def normalize_rt_min_frame(frame: pd.DataFrame, trade_date: str | None = None) -> pd.DataFrame:
    data = frame.copy()
    if data.empty:
        return data

    data.columns = [str(column).strip().lower() for column in data.columns]
    rename_map = {
        "volume": "vol",
        "datetime": "feature_timestamp",
    }
    data = data.rename(columns={key: value for key, value in rename_map.items() if key in data.columns})

    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    if "trade_time" not in data.columns and "time" in data.columns:
        data["trade_time"] = data["time"]

    if "feature_timestamp" not in data.columns:
        if "trade_time" in data.columns:
            timestamps = _parse_trade_timestamps(data["trade_time"], trade_date=trade_date)
        else:
            fallback_date = _format_trade_date(trade_date)
            timestamps = pd.to_datetime(
                pd.Series([f"{fallback_date[:4]}-{fallback_date[4:6]}-{fallback_date[6:]} 00:00:00"] * len(data.index))
            )
        data["feature_timestamp"] = timestamps.dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        timestamps = pd.to_datetime(data["feature_timestamp"], errors="coerce")

    if "trade_date" not in data.columns:
        data["trade_date"] = timestamps.dt.strftime("%Y%m%d")
    else:
        data["trade_date"] = data["trade_date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)

    if trade_date:
        fallback_trade_date = _format_trade_date(trade_date)
        data.loc[data["trade_date"].isna() | (data["trade_date"] == ""), "trade_date"] = fallback_trade_date

    if "price" not in data.columns and "close" in data.columns:
        data["price"] = data["close"]

    if "ts_code" in data.columns:
        data["ts_code"] = data["ts_code"].astype(str).str.strip()

    return data


def aggregate_rt_min_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = normalize_rt_min_frame(frame)
    if data.empty:
        return pd.DataFrame()

    data["parsed_feature_timestamp"] = pd.to_datetime(data["feature_timestamp"], errors="coerce")
    data = data.dropna(subset=["parsed_feature_timestamp"])
    if data.empty:
        return pd.DataFrame()

    data = data.sort_values(["ts_code", "parsed_feature_timestamp"], kind="stable").reset_index(drop=True)
    snapshots: list[dict] = []

    for ts_code, group in data.groupby("ts_code", sort=False):
        if not str(ts_code).strip():
            continue

        open_values = group["open"].dropna() if "open" in group.columns else pd.Series(dtype="float64")
        high_values = group["high"].dropna() if "high" in group.columns else pd.Series(dtype="float64")
        low_values = group["low"].dropna() if "low" in group.columns else pd.Series(dtype="float64")
        close_values = group["close"].dropna() if "close" in group.columns else pd.Series(dtype="float64")
        vol_values = group["vol"].dropna() if "vol" in group.columns else pd.Series(dtype="float64")
        amount_values = group["amount"].dropna() if "amount" in group.columns else pd.Series(dtype="float64")
        last_row = group.iloc[-1]

        current_trade_date = str(last_row.get("trade_date") or "").strip()
        if len(current_trade_date) != 8 or not current_trade_date.isdigit():
            current_trade_date = pd.Timestamp(last_row["parsed_feature_timestamp"]).strftime("%Y%m%d")

        snapshot = {
            "ts_code": str(ts_code).strip(),
            "trade_date": current_trade_date,
            "feature_timestamp": pd.Timestamp(last_row["parsed_feature_timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "minute_bar_count": int(len(group.index)),
        }
        if not open_values.empty:
            snapshot["open"] = float(open_values.iloc[0])
        if not high_values.empty:
            snapshot["high"] = float(high_values.max())
        if not low_values.empty:
            snapshot["low"] = float(low_values.min())
        if not close_values.empty:
            snapshot["close"] = float(close_values.iloc[-1])
            snapshot["price"] = snapshot["close"]
        if not vol_values.empty:
            snapshot["vol"] = float(vol_values.sum())
        if not amount_values.empty:
            snapshot["amount"] = float(amount_values.sum())
        snapshots.append(snapshot)

    return pd.DataFrame(snapshots)


def merge_rt_min_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (existing, incoming) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = normalize_rt_min_frame(combined)
    if combined.empty:
        return combined

    combined["parsed_feature_timestamp"] = pd.to_datetime(combined["feature_timestamp"], errors="coerce")
    combined = combined.sort_values(["ts_code", "trade_date", "parsed_feature_timestamp"], kind="stable")
    dedupe_columns = [column for column in ("ts_code", "trade_date", "feature_timestamp") if column in combined.columns]
    if dedupe_columns:
        combined = combined.drop_duplicates(subset=dedupe_columns, keep="last")
    combined = combined.drop(columns=["parsed_feature_timestamp"], errors="ignore").reset_index(drop=True)
    return combined


def rt_min_file_path(output_root: str | Path, ts_code: str, trade_date: str, frequency: str = "1MIN") -> Path:
    return (
        Path(output_root)
        / "rt_min"
        / f"freq={coerce_minute_frequency(frequency)}"
        / f"date={_format_trade_date(trade_date)}"
        / f"ts_code={ts_code}"
        / "data.parquet"
    )


class TushareRtMinClient:
    MAX_RT_MIN_ROWS_PER_REQUEST = DEFAULT_MAX_RT_MIN_ROWS_PER_REQUEST
    TRADING_SESSION_MINUTES = DEFAULT_TRADING_SESSION_MINUTES

    def __init__(
        self,
        token: str | None = None,
        batch_size: int = 25,
        http_url: str | None = None,
        frequency: str = "1MIN",
        max_rows_per_request: int = DEFAULT_MAX_RT_MIN_ROWS_PER_REQUEST,
    ):
        self._token = (token or config.TUSHARE_TOKEN).strip()
        self._batch_size = max(1, int(batch_size))
        self._http_url = (http_url or getattr(config, "TUSHARE_API_URL", "") or "").strip()
        self._frequency = coerce_minute_frequency(frequency, "1MIN")
        self._max_rows_per_request = max(1, int(max_rows_per_request))
        self._api = None
        self._api_lock = threading.Lock()
        self._supports_batch_queries = True

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

    def _frequency_minutes(self) -> int:
        return max(1, int(self._frequency[:-3]))

    def _effective_symbols_per_request(self) -> int:
        bars_per_symbol = max(1, math.ceil(self.TRADING_SESSION_MINUTES / self._frequency_minutes()))
        safe_limit = max(1, self._max_rows_per_request // bars_per_symbol)
        return max(1, min(self._batch_size, safe_limit))

    @staticmethod
    def _should_retry_symbols_individually(error: Exception) -> bool:
        message = str(error)
        keywords = (
            "不支持的市场后缀",
            "检查代码格式",
            "ts_code",
            "code format",
            "market suffix",
        )
        return any(keyword in message for keyword in keywords)

    def _call_rt_min(self, api, ts_code: str):
        with self._api_lock:
            return api.rt_min(ts_code=ts_code, freq=self._frequency)

    def _fetch_batch_frames(self, api, batch: list[str]) -> list[pd.DataFrame]:
        if self._supports_batch_queries:
            query = ",".join(batch)
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

    def fetch_raw(self, ts_codes: Sequence[str], trade_date: str | None = None) -> pd.DataFrame:
        api = self._get_api()
        frames: list[pd.DataFrame] = []
        symbols = [str(ts_code).strip() for ts_code in ts_codes if str(ts_code).strip()]
        if not symbols:
            return pd.DataFrame()

        symbols_per_request = self._effective_symbols_per_request()
        for start in range(0, len(symbols), symbols_per_request):
            batch = symbols[start:start + symbols_per_request]
            frames.extend(self._fetch_batch_frames(api, batch))

        if not frames:
            return pd.DataFrame()

        return normalize_rt_min_frame(pd.concat(frames, ignore_index=True), trade_date=trade_date)

    def fetch_quotes(self, ts_codes: Sequence[str], trade_date: str | None = None) -> pd.DataFrame:
        return aggregate_rt_min_frame(self.fetch_raw(ts_codes, trade_date=trade_date))

    @staticmethod
    def normalize_minute_bars(frame: pd.DataFrame) -> pd.DataFrame:
        return normalize_rt_min_frame(frame)

    @staticmethod
    def aggregate_minute_bars(frame: pd.DataFrame) -> pd.DataFrame:
        return aggregate_rt_min_frame(frame)


class RtMinDownloader:
    def __init__(
        self,
        output_root: str | Path | None = None,
        client: TushareRtMinClient | None = None,
        batch_size: int = 25,
        frequency: str = "1MIN",
        token: str | None = None,
        http_url: str | None = None,
    ):
        self.output_root = Path(output_root or config.DATA_DIR)
        self.frequency = coerce_minute_frequency(frequency, "1MIN")
        self.client = client or TushareRtMinClient(
            token=token,
            batch_size=batch_size,
            http_url=http_url,
            frequency=self.frequency,
        )

    def save_frame(self, frame: pd.DataFrame) -> dict:
        normalized = normalize_rt_min_frame(frame)
        if normalized.empty:
            return {
                "status": "empty",
                "rows": 0,
                "files": 0,
                "output_root": str(self.output_root),
                "frequency": self.frequency,
            }

        rows_written = 0
        files_written = 0
        saved_paths: list[str] = []

        for (trade_date, ts_code), group in normalized.groupby(["trade_date", "ts_code"], sort=False):
            if not trade_date or not ts_code:
                continue
            target = rt_min_file_path(self.output_root, str(ts_code), str(trade_date), self.frequency)
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = pd.read_parquet(target) if target.exists() else pd.DataFrame()
            merged = merge_rt_min_frames(existing, group)
            merged.to_parquet(target, engine="pyarrow", index=False)
            rows_written += len(group.index)
            files_written += 1
            saved_paths.append(str(target))

        return {
            "status": "ok" if files_written > 0 else "empty",
            "rows": rows_written,
            "files": files_written,
            "output_root": str(self.output_root),
            "frequency": self.frequency,
            "saved_paths": saved_paths,
        }

    def download(self, ts_codes: Sequence[str], trade_date: str | None = None) -> dict:
        symbols = [str(ts_code).strip() for ts_code in ts_codes if str(ts_code).strip()]
        frame = self.client.fetch_raw(symbols, trade_date=trade_date)
        report = self.save_frame(frame)
        report["requested_symbol_count"] = len(symbols)
        report["trade_date"] = _format_trade_date(trade_date)
        return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Tushare rt_min minute bars to parquet partitions")
    parser.add_argument("--ts-code", action="append", default=[], help="Single ts_code or comma-separated ts_code list")
    parser.add_argument("--trade-date", help="Force trade date (YYYYMMDD)")
    parser.add_argument("--frequency", default="1MIN")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--output-root", default=config.DATA_DIR)
    parser.add_argument("--token")
    parser.add_argument("--http-url", default=config.TUSHARE_API_URL)
    return parser.parse_args()


def _expand_symbols(values: Sequence[str]) -> list[str]:
    symbols: list[str] = []
    for value in values:
        parts = [part.strip() for part in str(value).split(",")]
        symbols.extend([part for part in parts if part])
    return symbols


def main() -> int:
    args = parse_args()
    symbols = _expand_symbols(args.ts_code)
    if not symbols:
        raise SystemExit("At least one --ts-code is required")

    downloader = RtMinDownloader(
        output_root=args.output_root,
        batch_size=args.batch_size,
        frequency=args.frequency,
        token=args.token,
        http_url=args.http_url,
    )
    report = downloader.download(symbols, trade_date=args.trade_date)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
