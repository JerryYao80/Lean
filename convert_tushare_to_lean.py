#!/usr/bin/env python3
"""
Convert tushare ETF data to LEAN format
"""
import pandas as pd
import os
import json
from pathlib import Path
from datetime import datetime

# Load T+0 ETF list
with open('/home/project/hope/Lean/t0_etf_list.json', 'r', encoding='utf-8') as f:
    t0_etfs = json.load(f)

print(f"Converting {len(t0_etfs)} T+0 ETFs to LEAN format...")

# LEAN data directory structure: Data/equity/{market}/daily/{ticker}.zip
lean_data_dir = Path('/home/project/hope/Lean/Data/equity')
lean_data_dir.mkdir(parents=True, exist_ok=True)

converted_count = 0
missing_count = 0

for etf in t0_etfs:
    ticker = etf['ticker']
    ts_code = etf['ts_code']
    market = etf['market'].lower()  # sse or szse

    # Check if tushare data exists
    tushare_path = f'/home/project/tushare-downloader/tushare_data/fund_daily/date={ts_code}/data.parquet'

    if not os.path.exists(tushare_path):
        missing_count += 1
        continue

    try:
        # Read tushare data
        df = pd.read_parquet(tushare_path)

        if df.empty:
            continue

        # Convert to LEAN format
        # LEAN daily format: Date,Open,High,Low,Close,Volume
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        df = df.sort_values('trade_date')

        # LEAN expects: YYYYMMDD HH:MM,Open,High,Low,Close,Volume
        lean_df = pd.DataFrame({
            'Date': df['trade_date'].dt.strftime('%Y%m%d 00:00'),
            'Open': df['open'],
            'High': df['high'],
            'Low': df['low'],
            'Close': df['close'],
            'Volume': df['vol'] * 100  # Convert from 万手 to shares (1手=100股)
        })

        # Create market directory
        market_dir = lean_data_dir / market / 'daily'
        market_dir.mkdir(parents=True, exist_ok=True)

        # Save as CSV (LEAN can read CSV directly)
        output_file = market_dir / f'{ticker}.csv'
        lean_df.to_csv(output_file, index=False, header=False)

        converted_count += 1
        if converted_count % 20 == 0:
            print(f"  Converted {converted_count}/{len(t0_etfs)} ETFs...")

    except Exception as e:
        print(f"  Error converting {ticker}: {e}")
        continue

print(f"\n✓ Conversion complete!")
print(f"  Converted: {converted_count} ETFs")
print(f"  Missing data: {missing_count} ETFs")
print(f"  Output directory: {lean_data_dir}")
