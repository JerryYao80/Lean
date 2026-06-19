#!/usr/bin/env python3
"""
Convert tushare_data to LEAN format:
  1. Extend daily price data back to 2016-01-02 (pre-start history for BL covariance)
  2. Generate LEAN factor_files from tushare adj_factor
  3. Generate LEAN map_files from tushare namechange + stock_basic

Usage:
  python3 data-source/tushare/convert_to_lean.py [--tushare-data DIR] [--lean-data DIR] [--start-date YYYYMMDD]

Defaults:
  --tushare-data  /home/project/tushare-downloader/tushare_data
  --lean-data     ./Data
  --start-date    20160102
"""

import argparse
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

TUSHARE_DATA = "/home/project/tushare-downloader/tushare_data"
LEAN_DATA = "./Data"
START_DATE = "20160102"


def ts_code_to_lean(ts_code):
    """Convert tushare ts_code (600519.SH) to (ticker, market) tuple."""
    parts = ts_code.split(".")
    if len(parts) != 2:
        return None, None
    ticker = parts[0]
    exchange = parts[1].upper()
    if exchange == "SH":
        return ticker, "sse"
    elif exchange == "SZ":
        return ticker, "szse"
    return None, None


def load_daily(tushare_data, ts_code):
    """Load daily data from tushare parquet."""
    path = os.path.join(tushare_data, "daily", f"ts_code={ts_code}", "data.parquet")
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def load_adj_factor(tushare_data, ts_code):
    """Load adj_factor data from tushare parquet."""
    path = os.path.join(tushare_data, "adj_factor", f"ts_code={ts_code}", "data.parquet")
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def write_lean_daily(df_daily, lean_data, ticker, market, start_date):
    """Append pre-start daily data to LEAN daily CSV files."""
    out_dir = os.path.join(lean_data, "equity", market, "daily")
    out_file = os.path.join(out_dir, f"{ticker}.csv")

    # Filter to start_date and before what's already in the file
    df_filtered = df_daily[df_daily["trade_date"] >= start_date].copy()
    if df_filtered.empty:
        return 0

    # Convert tushare prices (yuan) to LEAN format (cent-thousandths = price * 10000)
    # e.g., tushare close=700.50 -> LEAN 7005000
    SCALE = 10000

    rows = []
    for _, row in df_filtered.iterrows():
        # Format: YYYYMMDD HH:MM,Open,High,Low,Close,Volume
        trade_date = row["trade_date"]
        open_p = int(round(row["open"] * SCALE))
        high_p = int(round(row["high"] * SCALE))
        low_p = int(round(row["low"] * SCALE))
        close_p = int(round(row["close"] * SCALE))
        vol = int(round(row["vol"] * 100))  # 手 -> shares
        rows.append(f"{trade_date} 00:00,{open_p},{high_p},{low_p},{close_p},{vol}")

    if not rows:
        return 0

    # Check if file already exists
    if os.path.exists(out_file):
        # Read existing file to find what dates are already covered
        with open(out_file, "r") as f:
            existing_lines = f.readlines()

        existing_dates = set()
        for line in existing_lines[1:]:  # skip header
            if line.strip():
                date_str = line.split(",")[0].strip()[:8]  # YYYYMMDD
                existing_dates.add(date_str)

        # Only add rows for dates not already in the file
        new_rows = []
        for line in rows:
            date_str = line.split(",")[0][:8]
            if date_str not in existing_dates:
                new_rows.append(line)

        if not new_rows:
            return 0

        # Merge and sort all rows by date
        all_data_lines = []
        for line in existing_lines[1:]:
            if line.strip():
                all_data_lines.append(line.strip())
        for line in new_rows:
            all_data_lines.append(line)
        all_data_lines.sort(key=lambda x: x[:8])  # sort by YYYYMMDD

        header = "Date,Open,High,Low,Close,Volume\n"
        with open(out_file, "w") as f:
            f.write(header)
            for line in all_data_lines:
                f.write(line + "\n")

        return len(new_rows)
    else:
        # Create new file with header
        os.makedirs(out_dir, exist_ok=True)
        header = "Date,Open,High,Low,Close,Volume\n"
        with open(out_file, "w") as f:
            f.write(header)
            for line in rows:
                f.write(line + "\n")
        return len(rows)


