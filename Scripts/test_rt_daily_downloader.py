#!/usr/bin/env python3
"""
Test script for rt_daily_downloader.

Usage:
    python test_rt_daily_downloader.py --token YOUR_TOKEN
"""
import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

TUSHARE_MODULE_DIR = Path(__file__).resolve().parents[1] / 'data-source' / 'tushare'
if str(TUSHARE_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(TUSHARE_MODULE_DIR))

from rt_daily_downloader import TushareRtDailyClient


def main():
    parser = argparse.ArgumentParser(description='Test Tushare rt_etf_k and rt_k APIs')
    parser.add_argument('--token', required=True, help='Tushare API token')
    parser.add_argument('--etf', nargs='+', default=['510300.SH', '510500.SH'], help='ETF codes to test')
    parser.add_argument('--stock', nargs='+', default=['000001.SZ', '600000.SH'], help='Stock codes to test')
    args = parser.parse_args()

    client = TushareRtDailyClient(token=args.token)

    print("\n" + "="*80)
    print("Testing ETF codes (rt_etf_k API)")
    print("="*80)
    etf_quotes = client.fetch_quotes(args.etf)
    if not etf_quotes.empty:
        print("\nETF Data Preview:")
        print(etf_quotes.to_string())
    else:
        print("\n⚠️  No ETF data received")

    print("\n" + "="*80)
    print("Testing Stock codes (rt_k API)")
    print("="*80)
    stock_quotes = client.fetch_quotes(args.stock)
    if not stock_quotes.empty:
        print("\nStock Data Preview:")
        print(stock_quotes.to_string())
    else:
        print("\n⚠️  No stock data received")

    print("\n" + "="*80)
    print("Testing Mixed codes")
    print("="*80)
    mixed_codes = args.etf[:1] + args.stock[:1]
    mixed_quotes = client.fetch_quotes(mixed_codes)
    if not mixed_quotes.empty:
        print("\nMixed Data Preview:")
        print(mixed_quotes.to_string())
    else:
        print("\n⚠️  No mixed data received")

    return 0


if __name__ == '__main__':
    sys.exit(main())
