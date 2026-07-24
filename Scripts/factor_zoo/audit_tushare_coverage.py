"""Audit on-disk tushare coverage for factor-zoo readiness (spec §6).

Reads parquet METADATA only (no full scans) to determine per-table:
  - rows / file count
  - last trade_date present
  - status: NOT_DOWNLOADED | EMPTY | STALE | OK

STALE = last_date < (latest_trade_date - 10 calendar days) as a loose proxy
when we don't have the full trade_cal; the real freshness daemon (Phase 3)
uses trade_cal exact days.

Usage:
  python3 Scripts/factor_zoo/audit_tushare_coverage.py [--data-root DIR]
                                                        [--latest-trade-date YYYYMMDD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

import pyarrow.compute as pc
import pyarrow.parquet as pq


class CoverageStatus(str, Enum):
    NOT_DOWNLOADED = "NOT_DOWNLOADED"
    EMPTY = "EMPTY"
    STALE = "STALE"
    OK = "OK"


@dataclass
class TableReport:
    api_name: str
    status: CoverageStatus
    rows: int = 0
    last_date: Optional[str] = None
    partition_files: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def _table_dir(api_name: str, data_root: str) -> Path:
    return Path(data_root) / api_name


def _iter_parquet_files(table_dir: Path):
    """Yield all data.parquet under a table dir (ts_code=/year=/trade_date= partitions)."""
    if not table_dir.exists():
        return
    for p in table_dir.rglob("data.parquet"):
        yield p


# Date columns tried in order of preference.
_DATE_COLS = ("trade_date", "cal_date", "ann_date", "f_ann_date", "end_date")


def _scan_table(table_dir: Path) -> tuple[int, Optional[str], int]:
    """Single pass over parquet files: returns (total_rows, last_date, file_count).

    Reads only metadata + the matched date column (never a full scan).
    Corrupt/unreadable files are skipped silently — a single bad footer across
    5000+ partition files will NOT abort the run.
    """
    total_rows = 0
    last: Optional[str] = None
    file_count = 0
    for f in _iter_parquet_files(table_dir):
        # Open footer + read metadata/schema/date column in ONE try so a corrupt
        # footer (pq.ParquetFile) or corrupt body (pf.read) skips just this file.
        try:
            pf = pq.ParquetFile(f)
            n = pf.metadata.num_rows
            total_rows += n
            file_count += 1
            if n == 0:
                continue
            schema_names = set(pf.schema_arrow.names)
            col = next((c for c in _DATE_COLS if c in schema_names), None)
            if col is None:
                continue
            col_data = pf.read(columns=[col]).column(0)
        except Exception:
            # corrupt footer / unreadable body — skip, keep the run alive
            continue
        # pc.max avoids materializing the full Python list; returns None on
        # empty/all-null (we then leave `last` unchanged for this file).
        try:
            m = pc.max(col_data)
        except Exception:
            continue
        if m is None:
            continue
        s = str(m.as_py())
        if len(s) == 8 and s.isdigit():
            if last is None or s > last:
                last = s
    return total_rows, last, file_count


def audit_table(api_name: str, data_root: str, latest_trade_date: str) -> TableReport:
    """Audit one tushare table on disk."""
    table_dir = _table_dir(api_name, data_root)
    if not table_dir.exists():
        return TableReport(api_name, CoverageStatus.NOT_DOWNLOADED)

    total_rows, last, file_count = _scan_table(table_dir)
    if file_count == 0:
        return TableReport(api_name, CoverageStatus.EMPTY)
    if total_rows == 0:
        return TableReport(api_name, CoverageStatus.EMPTY, rows=0,
                           last_date=last, partition_files=file_count)

    stale = False
    if last is not None:
        try:
            # loose proxy: >10 calendar days behind latest trade date
            stale = (_parse_date(latest_trade_date) - _parse_date(last)).days > 10
        except ValueError:
            stale = False

    status = CoverageStatus.STALE if stale else CoverageStatus.OK
    return TableReport(api_name, status, rows=total_rows, last_date=last,
                       partition_files=file_count)


# Tables the factor zoo depends on (spec §5 deps + §6 critical gaps).
FACTOR_CRITICAL_TABLES = [
    "daily", "daily_basic", "adj_factor", "moneyflow", "moneyflow_hsgt",
    "margin", "margin_detail", "hsgt_top10", "hk_hold", "cyq_perf", "cyq_chips",
    "income", "balancesheet", "cashflow", "fina_indicator", "forecast",
    "express", "dividend", "stk_holdertrade", "pledge_stat", "share_float",
    "block_trade", "limit_list_d", "kpl_list", "index_daily", "index_weight",
    "index_member_all", "index_dailybasic", "trade_cal", "bak_daily",
    "stk_auction_o", "disclosure_date", "top10_holders", "repurchase",
    "stk_rewards", "fina_mainbz", "fina_audit", "namechange", "stock_basic",
]


def audit_all(data_root: str, latest_trade_date: str) -> list[TableReport]:
    return [audit_table(t, data_root, latest_trade_date) for t in FACTOR_CRITICAL_TABLES]


def _resolve_latest_trade_date(data_root: str) -> str:
    """Use trade_cal max is_open=1 date <= today; fall back to today."""
    cal = _table_dir("trade_cal", data_root)
    if cal.exists():
        last: Optional[str] = None
        for f in _iter_parquet_files(cal):
            try:
                df = pq.ParquetFile(f).read(columns=["cal_date", "is_open"]).to_pandas()
            except Exception:
                continue
            if "cal_date" in df.columns and "is_open" in df.columns:
                today = dt.date.today().strftime("%Y%m%d")
                open_dates = df.loc[
                    (df["is_open"].astype(str) == "1") & (df["cal_date"].astype(str) <= today),
                    "cal_date",
                ].astype(str)
                if not open_dates.empty:
                    m = open_dates.max()
                    last = m if (last is None or m > last) else last
        if last:
            return last
    return dt.date.today().strftime("%Y%m%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit tushare on-disk coverage")
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--latest-trade-date", default=None)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    latest = args.latest_trade_date or _resolve_latest_trade_date(args.data_root)
    reports = audit_all(args.data_root, latest)
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2))
    else:
        print(f"latest_trade_date={latest}")
        print(f"{'table':<20} {'status':<16} {'rows':>12} {'last_date':<10} files")
        for r in reports:
            print(f"{r.api_name:<20} {r.status.value:<16} {r.rows:>12} "
                  f"{(r.last_date or '-'):<10} {r.partition_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