def compute_factor_file(df_adj, df_daily):
    """Compute LEAN factor_file from tushare adj_factor.

    LEAN factor_file format: date,priceFactor,splitFactor,referencePrice
    - priceFactor: cumulative dividend adjustment
    - splitFactor: cumulative split adjustment
    - referencePrice: raw close price before the corporate event

    tushare adj_factor is a cumulative factor where:
    adjusted_close = raw_close * adj_factor
    So adj_factor incorporates both dividends and splits.

    We decompose by comparing daily change ratio:
    - If change ≈ 1.0: no event (skip)
    - Otherwise: a corporate event occurred between prev and current trade_date

    For simplicity, we put the entire adj_factor as priceFactor and set splitFactor=1.
    This is equivalent to "total adjustment" mode and is correct for LEAN's
    price scaling: PriceScaleFactor = priceFactor * splitFactor.
    """
    if df_adj is None or df_adj.empty:
        return None

    # We need daily close prices for referencePrice
    close_by_date = {}
    if df_daily is not None and not df_daily.empty:
        for _, row in df_daily.iterrows():
            close_by_date[row["trade_date"]] = row["close"]

    # Find rows where adj_factor changes (corporate events)
    events = []
    prev_factor = None
    for _, row in df_adj.iterrows():
        current_factor = row["adj_factor"]
        if prev_factor is not None:
            ratio = current_factor / prev_factor
            # Significant change = corporate event
            if abs(ratio - 1.0) > 0.0001:
                trade_date = row["trade_date"]
                ref_price = close_by_date.get(trade_date, 0)
                events.append({
                    "date": trade_date,
                    "adj_factor": current_factor,
                    "referencePrice": ref_price
                })
        prev_factor = current_factor

    if not events:
        # No corporate events - minimal factor file
        first_date = df_adj.iloc[0]["trade_date"]
        return [
            f"{first_date},1,1,0",
            "20501231,1,1,0"
        ]

    # Build factor rows
    # LEAN expects cumulative factors from the LAST row backward.
    # The last event should have factors approaching 1.0.
    # adj_factor in tushare is cumulative from IPO forward.
    # To convert: factor_row = adj_factor_event / adj_factor_latest
    latest_factor = df_adj.iloc[-1]["adj_factor"]

    lines = []
    # First row: the earliest date with baseline factors
    # The first event's priceFactor = adj_factor / latest_factor, splitFactor = 1
    for i, evt in enumerate(events):
        price_factor = round(evt["adj_factor"] / latest_factor, 7)
        split_factor = 1.0
        ref_price = round(evt["referencePrice"], 4)
        lines.append(f"{evt['date']},{price_factor},{split_factor:.8f},{ref_price}")

    # Sentinel row
    lines.append("20501231,1,1,0")

    return lines


def write_factor_file(lines, lean_data, ticker, market):
    """Write LEAN factor_file."""
    out_dir = os.path.join(lean_data, "equity", market, "factor_files")
    out_file = os.path.join(out_dir, f"{ticker}.csv")
    os.makedirs(out_dir, exist_ok=True)

    with open(out_file, "w") as f:
        for line in lines:
            f.write(line + "\n")

    return out_file


def compute_map_file(ts_code, namechange_df, stock_basic_df):
    """Compute LEAN map_file from tushare namechange + stock_basic.

    LEAN map_file format: date,mappedSymbol,exchange,market
    For A-shares: date,permtick,permtick,market (e.g., 19980101,600519,600519,sse)
    """
    ticker, market = ts_code_to_lean(ts_code)
    if ticker is None:
        return None

    lines = []

    # Get the stock's listing date from stock_basic
    first_date = "19980101"  # default fallback
    if stock_basic_df is not None and not stock_basic_df.empty:
        match = stock_basic_df[stock_basic_df["ts_code"] == ts_code]
        if not match.empty:
            list_date = match.iloc[0].get("list_date")
            if pd.notna(list_date):
                first_date = str(int(list_date))

    # Check for name changes (deduplicate by start_date)
    if namechange_df is not None and not namechange_df.empty:
        changes = namechange_df[namechange_df["ts_code"] == ts_code].sort_values("start_date")
        if not changes.empty:
            lines.append(f"{first_date},{ticker},{ticker},{market}")
            seen_dates = set()
            for _, row in changes.iterrows():
                start_date = str(int(row["start_date"])) if pd.notna(row["start_date"]) else None
                if start_date and start_date > first_date and start_date not in seen_dates:
                    lines.append(f"{start_date},{ticker},{ticker},{market}")
                    seen_dates.add(start_date)
            lines.append(f"20501231,{ticker},{ticker},{market}")
            return lines

    # No name changes - simple two-line file
    lines.append(f"{first_date},{ticker},{ticker},{market}")
    lines.append(f"20501231,{ticker},{ticker},{market}")
    return lines


def write_map_file(lines, lean_data, ticker, market):
    """Write LEAN map_file."""
    out_dir = os.path.join(lean_data, "equity", market, "map_files")
    out_file = os.path.join(out_dir, f"{ticker}.csv")
    os.makedirs(out_dir, exist_ok=True)

    with open(out_file, "w") as f:
        for line in lines:
            f.write(line + "\n")

    return out_file


