"""Generate simulated A-share daily price data in LEAN format for backtesting.

Creates synthetic OHLCV CSV files matching LEAN's expected format:
  Data/equity/{market}/daily/{ticker}.csv

Also creates minimal factor_files and map_files so LEAN can load the data.

LEAN daily price CSV format:
  Header: Date,Open,High,Low,Close,Volume
  Date: YYYYMMDD HH:MM (always 00:00 for daily)
  Prices: raw_price * 10000 (integer, no decimals)
  Volume: shares (integer)

Usage:
  python3 Scripts/generate_simulated_ashare_prices.py --output Data --stocks 50 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path


# Representative CSI300 tickers (SSE + SZSE mix) — same as generate_simulated_cne5_factors.py
SSE_TICKERS = [
    "600000", "600009", "600010", "600011", "600015", "600016", "600018",
    "600019", "600023", "600025", "600028", "600029", "600030", "600031",
    "600036", "600048", "600050", "600061", "600066", "600068", "600085",
    "600089", "600090", "600095", "600096", "600100", "600104", "600109",
    "600111", "600115", "600118", "600120", "600150", "600153", "600160",
    "600161", "600166", "600170", "600176", "600177", "600183", "600188",
    "600196", "600199", "600208", "600219", "600221", "600233", "600271",
    "600276", "600309", "600325", "600332", "600340", "600346", "600352",
    "600362", "600369", "600372", "600383", "600390", "600398", "600406",
    "600415", "600426", "600433", "600436", "600438", "600460", "600486",
    "600489", "600496", "600498", "600500", "600507", "600519", "600521",
    "600523", "600547", "600570", "600571", "600588", "600589", "600590",
    "600595", "600597", "600598", "600600", "600606", "600613", "600623",
    "600637", "600655", "600660", "600663", "600690", "600703", "600704",
    "600705", "600710", "600711", "600718", "600732", "600739", "600745",
    "600748", "600760", "600763", "600770", "600779", "600782", "600787",
    "600801", "600809", "600823", "600837", "600845", "600848", "600850",
    "600859", "600862", "600867", "600873", "600875", "600880", "600887",
    "600893", "600895", "600900", "600905", "600918", "600919", "600926",
    "600933", "600934", "600935", "600936", "600937", "600938", "600939",
    "600941", "600958", "600959", "600967", "600970", "600973", "600977",
    "600980", "600985", "600988", "600989", "600990", "600993", "600995",
    "600996", "600998", "600999",
]

SZSE_TICKERS = [
    "000001", "000002", "000063", "000066", "000069", "000100", "000157",
    "000166", "000333", "000338", "000425", "000538", "000568", "000596",
    "000625", "000651", "000661", "000708", "000725", "000768", "000776",
    "000783", "000786", "000800", "000807", "000858", "000876", "000895",
    "000938", "000963", "000977", "001289", "001979", "002001", "002007",
    "002008", "002024", "002027", "002032", "002049", "002050", "002056",
    "002065", "002074", "002081", "002120", "002124", "002127", "002128",
    "002142", "002146", "002153", "002157", "002179", "002180", "002185",
    "002202", "002230", "002236", "002241", "002250", "002252", "002271",
    "002304", "002311", "002318", "002322", "002326", "002329", "002352",
    "002371", "002372", "002375", "002377", "002384", "002390", "002396",
    "002402", "002405", "002410", "002411", "002415", "002422", "002432",
    "002436", "002439", "002456", "002459", "002460", "002466", "002468",
    "002470", "002475", "002493", "002496", "002508", "002511", "002531",
    "002539", "002541", "002555", "002558", "002568", "002594", "002601",
    "002602", "002607", "002625", "002635", "002648", "002670", "002680",
    "002690", "002709", "002714", "002736", "002756", "002773", "002812",
    "002821", "002832", "002841", "002895", "002916", "002926", "002945",
    "002958", "003816",
]

# Approximate listing dates for major tickers (used in map_files)
# Format: ticker -> YYYYMMDD string
LISTING_DATES = {
    "600000": "19991110", "600519": "20010827", "600036": "20020409",
    "600016": "20001219", "600030": "20030102", "600276": "20020612",
    "600887": "19960904", "600104": "19970612", "600309": "20010105",
}


def get_listing_date(ticker: str, rng: random.Random) -> str:
    """Get approximate listing date for a ticker."""
    if ticker in LISTING_DATES:
        return LISTING_DATES[ticker]
    # Generate a plausible listing date between 1995-2018
    year = rng.randint(1995, 2018)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return f"{year}{month:02d}{day:02d}"


def generate_price_csv(
    output_path: str,
    ticker: str,
    start_date: datetime,
    end_date: datetime,
    rng: random.Random,
) -> int:
    """Generate a single daily price CSV file in LEAN format. Returns number of rows written."""
    # Per-stock base price (simulate realistic A-share price range)
    base_price = rng.uniform(5.0, 150.0)
    # Daily volatility (annualized ~25-45%)
    daily_vol = rng.uniform(0.015, 0.028)
    # Autoregressive drift
    drift = rng.uniform(-0.0001, 0.0003)

    price = base_price
    prev_close = price

    # Volume base (in lots of 100 shares)
    volume_base = rng.uniform(5000, 500000)

    rows = 0
    current_date = start_date

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])

        while current_date <= end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # GBM-like price simulation with mean reversion
            shock = rng.gauss(0, daily_vol)
            ret = drift + shock
            # Mean revert slightly toward base_price to avoid extreme drift
            ret += 0.001 * (base_price / prev_close - 1) if prev_close > 0 else 0

            close = prev_close * math.exp(ret)
            if close < 0.5:
                close = 0.5  # Floor price

            # Generate OHLC around close
            intra_vol = abs(rng.gauss(0, daily_vol * 0.5))
            high = max(prev_close, close) * (1 + abs(rng.gauss(0, intra_vol)))
            low = min(prev_close, close) * (1 - abs(rng.gauss(0, intra_vol)))
            if low < 0.1:
                low = 0.1
            open_price = prev_close * (1 + rng.gauss(0, intra_vol * 0.3))

            # Ensure OHLC consistency
            high = max(high, open_price, close)
            low = min(low, open_price, close)

            # Scale prices by 10000 for LEAN format
            SCALE = 10000
            open_scaled = int(round(open_price * SCALE))
            high_scaled = int(round(high * SCALE))
            low_scaled = int(round(low * SCALE))
            close_scaled = int(round(close * SCALE))

            # Volume in shares (multiply by 100 from lots)
            volume = int(round(volume_base * (1 + rng.gauss(0, 0.3)) * 100))
            if volume < 100:
                volume = 100

            writer.writerow([
                f"{current_date.strftime('%Y%m%d')} 00:00",
                open_scaled,
                high_scaled,
                low_scaled,
                close_scaled,
                volume,
            ])

            prev_close = close
            rows += 1
            current_date += timedelta(days=1)

    return rows


def generate_factor_file(output_path: str, ticker: str, listing_date: str) -> None:
    """Generate a minimal factor file (no corporate actions)."""
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        # No header for factor files
        writer.writerow([listing_date, 1.0, 1.0, 0])
        # Sentinel row
        writer.writerow(["20501231", 1, 1, 0])


def generate_map_file(output_path: str, ticker: str, market: str, listing_date: str) -> None:
    """Generate a minimal map file."""
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        # No header for map files
        writer.writerow([listing_date, ticker, ticker, market])
        # Sentinel row
        writer.writerow(["20501231", ticker, ticker, market])


def main():
    parser = argparse.ArgumentParser(description="Generate simulated A-share price data in LEAN format")
    parser.add_argument("--output", default="Data",
                        help="Output directory (default: Data)")
    parser.add_argument("--stocks", type=int, default=50,
                        help="Number of stocks to generate (default: 50)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--start-date", default="2018-01-02",
                        help="Start date (default: 2018-01-02, gives 2yr history before factor data)")
    parser.add_argument("--end-date", default="2025-12-31",
                        help="End date (default: 2025-12-31)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")

    # Select tickers (same selection logic as generate_simulated_cne5_factors.py)
    n_sse = min(args.stocks // 2 + args.stocks % 2, len(SSE_TICKERS))
    n_szse = min(args.stocks - n_sse, len(SZSE_TICKERS))
    sse_selected = rng.sample(SSE_TICKERS, n_sse)
    szse_selected = rng.sample(SZSE_TICKERS, n_szse)

    total_rows = 0
    total_files = 0

    for ticker in sse_selected:
        market = "sse"
        # Daily price file
        daily_dir = Path(args.output) / "equity" / market / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        rows = generate_price_csv(str(daily_dir / f"{ticker}.csv"), ticker, start_date, end_date, rng)
        total_rows += rows
        total_files += 1

        # Factor file
        factor_dir = Path(args.output) / "equity" / market / "factor_files"
        factor_dir.mkdir(parents=True, exist_ok=True)
        listing_date = get_listing_date(ticker, rng)
        generate_factor_file(str(factor_dir / f"{ticker}.csv"), ticker, listing_date)

        # Map file
        map_dir = Path(args.output) / "equity" / market / "map_files"
        map_dir.mkdir(parents=True, exist_ok=True)
        generate_map_file(str(map_dir / f"{ticker}.csv"), ticker, market, listing_date)

    for ticker in szse_selected:
        market = "szse"
        daily_dir = Path(args.output) / "equity" / market / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        rows = generate_price_csv(str(daily_dir / f"{ticker}.csv"), ticker, start_date, end_date, rng)
        total_rows += rows
        total_files += 1

        factor_dir = Path(args.output) / "equity" / market / "factor_files"
        factor_dir.mkdir(parents=True, exist_ok=True)
        listing_date = get_listing_date(ticker, rng)
        generate_factor_file(str(factor_dir / f"{ticker}.csv"), ticker, listing_date)

        map_dir = Path(args.output) / "equity" / market / "map_files"
        map_dir.mkdir(parents=True, exist_ok=True)
        generate_map_file(str(map_dir / f"{ticker}.csv"), ticker, market, listing_date)

    print(f"Generated {total_files} stock price CSV files with {total_rows} total daily bars")
    print(f"  SSE: {n_sse} stocks → {args.output}/equity/sse/daily/")
    print(f"  SZSE: {n_szse} stocks → {args.output}/equity/szse/daily/")
    print(f"  Date range: {args.start_date} to {args.end_date}")
    print(f"  Also generated factor_files/ and map_files/ for each stock")


if __name__ == "__main__":
    main()
