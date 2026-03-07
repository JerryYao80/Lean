#!/usr/bin/env python3
"""
Convert Tushare Parquet data to LEAN format with price scaling
"""

import pandas as pd
import os
import zipfile
from datetime import datetime

# Tushare data path
TUSHARE_DATA_PATH = "/home/project/tushare-downloader/tushare_data"
LEAN_DATA_PATH = "/home/project/hope/Lean/Data"

# T+0 ETF list (from AShareETFMetadata in C#)
T0_ETFS = [
    "510050.SH",  # 50ETF
    "510300.SH",  # 300ETF
    "510500.SH",  # 500ETF
    "518880.SH",  # 黄金ETF
    "511880.SH",  # 银华日利
    "511990.SH",  # 华宝添益
    "159915.SZ",  # 创业板ETF
    "159919.SZ",  # 300ETF
    "159949.SZ",  # 创业板50
]

def convert_ts_code_to_lean_format(ts_code):
    """Convert Tushare ts_code to LEAN format

    Args:
        ts_code: e.g., "510050.SH"

    Returns:
        tuple: (ticker, market) e.g., ("510050", "sse")
    """
    ticker, exchange = ts_code.split('.')
    market = "sse" if exchange == "SH" else "szse"
    return ticker, market

def convert_trade_date(trade_date_str):
    """Convert Tushare trade_date (YYYYMMDD) to datetime"""
    return datetime.strptime(trade_date_str, "%Y%m%d")

def scale_price(value):
    return int(round(float(value) * 10000))


def validate_zip_output(zip_path, ticker, expected_rows):
    entry_name = f"{ticker.lower()}.csv"

    with zipfile.ZipFile(zip_path, "r") as archive:
        with archive.open(entry_name) as handle:
            rows = handle.read().decode("utf-8").strip().splitlines()

    if rows != expected_rows:
        raise ValueError(f"Zip validation failed for {zip_path}: generated rows do not match expected rows")

    first_fields = rows[0].split(",")
    if any("." in field for field in first_fields[1:5]):
        raise ValueError(f"Zip validation failed for {zip_path}: equity prices must be scaled integers, got {rows[0]}")


def write_lean_daily_file(ts_code, start_date="20240101", end_date="20241231"):
    """Convert Tushare daily data to LEAN format

    LEAN daily format (CSV):
    Date,Open,High,Low,Close,Volume
    20240102 00:00,23100,23300,23050,23240,1432459500

    Note: Prices are scaled by 10000 (price_magnifier)
    """
    ticker, market = convert_ts_code_to_lean_format(ts_code)

    # Read Tushare parquet data
    parquet_path = os.path.join(TUSHARE_DATA_PATH, "fund_daily", f"ts_code={ts_code}", "data.parquet")

    if not os.path.exists(parquet_path):
        print(f"Warning: Data file not found for {ts_code}")
        return False

    df = pd.read_parquet(parquet_path)

    # Filter by date range
    df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
    df = df.sort_values('trade_date')

    if len(df) == 0:
        print(f"Warning: No data in date range for {ts_code}")
        return False

    # Create LEAN data directory structure
    # Data/equity/{market}/daily/{ticker}.zip (contains {ticker}.csv)
    lean_dir = os.path.join(LEAN_DATA_PATH, "equity", market, "daily")
    os.makedirs(lean_dir, exist_ok=True)

    csv_lines = ["Date,Open,High,Low,Close,Volume"]
    zip_lines = []

    for _, row in df.iterrows():
        date = convert_trade_date(row['trade_date'])
        date_str = date.strftime("%Y%m%d 00:00")

        # Scale prices by 10000 (price_magnifier)
        open_price = scale_price(row['open'])
        high_price = scale_price(row['high'])
        low_price = scale_price(row['low'])
        close_price = scale_price(row['close'])

        # Convert volume from lots (手) to shares
        volume = int(row['vol'] * 100)

        formatted_row = f"{date_str},{open_price},{high_price},{low_price},{close_price},{volume}"
        csv_lines.append(formatted_row)
        zip_lines.append(formatted_row)

    # Write CSV file
    csv_path = os.path.join(lean_dir, f"{ticker}.csv")
    with open(csv_path, 'w') as f:
        f.write('\n'.join(csv_lines))

    zip_path = os.path.join(lean_dir, f"{ticker}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{ticker.lower()}.csv", '\n'.join(zip_lines))

    validate_zip_output(zip_path, ticker, zip_lines)

    print(f"Converted {ts_code}: {len(df)} bars -> {csv_path}, {zip_path}")
    return True

def main():
    print("=" * 80)
    print("Converting Tushare data to LEAN format")
    print("=" * 80)

    success_count = 0
    for ts_code in T0_ETFS:
        if write_lean_daily_file(ts_code):
            success_count += 1

    print("\n" + "=" * 80)
    print(f"Conversion complete: {success_count}/{len(T0_ETFS)} ETFs")
    print("=" * 80)

    if success_count > 0:
        print("\nData files created in:")
        print(f"  {LEAN_DATA_PATH}/equity/sse/daily/")
        print(f"  {LEAN_DATA_PATH}/equity/szse/daily/")
        print("\nZip validation completed successfully for all converted ETFs.")
        print("\nYou can now run the backtest:")
        print("  cd /home/project/hope/Lean/Launcher/bin/Debug")
        print("  /usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-ashare-etf-backtest.json")

if __name__ == "__main__":
    main()