def process_stock(args):
    """Process a single stock: daily extension + factor_file + map_file."""
    ts_code, tushare_data, lean_data, start_date, namechange_df, stock_basic_df = args

    ticker, market = ts_code_to_lean(ts_code)
    if ticker is None:
        return {"daily": 0, "factor": False, "map": False, "ts_code": ts_code}

    # Load data
    df_daily = load_daily(tushare_data, ts_code)
    df_adj = load_adj_factor(tushare_data, ts_code)

    # 1. Extend daily data
    daily_count = 0
    if df_daily is not None:
        daily_count = write_lean_daily(df_daily, lean_data, ticker, market, start_date)

    # 2. Generate factor_file
    factor_ok = False
    if df_adj is not None:
        factor_lines = compute_factor_file(df_adj, df_daily)
        if factor_lines:
            write_factor_file(factor_lines, lean_data, ticker, market)
            factor_ok = True

    # 3. Generate map_file
    map_ok = False
    map_lines = compute_map_file(ts_code, namechange_df, stock_basic_df)
    if map_lines:
        write_map_file(map_lines, lean_data, ticker, market)
        map_ok = True

    return {"daily": daily_count, "factor": factor_ok, "map": map_ok, "ts_code": ts_code}


def get_stock_list(tushare_data):
    """Get list of all stock ts_codes from tushare_data/daily/."""
    daily_dir = os.path.join(tushare_data, "daily")
    if not os.path.isdir(daily_dir):
        print(f"ERROR: {daily_dir} not found")
        return []

    ts_codes = []
    for entry in os.listdir(daily_dir):
        if entry.startswith("ts_code="):
            ts_code = entry.split("=", 1)[1]
            # Only process SSE (6xxxxx.SH) and SZSE (0xxxxx.SZ, 3xxxxx.SZ)
            if ts_code.endswith(".SH") or ts_code.endswith(".SZ"):
                # Skip ETFs and funds (51xxxx, 15xxxx, 51xxxx SH, etc.)
                ticker = ts_code.split(".")[0]
                if ticker.startswith(("6", "0", "3")) and not ticker.startswith(("51", "15", "56", "58", "52", "55")):
                    ts_codes.append(ts_code)

    return sorted(ts_codes)


def main():
    parser = argparse.ArgumentParser(description="Convert tushare_data to LEAN format")
    parser.add_argument("--tushare-data", default=TUSHARE_DATA, help="Path to tushare_data directory")
    parser.add_argument("--lean-data", default=LEAN_DATA, help="Path to LEAN Data directory")
    parser.add_argument("--start-date", default=START_DATE, help="Start date for daily data (YYYYMMDD)")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be done")
    parser.add_argument("--tickers", nargs="*", help="Process only these tickers (e.g., 600519.SH)")
    args = parser.parse_args()

    tushare_data = args.tushare_data
    lean_data = args.lean_data
    start_date = args.start_date

    print(f"Tushare data: {tushare_data}")
    print(f"LEAN data:    {lean_data}")
    print(f"Start date:   {start_date}")
    print()

    # Load namechange and stock_basic
    namechange_df = None
    namechange_path = os.path.join(tushare_data, "namechange", "data.parquet")
    if os.path.exists(namechange_path):
        namechange_df = pd.read_parquet(namechange_path)
        print(f"Loaded namechange: {len(namechange_df)} rows")
    else:
        print(f"WARNING: namechange data not found at {namechange_path}")

    stock_basic_df = None
    stock_basic_path = os.path.join(tushare_data, "stock_basic", "data.parquet")
    if os.path.exists(stock_basic_path):
        stock_basic_df = pd.read_parquet(stock_basic_path)
        print(f"Loaded stock_basic: {len(stock_basic_df)} rows")
    else:
        print(f"WARNING: stock_basic data not found at {stock_basic_path}")

    # Get stock list
    if args.tickers:
        ts_codes = args.tickers
    else:
        ts_codes = get_stock_list(tushare_data)

    print(f"Stocks to process: {len(ts_codes)}")

    if args.dry_run:
        print("\nDRY RUN - no files will be written")
        for ts_code in ts_codes[:5]:
            ticker, market = ts_code_to_lean(ts_code)
            print(f"  {ts_code} -> {ticker} ({market})")
        if len(ts_codes) > 5:
            print(f"  ... and {len(ts_codes) - 5} more")
        return

    # Process stocks
    total_daily = 0
    total_factor = 0
    total_map = 0
    errors = 0

    task_args = [
        (ts_code, tushare_data, lean_data, start_date, namechange_df, stock_basic_df)
        for ts_code in ts_codes
    ]

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_stock, arg): arg[0] for arg in task_args}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
                total_daily += result["daily"]
                if result["factor"]:
                    total_factor += 1
                if result["map"]:
                    total_map += 1
                if i % 500 == 0 or i == len(ts_codes):
                    print(f"  Progress: {i}/{len(ts_codes)} stocks processed")
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  ERROR processing {futures[future]}: {e}")

    print()
    print("=" * 60)
    print(f"Conversion complete!")
    print(f"  Daily rows added (pre-start): {total_daily}")
    print(f"  Factor files generated:        {total_factor}")
    print(f"  Map files generated:           {total_map}")
    print(f"  Errors:                        {errors}")


if __name__ == "__main__":
    main()
