"""Limit behavior factor builder.

Counts limit-up and limit-down occurrences for each stock in trailing N days.
Score = (up_count + down_count) / N, range [0, 2].
If no limit records, score = 0.0.

Reads limit_list_d table which is date-partitioned (not per-ts_code).
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
    "LIMIT_BEHAVIOR_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "limit_behavior"
MEASUREMENT = "lean_factor_limit_behavior"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

DEFAULT_WINDOW = 5


def _read_limit_list(data_root: str) -> pd.DataFrame:
    """Read all date partitions of limit_list_d from data_root.

    Expects {data_root}/limit_list_d/year=YYYY/data.parquet files.
    Returns concatenated DataFrame with columns including trade_date, ts_code,
    up_stat, limit.  Empty DataFrame if no data found.
    """
    root = Path(data_root) / "limit_list_d"
    if not root.exists():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for year_dir in root.glob("year=*"):
        p = year_dir / "data.parquet"
        if p.exists():
            try:
                df = pd.read_parquet(p)
                frames.append(df)
            except Exception:
                continue

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def _compute_limit_behavior(
    limit_df: pd.DataFrame,
    ts_code: str,
    asof: str,
    window: int = DEFAULT_WINDOW,
) -> float | None:
    """Compute limit behavior score for a single stock.

    Count limit-up and limit-down occurrences in the trailing `window` days
    up to and including `asof`.  Score = (up_count + down_count) / window.
    Returns 0.0 if no limit records exist.  Returns None if input is empty.
    """
    if limit_df is None or limit_df.empty:
        return None

    df = limit_df.copy()

    # Ensure trade_date is string for comparison
    if "trade_date" not in df.columns:
        return None
    df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)

    # Filter by ts_code
    if "ts_code" not in df.columns:
        return None
    df = df[df["ts_code"].astype(str) == ts_code]
    if df.empty:
        return 0.0

    # Filter trailing window: trade_date <= asof, within window days
    df = df[df["trade_date"].astype(str) <= str(asof)]
    if df.empty:
        return 0.0

    # Sort by date descending and take top window rows
    df = df.sort_values("trade_date", ascending=False)
    df = df.head(window)

    up_count = 0
    down_count = 0

    for _, row in df.iterrows():
        limit_val = row.get("limit")
        up_stat = row.get("up_stat")
        # up_stat non-null indicates a limit hit
        if up_stat is not None and pd.notna(up_stat):
            if limit_val == "U":
                up_count += 1
            elif limit_val == "D":
                down_count += 1

    return (up_count + down_count) / window


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
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
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)

    # Read all limit_list_d partitions once
    limit_df = _read_limit_list(data_root)

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        value = _compute_limit_behavior(limit_df, ts_code, trade_date_compact)
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
    parser.add_argument("--influx", action="store_true",
                        help="Also write to InfluxDB")
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
