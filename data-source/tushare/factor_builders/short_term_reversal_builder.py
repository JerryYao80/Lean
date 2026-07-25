"""Jegadeesh-Lehmann 1-month short-term reversal factor builder.

REV20 = -(close_t / close_{t-20} - 1) using adj_close = close * adj_factor.
Need >= 21 days (t and t-20). value = REV20 float.
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
    "SHORT_TERM_REVERSAL_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "short_term_reversal"
MEASUREMENT = "lean_factor_short_term_reversal"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_LAG = 20


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _compute_reversal(daily_df: pd.DataFrame, adj_df: pd.DataFrame,
                      asof: str) -> float | None:
    if daily_df is None or daily_df.empty:
        return None
    if "trade_date" not in daily_df.columns or "close" not in daily_df.columns:
        return None
    if adj_df is None or adj_df.empty or "adj_factor" not in adj_df.columns:
        return None

    d = daily_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    if d.empty:
        return None
    d = d.sort_values("trade_date")

    a = adj_df.copy()
    a["trade_date"] = a["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    a = a[a["trade_date"].astype(str) <= str(asof)]
    if a.empty:
        return None
    a = a.sort_values("trade_date")

    merged = d[["trade_date", "close"]].merge(
        a[["trade_date", "adj_factor"]], on="trade_date", how="left"
    )
    merged["adj_factor"] = merged["adj_factor"].fillna(1.0)
    merged["adj_close"] = merged["close"].astype(float) * merged["adj_factor"].astype(float)
    merged = merged.sort_values("trade_date").dropna(subset=["adj_close"])

    # Need at least _LAG + 1 rows to compute close_t / close_{t-20} - 1.
    if len(merged) < _LAG + 1:
        return None
    close_t = float(merged["adj_close"].iloc[-1])
    close_t_minus_20 = float(merged["adj_close"].iloc[-1 - _LAG])
    if close_t_minus_20 == 0:
        return None
    return -(close_t / close_t_minus_20 - 1.0)


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
    date_dir = Path(result_root) / "factor-zoo" / FACTOR_ID / date_yyyy_mm_dd
    date_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        daily_df = _read_partition(data_root, "daily", ts_code)
        adj_df = _read_partition(data_root, "adj_factor", ts_code)
        value = _compute_reversal(daily_df, adj_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        pd.DataFrame([record]).to_parquet(
            date_dir / f"{ts_code}.parquet", index=False
        )
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)

    if write_influxdb and lines:
        write_influx(
            lines,
            url=influx_url or DEFAULT_INFLUX_URL,
            org=influx_org or DEFAULT_INFLUX_ORG,
            bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
            token=influx_token or DEFAULT_INFLUX_TOKEN or "",
        )

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
