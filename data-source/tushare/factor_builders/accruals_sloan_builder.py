"""Sloan (1996) accruals factor builder.

accruals = (d_non_cash_wc - d_cash - d_depr_amort) / avg(total_assets)

Annual PIT (point-in-time): only consolidated annual rows
(report_type="1", end_type="4") whose f_ann_date/ann_date <= asof are
eligible. min_periods=2 (need current + prior year).

Mirrors crowding_factor_builder.build_day shape (parquet to
result/factor-zoo/<id>/<date>/<ts_code>.parquet + InfluxDB line).
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
    "ACCRUALS_SLOAN_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "accruals_sloan"
MEASUREMENT = "lean_factor_accruals_sloan"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]


# ────────────────────────────────────────────────────────────────────────────
# PIT reader (local helper — mirrors pit_financials filter logic, returns the
# full sorted annual DataFrame so multi-column factors get all columns).
# ────────────────────────────────────────────────────────────────────────────
_ANNOUNCE_FIELDS = ("f_ann_date", "ann_date")
_BASE_COLS = ("ts_code", "end_date", "f_ann_date", "ann_date",
              "report_type", "end_type", "comp_type")


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    """Read {data_root}/{table}/ts_code={ts_code}/data.parquet. Empty if missing."""
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _load_pit_rows(api_name: str, ts_code: str, asof: str,
                   data_root: str) -> pd.DataFrame:
    """Return the full sorted PIT-annual frame for ts_code as-of `asof`.

    Mirrors pit_financials.load_pit_annual filter logic but returns ALL
    columns (not just value_col) so multi-column factors (accruals needs ~7
    balancesheet cols + 3 cashflow cols) get everything in one pass.

    Filter: report_type="1", end_type="4", f_ann_date (fallback ann_date)
    <= asof, dedup keep last per end_date (handles restatements), sort by
    end_date. Returns empty DataFrame if no eligible rows.
    """
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


# ────────────────────────────────────────────────────────────────────────────
# Factor computation
# ────────────────────────────────────────────────────────────────────────────
def _compute_accruals(bs_rows: pd.DataFrame,
                      cf_rows: pd.DataFrame) -> float | None:
    """Sloan accruals from current (iloc[-1]) + prior (iloc[-2]) annual rows.

    Returns None if any required column is missing or < 2 annuals.
    """
    if bs_rows is None or len(bs_rows) < 2:
        return None
    required_bs = ["total_assets", "total_cur_assets", "money_cap",
                   "total_cur_liab"]
    for c in required_bs:
        if c not in bs_rows.columns:
            return None

    cur = bs_rows.iloc[-1]
    prev = bs_rows.iloc[-2]

    def _non_cash_wc(row):
        tca = _to_float(row.get("total_cur_assets"))
        mc = _to_float(row.get("money_cap"))
        tcl = _to_float(row.get("total_cur_liab"))
        st = _to_float(row.get("st_borr"), default=0.0) or 0.0
        np_ = _to_float(row.get("notes_payable"), default=0.0) or 0.0
        ncl1y = _to_float(row.get("non_cur_liab_due_1y"), default=0.0) or 0.0
        if tca is None or mc is None or tcl is None:
            return None
        return (tca - mc) - (tcl - st - np_ - ncl1y)

    ncwc_t = _non_cash_wc(cur)
    ncwc_prev = _non_cash_wc(prev)
    if ncwc_t is None or ncwc_prev is None:
        return None
    d_ncwc = ncwc_t - ncwc_prev

    mc_t = _to_float(cur.get("money_cap"))
    mc_prev = _to_float(prev.get("money_cap"))
    if mc_t is None or mc_prev is None:
        return None
    d_cash = mc_t - mc_prev

    # Depreciation + amortization from cashflow (matched on end_date).
    def _depr(row):
        if row is None:
            return None
        d = _to_float(row.get("depr_fa_coga_dpba"), default=0.0) or 0.0
        a = _to_float(row.get("amort_intang_assets"), default=0.0) or 0.0
        l = _to_float(row.get("lt_amort_deferred_exp"), default=0.0) or 0.0
        return d + a + l

    def _cf_for_end_date(end_date):
        if cf_rows is None or cf_rows.empty or "end_date" not in cf_rows.columns:
            return None
        m = cf_rows[cf_rows["end_date"].astype(str) == str(end_date)]
        if m.empty:
            return None
        return m.sort_values(
            [c for c in _ANNOUNCE_FIELDS if c in m.columns] or ["end_date"]
        ).iloc[-1]

    cf_cur = _cf_for_end_date(cur["end_date"])
    cf_prev = _cf_for_end_date(prev["end_date"])
    depr_t = _depr(cf_cur)
    depr_prev = _depr(cf_prev)
    if depr_t is None or depr_prev is None:
        return None
    d_depr = depr_t - depr_prev

    ta_t = _to_float(cur.get("total_assets"))
    ta_prev = _to_float(prev.get("total_assets"))
    if ta_t is None or ta_prev is None:
        return None
    avg_ta = (ta_t + ta_prev) / 2.0
    if avg_ta == 0:
        return None

    return (d_ncwc - d_cash - d_depr) / avg_ta


# ────────────────────────────────────────────────────────────────────────────
# Day build
# ────────────────────────────────────────────────────────────────────────────
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

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        bs_rows = _load_pit_rows("balancesheet", ts_code, trade_date_compact,
                                 data_root)
        cf_rows = _load_pit_rows("cashflow", ts_code, trade_date_compact,
                                 data_root)
        value = _compute_accruals(bs_rows, cf_rows)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        pass  # batch write below
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
