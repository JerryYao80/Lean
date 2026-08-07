"""Turnover Anomaly factor builder — Z-score of turnover_rate vs trailing 20-day window.

Logic:
    z = (turnover_t - mean) / std, clamped to [-5, 5]
    If std == 0, return 0.0
    Need at least 21 days of data (20 history + current)

Input:
    {data_root}/daily_basic/ts_code={ts_code}/data.parquet (needs turnover_rate column)

Output:
    {result_root}/factor-zoo/turnover_anomaly/<YYYY-MM-DD>.parquet
    columns: ts_code, turnover_anomaly
    Also mirrored to InfluxDB measurement `lean_factor_turnover_anomaly`.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "TURNOVER_ANOMALY_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "turnover_anomaly"
MEASUREMENT = "lean_factor_turnover_anomaly"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_WINDOW = 20
_MIN_DAYS = _WINDOW + 1  # 20 history + current day


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    """Read {data_root}/{table}/ts_code={ts_code}/data.parquet. Empty if missing."""
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _compute_turnover_zscore(daily_basic_df: pd.DataFrame, asof: str, window: int = _WINDOW) -> float | None:
    """Compute Z-score of turnover_rate vs trailing window ending at asof.

    Args:
        daily_basic_df: DataFrame with trade_date and turnover_rate columns
        asof: Date string in YYYYMMDD format
        window: Trailing window size (default 20)

    Returns:
        Z-score clamped to [-5, 5], or None if insufficient data
    """
    if daily_basic_df is None or daily_basic_df.empty:
        return None
    if "trade_date" not in daily_basic_df.columns or "turnover_rate" not in daily_basic_df.columns:
        return None

    df = daily_basic_df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    df = df[df["trade_date"].astype(str) <= str(asof)]
    if df.empty:
        return None
    df = df.sort_values("trade_date")

    # Need at least window + 1 days (window history + current)
    if len(df) < window + 1:
        return None

    # Get trailing window (including current day)
    turnover_series = df["turnover_rate"].astype(float).tail(window + 1)
    turnover_series = turnover_series.replace([float("inf"), float("-inf")], float("nan")).dropna()

    if len(turnover_series) < window + 1:
        return None

    # Current turnover (last value)
    turnover_t = float(turnover_series.iloc[-1])

    # Historical window (exclude current day)
    hist_window = turnover_series.iloc[:-1]

    if len(hist_window) < window:
        return None

    mean = float(hist_window.mean())
    std = float(hist_window.std(ddof=1))  # Sample std

    if std == 0 or math.isnan(std):
        return 0.0

    z_score = (turnover_t - mean) / std

    # Clamp to [-5, 5]
    z_score = max(-5.0, min(5.0, z_score))

    return z_score


def _ts_ns(trade_date_compact: str) -> int:
    """Convert YYYYMMDD to UTC nanoseconds (15:00 Shanghai)."""
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    """Convert factors dict to InfluxDB line protocol."""
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    """Write lines to InfluxDB via HTTP API."""
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}",
                 "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    """'2024-01-05' -> '20240105'."""
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    """Build turnover_anomaly factor for one trade date across many ts_codes.

    Args:
        date_yyyy_mm_dd: 'YYYY-MM-DD' (e.g., '2024-01-05').
        ts_codes: list of Tushare codes like '600519.SH'.
        data_root: tushare parquet root. Defaults to DEFAULT_TS_PATH.
        result_root: result dir root. Defaults to DEFAULT_RESULT_ROOT.
        write_influxdb: if True, also write to InfluxDB.
        influx_*: InfluxDB connection params (env defaults if None).

    Returns:
        DataFrame with columns OUTPUT_COLUMNS, one row per ts_code that had
        sufficient data to compute the factor.
    """
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        daily_basic_df = _read_partition(data_root, "daily_basic", ts_code)
        value = _compute_turnover_zscore(daily_basic_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)

    if out_rows:
        pd.DataFrame(out_rows).to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass

    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader  # noqa: E402
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: failed to load CSI300 universe: {exc}",
                  file=sys.stderr)
            ts_codes = []

    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2

    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
