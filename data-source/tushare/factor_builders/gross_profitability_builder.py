"""Novy-Marx (2013) gross profitability factor builder.

GP = (revenue - oper_cost) / total_assets

Annual PIT, min_periods=1. Uses income (revenue, oper_cost) and
balancesheet (total_assets) matched on the SAME latest annual end_date.
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
    "GROSS_PROFITABILITY_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "gross_profitability"
MEASUREMENT = "lean_factor_gross_profitability"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]


_ANNOUNCE_FIELDS = ("f_ann_date", "ann_date")


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _load_pit_rows(api_name: str, ts_code: str, asof: str,
                   data_root: str) -> pd.DataFrame:
    """Full sorted PIT-annual frame (mirrors pit_financials filter logic)."""
    df = _read_partition(data_root, api_name, ts_code)
    if df.empty:
        return df
    if "ts_code" in df.columns:
        df = df[df["ts_code"].astype(str) == ts_code]
    if "report_type" in df.columns:
        df = df[df["report_type"].astype(str) == "1"]
    if "end_type" in df.columns:
        df = df[df["end_type"].astype(str) == "4"]
    ann_col = next((c for c in _ANNOUNCE_FIELDS if c in df.columns), None)
    if ann_col is None:
        return pd.DataFrame()
    df = df[df[ann_col].astype(str) <= asof]
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values(["end_date", ann_col])
    df = df.drop_duplicates(subset=["end_date"], keep="last")
    return df.sort_values("end_date").reset_index(drop=True)


def _to_float(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _compute_gross_profitability(income_rows: pd.DataFrame,
                                bs_rows: pd.DataFrame) -> float | None:
    """GP = (revenue - oper_cost) / total_assets for matched latest end_date."""
    if income_rows is None or income_rows.empty:
        return None
    if bs_rows is None or bs_rows.empty:
        return None
    for c in ("revenue", "oper_cost"):
        if c not in income_rows.columns:
            return None
    if "total_assets" not in bs_rows.columns:
        return None
    inc = income_rows.iloc[-1]
    bs = bs_rows.iloc[-1]
    # Match on end_date — they must be the same annual period.
    if str(inc.get("end_date", "")) != str(bs.get("end_date", "")):
        # Try to find a balancesheet row matching income's latest end_date.
        ed = str(inc.get("end_date", ""))
        m = bs_rows[bs_rows["end_date"].astype(str) == ed]
        if m.empty:
            return None
        bs = m.sort_values(
            [c for c in _ANNOUNCE_FIELDS if c in m.columns] or ["end_date"]
        ).iloc[-1]
    rev = _to_float(inc.get("revenue"))
    op_cost = _to_float(inc.get("oper_cost"), default=0.0) or 0.0
    ta = _to_float(bs.get("total_assets"))
    if rev is None or ta is None:
        return None
    if ta == 0:
        return None
    return (rev - op_cost) / ta


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
        income_rows = _load_pit_rows("income", ts_code, trade_date_compact,
                                     data_root)
        bs_rows = _load_pit_rows("balancesheet", ts_code, trade_date_compact,
                                 data_root)
        value = _compute_gross_profitability(income_rows, bs_rows)
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
