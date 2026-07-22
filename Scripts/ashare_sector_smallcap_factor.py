#!/usr/bin/env python3
"""
ashare_sector_smallcap_factor.py

Generates the factor CSV consumed by AShareSectorSmallCapAlgorithm.

Output columns: trade_date, ts_code, sector, total_mv, pb, momentum_20d

Data sources (all from /home/project/tushare-downloader/tushare_data_v2/):
  - daily_basic/   : total_mv, pb  (partitioned by ts_code)
  - daily/         : close prices for momentum  (partitioned by ts_code)
  - index_member/  : stock → Shenwan L1 sector mapping
  - index_classify/: sector code → sector name
  - stock_basic/   : stock metadata (list_date, market)

Usage:
  python Scripts/ashare_sector_smallcap_factor.py \
      --tushare-data /home/project/tushare-downloader/tushare_data_v2 \
      --output local_data/ashare-sector-smallcap-factors.csv \
      --start-date 2019-01-01 \
      --end-date 2025-12-31

The script is idempotent: re-running overwrites the output file.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

MOMENTUM_WINDOW = 20  # trading days


def load_sector_mapping(tushare_root: Path) -> dict[str, str]:
    """Return {ts_code: sector_name} using current Shenwan L1 membership."""
    classify_path = tushare_root / "index_classify" / "data.parquet"
    member_path = tushare_root / "index_member" / "data.parquet"

    classify = pd.read_parquet(classify_path)
    members = pd.read_parquet(member_path)

    # Shenwan L1 codes
    sw_l1 = classify[(classify["src"] == "SW2014") & (classify["level"] == "L1")][
        ["index_code", "industry_name"]
    ]
    sw_codes = set(sw_l1["index_code"].tolist())

    # Current members only
    current = members[members["is_new"] == "Y"]
    current = current[current["index_code"].isin(sw_codes)]
    merged = current.merge(sw_l1, on="index_code", how="left")

    mapping = dict(zip(merged["con_code"], merged["industry_name"]))
    log.info("Sector mapping: %d stocks across %d sectors", len(mapping), merged["industry_name"].nunique())
    return mapping


def load_stock_basic(tushare_root: Path) -> pd.DataFrame:
    """Return stock_basic with ts_code, list_date, market."""
    path = tushare_root / "stock_basic" / "data.parquet"
    df = pd.read_parquet(path, columns=["ts_code", "list_date", "market"])
    return df


def load_daily_basic_for_stock(tushare_root: Path, ts_code: str) -> pd.DataFrame | None:
    """Load daily_basic for a single stock (partitioned by ts_code)."""
    p = tushare_root / "daily_basic" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        # Try alternative partition layout
        p2 = tushare_root / "daily_basic" / "data.parquet"
        if p2.exists():
            df = pd.read_parquet(p2, columns=["ts_code", "trade_date", "total_mv", "pb"])
            df = df[df["ts_code"] == ts_code]
            return df if len(df) > 0 else None
        return None
    try:
        df = pd.read_parquet(p, columns=["ts_code", "trade_date", "total_mv", "pb"])
        return df
    except Exception:
        return None


def load_daily_for_stock(tushare_root: Path, ts_code: str) -> pd.DataFrame | None:
    """Load daily OHLCV for a single stock."""
    p = tushare_root / "daily" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        p2 = tushare_root / "daily" / "data.parquet"
        if p2.exists():
            df = pd.read_parquet(p2, columns=["ts_code", "trade_date", "close"])
            df = df[df["ts_code"] == ts_code]
            return df if len(df) > 0 else None
        return None
    try:
        df = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        return df
    except Exception:
        return None


def compute_momentum(closes: pd.Series, window: int = MOMENTUM_WINDOW) -> pd.Series:
    """Rolling momentum: close / close[window days ago] - 1."""
    return closes.pct_change(periods=window)


def build_factor_table(
    tushare_root: Path,
    sector_mapping: dict[str, str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Build the full factor table for all stocks in sector_mapping."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    # Extend lookback for momentum calculation
    fetch_start = (start_dt - timedelta(days=60)).strftime("%Y%m%d")
    fetch_end = end_dt.strftime("%Y%m%d")
    filter_start = start_dt.strftime("%Y%m%d")

    all_rows: list[pd.DataFrame] = []
    ts_codes = list(sector_mapping.keys())
    log.info("Processing %d stocks...", len(ts_codes))

    for i, ts_code in enumerate(ts_codes):
        if i % 200 == 0:
            log.info("  %d / %d", i, len(ts_codes))

        sector = sector_mapping[ts_code]

        # Load daily_basic (market cap, pb)
        db = load_daily_basic_for_stock(tushare_root, ts_code)
        if db is None or len(db) == 0:
            continue
        db = db[(db["trade_date"] >= fetch_start) & (db["trade_date"] <= fetch_end)].copy()
        if len(db) == 0:
            continue
        db = db.sort_values("trade_date").drop_duplicates("trade_date")

        # Load daily (close prices for momentum)
        dd = load_daily_for_stock(tushare_root, ts_code)
        if dd is not None and len(dd) > 0:
            dd = dd[(dd["trade_date"] >= fetch_start) & (dd["trade_date"] <= fetch_end)].copy()
            dd = dd.sort_values("trade_date").drop_duplicates("trade_date")
            dd["momentum_20d"] = compute_momentum(dd["close"], MOMENTUM_WINDOW)
            db = db.merge(dd[["trade_date", "momentum_20d"]], on="trade_date", how="left")
        else:
            db["momentum_20d"] = np.nan

        db["sector"] = sector
        db = db[db["trade_date"] >= filter_start]
        if len(db) == 0:
            continue

        all_rows.append(db[["trade_date", "ts_code", "sector", "total_mv", "pb", "momentum_20d"]])

    if not all_rows:
        log.warning("No factor data generated!")
        return pd.DataFrame(columns=["trade_date", "ts_code", "sector", "total_mv", "pb", "momentum_20d"])

    result = pd.concat(all_rows, ignore_index=True)
    result = result.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    log.info("Factor table: %d rows, %d stocks, %d dates",
             len(result), result["ts_code"].nunique(), result["trade_date"].nunique())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate A-share sector small-cap factor CSV")
    parser.add_argument("--tushare-data", default="/home/project/tushare-downloader/tushare_data_v2")
    parser.add_argument("--output", default="/home/project/hope/Lean/local_data/ashare-sector-smallcap-factors.csv")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    args = parser.parse_args()

    tushare_root = Path(args.tushare_data)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Loading sector mapping...")
    sector_mapping = load_sector_mapping(tushare_root)

    log.info("Building factor table %s → %s ...", args.start_date, args.end_date)
    df = build_factor_table(tushare_root, sector_mapping, args.start_date, args.end_date)

    if len(df) == 0:
        log.error("Empty factor table, aborting.")
        sys.exit(1)

    df.to_csv(output_path, index=False, float_format="%.4f")
    log.info("Saved factor CSV: %s (%d rows)", output_path, len(df))

    # Print summary stats
    log.info("Date range: %s → %s", df["trade_date"].min(), df["trade_date"].max())
    log.info("Sectors: %s", sorted(df["sector"].unique()))
    log.info("Market cap (万元) p25/p50/p75: %.0f / %.0f / %.0f",
             df["total_mv"].quantile(0.25), df["total_mv"].quantile(0.5), df["total_mv"].quantile(0.75))


if __name__ == "__main__":
    main()
