#!/usr/bin/env python3
"""
Prepare and test Tushare data for A-Share ETF T+0 trading system
"""

import pandas as pd
import os
from pathlib import Path

# Tushare data path
TUSHARE_DATA_PATH = "/home/project/tushare-downloader/tushare_data_v2"

def load_etf_metadata():
    """Load ETF basic metadata"""
    etf_basic_path = os.path.join(TUSHARE_DATA_PATH, "etf_basic", "data.parquet")

    if not os.path.exists(etf_basic_path):
        print(f"Error: ETF basic data not found at {etf_basic_path}")
        return None

    df = pd.read_parquet(etf_basic_path)
    print(f"Loaded {len(df)} ETFs from metadata")
    print(f"\nColumns: {df.columns.tolist()}")
    return df

def identify_t0_etfs(df):
    """Identify T+0 tradable ETFs"""
    if df is None:
        return []

    t0_etfs = []

    # QDII ETFs
    qdii = df[df['etf_type'].str.contains('QDII', na=False)]
    t0_etfs.extend(qdii['ts_code'].tolist())
    print(f"\nQDII ETFs (T+0): {len(qdii)}")

    # Gold ETFs
    gold = df[df['csname'].str.contains('黄金|黃金', na=False)]
    t0_etfs.extend(gold['ts_code'].tolist())
    print(f"Gold ETFs (T+0): {len(gold)}")

    # Money Market ETFs
    money = df[df['csname'].str.contains('货币|貨幣', na=False)]
    t0_etfs.extend(money['ts_code'].tolist())
    print(f"Money Market ETFs (T+0): {len(money)}")

    # Bond ETFs (excluding convertible bonds)
    bond = df[df['csname'].str.contains('债|債', na=False) &
              ~df['csname'].str.contains('可转债|可轉債', na=False)]
    t0_etfs.extend(bond['ts_code'].tolist())
    print(f"Bond ETFs (T+0): {len(bond)}")

    # Remove duplicates
    t0_etfs = list(set(t0_etfs))
    print(f"\nTotal T+0 ETFs: {len(t0_etfs)}")

    return t0_etfs

def check_daily_data_availability(ts_codes, sample_size=5):
    """Check if daily data is available for sample ETFs"""
    daily_path = os.path.join(TUSHARE_DATA_PATH, "fund_daily")

    print(f"\n\nChecking daily data availability for {sample_size} sample ETFs...")

    available = []
    for ts_code in ts_codes[:sample_size]:
        data_path = os.path.join(daily_path, f"ts_code={ts_code}", "data.parquet")

        if os.path.exists(data_path):
            df = pd.read_parquet(data_path)
            print(f"\n{ts_code}: {len(df)} trading days")
            print(f"  Date range: {df['trade_date'].min()} to {df['trade_date'].max()}")
            print(f"  Latest close: {df.iloc[0]['close']}")
            available.append(ts_code)
        else:
            print(f"\n{ts_code}: Data file not found")

    return available

def generate_symbol_list(t0_etfs, output_file="t0_etf_symbols.txt"):
    """Generate a text file with T+0 ETF symbols"""
    output_path = os.path.join("/home/project/hope/Lean/Data", output_file)

    # Create Data directory if it doesn't exist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        for ts_code in sorted(t0_etfs):
            f.write(f"{ts_code}\n")

    print(f"\n\nGenerated symbol list: {output_path}")
    print(f"Total symbols: {len(t0_etfs)}")

def main():
    print("=" * 80)
    print("A-Share ETF T+0 Trading System - Data Preparation")
    print("=" * 80)

    # Load ETF metadata
    etf_df = load_etf_metadata()

    if etf_df is None:
        return

    # Show sample ETFs
    print("\n\nSample ETFs:")
    print(etf_df[['ts_code', 'csname', 'etf_type', 'exchange']].head(10))

    # Identify T+0 ETFs
    t0_etfs = identify_t0_etfs(etf_df)

    if not t0_etfs:
        print("\nNo T+0 ETFs found!")
        return

    # Show sample T+0 ETFs
    print("\n\nSample T+0 ETFs:")
    t0_df = etf_df[etf_df['ts_code'].isin(t0_etfs[:10])]
    print(t0_df[['ts_code', 'csname', 'etf_type', 'exchange']])

    # Check daily data availability
    available_etfs = check_daily_data_availability(t0_etfs, sample_size=5)

    # Generate symbol list
    generate_symbol_list(t0_etfs)

    print("\n" + "=" * 80)
    print("Data preparation complete!")
    print("=" * 80)
    print(f"\nSummary:")
    print(f"  Total ETFs: {len(etf_df)}")
    print(f"  T+0 ETFs: {len(t0_etfs)}")
    print(f"  Sample ETFs with data: {len(available_etfs)}")
    print(f"\nNext steps:")
    print(f"  1. Convert parquet data to LEAN csv+zip: python3 Scripts/convert-tushare-to-lean.py")
    print(f"  2. Build the LEAN solution: /usr/local/dotnet/dotnet build QuantConnect.Lean.sln")
    print(f"  3. Run backtest: /usr/local/dotnet/dotnet run --project Launcher --config config/config-ashare-etf-backtest.json")
    print(f"  4. Run live-paper: /usr/local/dotnet/dotnet run --project Launcher --config config/config-ashare-etf-live-paper.json")

if __name__ == "__main__":
    main()
