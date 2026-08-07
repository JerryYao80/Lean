"""PIT (point-in-time) annual financials reader (spec §4.1, §7 STEP 0b).

Thin wrapper that reuses the SAME PIT semantics as
data-source/tushare/barra_cne5_data_loader.py:69 load_point_in_time:
  filter f_ann_date/ann_date <= asof  ->  sort by f_ann_date  ->
  take the latest revision per end_date (iloc[-1]).

This single PIT boundary is shared by all 4 financial factors
(accruals_sloan, gross_profitability, asset_growth, roe_change) so the
"财报延迟披露" lookahead trap is closed in ONE place (0723 review point 1).

Only ANNUAL rows are returned (end_type='4', report_type='1' consolidated),
matching Sloan/Novy-Marx/Cooper-Gulen-Schill canonical annual measures.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow.parquet as pq


_ANNOUNCE_FIELDS = ("f_ann_date", "ann_date")
_END_DATE_FIELD = "end_date"
_REPORT_TYPE_FIELD = "report_type"
_END_TYPE_FIELD = "end_type"
# Columns always needed for PIT filtering/selection, alongside the caller's
# value_col. Used for parquet column projection (Minor #3) so we don't read
# all ~90 balancesheet columns per stock.
_BASE_COLS = ("ts_code", "end_date", "f_ann_date", "ann_date",
             "report_type", "end_type")


def _table_dir(api_name: str, data_root: str) -> Path:
    return Path(data_root) / api_name


def _read_all_partitions(table_dir: Path, value_col: str) -> pd.DataFrame:
    """Read all data.parquet partitions under table_dir.

    Robust to a single corrupt partition (try/except -> skip), mirroring the
    Task 1/2 corrupt-file resilience pattern. Uses column projection so only
    the value_col + PIT-filter columns are materialized (not all ~90).
    """
    frames = []
    for f in table_dir.rglob("data.parquet"):
        try:
            pf = pq.ParquetFile(f)
            cols = [c for c in (value_col, *_BASE_COLS)
                    if c in pf.schema_arrow.names]
            df = pf.read(columns=cols).to_pandas()
        except Exception:
            # Skip corrupt/unreadable partition (mirrors Task 1/2 fix).
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_pit_annual(api_name: str, ts_code: str, asof: str,
                    data_root: str, value_col: str,
                    min_periods: int = 1) -> Optional[dict]:
    """Return the most-recent PIT-annual row for ts_code as-of `asof`.

    Returns None if fewer than `min_periods` annual periods are available
    (e.g. newly-listed firms with <2 annuals -> factor = Stale/NaN).

    `asof` is YYYYMMDD string. Only rows with f_ann_date (fallback ann_date)
    <= asof are eligible. Among eligible rows, picks the latest f_ann_date per
    end_date (handles restatements), then the latest end_date (most recent
    annual report).
    """
    table_dir = _table_dir(api_name, data_root) / f"ts_code={ts_code}"
    if not table_dir.exists():
        table_dir = _table_dir(api_name, data_root)
    df = _read_all_partitions(table_dir, value_col)
    if df.empty:
        return None
    if "ts_code" in df.columns:
        df = df[df["ts_code"].astype(str) == ts_code]
    if _REPORT_TYPE_FIELD in df.columns:
        df = df[df[_REPORT_TYPE_FIELD].astype(str) == "1"]
    if _END_TYPE_FIELD in df.columns:
        df = df[df[_END_TYPE_FIELD].astype(str) == "4"]
    ann_col = next((c for c in _ANNOUNCE_FIELDS if c in df.columns), None)
    if ann_col is None:
        return None
    df = df[df[ann_col].astype(str) <= asof]
    if df.empty:
        return None
    df = df.sort_values([_END_DATE_FIELD, ann_col])
    df = df.drop_duplicates(subset=[_END_DATE_FIELD], keep="last")
    if len(df) < min_periods:
        return None
    row = df.sort_values(_END_DATE_FIELD).iloc[-1]
    if value_col not in row.index:
        return None
    out = {}
    for c in row.index:
        if c == value_col:
            v = row[c]
            # Coerce np.float64 -> native float (Minor #4); leave None/NaN as-is.
            out[c] = float(v) if v is not None else v
        elif c in (_END_DATE_FIELD, ann_col, _REPORT_TYPE_FIELD, _END_TYPE_FIELD):
            out[c] = row[c]
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PIT annual financials probe (STEP 0b)")
    ap.add_argument("--api", default="balancesheet")
    ap.add_argument("--ts-code", required=True)
    ap.add_argument("--asof", required=True)
    ap.add_argument("--value-col", required=True)
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--min-periods", type=int, default=1)
    args = ap.parse_args()
    r = load_pit_annual(args.api, args.ts_code, args.asof, args.data_root,
                       args.value_col, args.min_periods)
    if r is None:
        print("None (insufficient PIT history)")
    else:
        print(r)


if __name__ == "__main__":
    main()
