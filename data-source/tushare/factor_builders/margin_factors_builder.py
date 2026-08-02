"""Margin (融资融券) factors builder.

Factors:
- margin_balance_change: 融资余额20日变化率
- short_balance_change: 融券余额20日变化率
- margin_buy_ratio: 融资买入额占比
- short_sell_ratio: 融券卖出额占比
- margin_short_ratio: 融资余额/融券余额
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
    "MARGIN_FACTORS_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "margin_factors"
MEASUREMENT = "lean_factor_margin_factors"

_WINDOW = 20


def _read_margin_partition(data_root: str, ts_code: str) -> pd.DataFrame:
    """Read margin_detail table for a stock."""
    p = Path(data_root) / "margin_detail" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        return df.sort_values("trade_date")
    except Exception:
        return pd.DataFrame()


def _compute_margin_factors(margin_df: pd.DataFrame, asof: str, window: int = _WINDOW) -> dict:
    """Compute margin-based factors."""
    if margin_df.empty or "trade_date" not in margin_df.columns:
        return {}
    
    df = margin_df[margin_df["trade_date"] <= asof.replace("-", "")]
    if len(df) < window + 1:
        return {}
    
    current = df.iloc[-1]
    hist = df.iloc[-(window + 1):-1]
    
    result = {}
    
    # margin_balance_change
    if "rzye" in df.columns:
        rzye_current = float(current["rzye"])
        rzye_hist = hist["rzye"].astype(float).mean()
        if rzye_hist > 0:
            result["margin_balance_change"] = max(-5.0, min(5.0, (rzye_current - rzye_hist) / rzye_hist))
    
    # short_balance_change
    if "rqye" in df.columns:
        rqye_current = float(current["rqye"])
        rqye_hist = hist["rqye"].astype(float).mean()
        if rqye_hist > 0:
            result["short_balance_change"] = max(-5.0, min(5.0, (rqye_current - rqye_hist) / rqye_hist))
    
    # margin_buy_ratio
    if "rzmre" in df.columns and "rzrqye" in df.columns:
        rzmre = float(current.get("rzmre", 0))
        rzrqye = float(current.get("rzrqye", 1))
        if rzrqye > 0:
            result["margin_buy_ratio"] = rzmre / rzrqye
    
    # short_sell_ratio
    if "rqmcl" in df.columns and "rzrqye" in df.columns:
        rqmcl = float(current.get("rqmcl", 0))
        rzrqye = float(current.get("rzrqye", 1))
        if rzrqye > 0:
            result["short_sell_ratio"] = rqmcl / rzrqye
    
    # margin_short_ratio
    if "rzye" in df.columns and "rqye" in df.columns:
        rzye = float(current.get("rzye", 0))
        rqye = float(current.get("rqye", 0))
        if rqye > 0:
            result["margin_short_ratio"] = min(100.0, rzye / rqye)
    
    return result


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
    trade_date_compact = date_yyyy_mm_dd.replace("-", "")
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)
    
    lines: list[str] = []
    out_rows: list[dict] = []
    
    for ts_code in ts_codes:
        margin_df = _read_margin_partition(data_root, ts_code)
        factors = _compute_margin_factors(margin_df, trade_date_compact)
        if not factors:
            continue
        
        record = {"ts_code": ts_code, **factors}
        out_rows.append(record)
        
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, factors)
            if line:
                lines.append(line)
    
    if out_rows:
        df = pd.DataFrame(out_rows)
        df.to_parquet(factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False)
    
    if write_influxdb and lines:
        try:
            import urllib.request
            payload = "\n".join(lines) + "\n"
            req = urllib.request.Request(
                f"{(influx_url or DEFAULT_INFLUX_URL).rstrip('/')}/api/v2/write?org={influx_org or DEFAULT_INFLUX_ORG}&bucket={influx_bucket or DEFAULT_INFLUX_BUCKET}&precision=ns",
                data=payload.encode(),
                method="POST",
                headers={"Authorization": f"Token {influx_token or DEFAULT_INFLUX_TOKEN or ''}",
                        "Content-Type": "text/plain; charset=utf-8"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                pass
        except Exception:
            pass
    
    if not out_rows:
        return pd.DataFrame()
    return pd.DataFrame(out_rows)


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
            from barra_cne5_data_loader import BarraCNE5DataLoader
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=args.date.replace("-", ""), index_code="000300.SH"
            )
        except Exception as exc:
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
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
