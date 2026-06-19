"""Generate simulated Barra CNE5 factor CSV files for backtesting without real tushare data.

Creates random factor data for a configurable number of A-share stocks,
organized in the directory structure expected by AShareBarraCNE5V2FactorData:
  alternative/barra-cne5v2-factors/{market}/daily/{ticker}.csv

Each CSV has 21 columns matching the real factor data format:
  date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,
  nlsize,moneyflow,quality,northbound,margin,chipcost,TotalMv,TurnoverRate,
  ListedDays,MissingFactorCount,IsSt

Usage:
  python3 Scripts/generate_simulated_cne5_factors.py --output Data/alternative/barra-cne5v2-factors --stocks 50 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import os
import random
from datetime import datetime, timedelta
from pathlib import Path


# Representative CSI300 tickers (SSE + SZSE mix)
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

FACTOR_NAMES = [
    "beta", "momentum", "size", "earnyld", "resvol", "growth", "btop",
    "leverage", "liquidity", "nlsize", "moneyflow", "quality", "northbound",
    "margin", "chipcost",
]


def generate_factor_csv(
    output_path: str,
    ticker: str,
    start_date: datetime,
    end_date: datetime,
    rng: random.Random,
) -> int:
    """Generate a single factor CSV file. Returns number of rows written."""
    # Per-stock factor means and stds (simulating cross-sectional variation)
    factor_means = {f: rng.gauss(0, 0.3) for f in FACTOR_NAMES}
    factor_stds = {f: max(0.1, rng.gauss(0.5, 0.2)) for f in FACTOR_NAMES}

    # Autoregressive coefficient for smooth time series
    ar_coeff = 0.95
    prev_values = {f: factor_means[f] for f in FACTOR_NAMES}

    total_mv_base = rng.uniform(5e6, 5e7)  # in 10k CNY
    turnover_base = rng.uniform(0.5, 5.0)
    listed_days_start = rng.randint(500, 5000)
    is_st = 0

    rows = 0
    current_date = start_date
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "trade_date", "beta", "momentum", "size", "earnyld", "resvol", "growth",
            "btop", "leverage", "liquidity", "nlsize", "moneyflow", "quality",
            "northbound", "margin", "chipcost", "total_mv", "turnover_rate",
            "listed_days", "missing_factor_count", "is_st",
        ])

        day_count = 0
        while current_date <= end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # AR(1) factor values with mean reversion
            factor_values = {}
            for fn in FACTOR_NAMES:
                noise = rng.gauss(0, factor_stds[fn] * 0.1)
                val = ar_coeff * prev_values[fn] + (1 - ar_coeff) * factor_means[fn] + noise
                factor_values[fn] = round(val, 6)
                prev_values[fn] = val

            total_mv = round(total_mv_base * (1 + rng.gauss(0, 0.02)), 2)
            turnover = round(max(0.01, turnover_base + rng.gauss(0, 0.3)), 4)
            listed_days = listed_days_start + day_count
            missing = rng.choices([0, 0, 0, 0, 1, 1, 2], k=1)[0]

            writer.writerow([
                current_date.strftime("%Y%m%d"),
                factor_values["beta"],
                factor_values["momentum"],
                factor_values["size"],
                factor_values["earnyld"],
                factor_values["resvol"],
                factor_values["growth"],
                factor_values["btop"],
                factor_values["leverage"],
                factor_values["liquidity"],
                factor_values["nlsize"],
                factor_values["moneyflow"],
                factor_values["quality"],
                factor_values["northbound"],
                factor_values["margin"],
                factor_values["chipcost"],
                total_mv,
                turnover,
                listed_days,
                missing,
                is_st,
            ])

            rows += 1
            day_count += 1
            current_date += timedelta(days=1)

    return rows


def main():
    parser = argparse.ArgumentParser(description="Generate simulated Barra CNE5 factor data")
    parser.add_argument("--output", default="Data/alternative/barra-cne5v2-factors",
                        help="Output directory (default: Data/alternative/barra-cne5v2-factors)")
    parser.add_argument("--stocks", type=int, default=50,
                        help="Number of stocks to generate (default: 50)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--start-date", default="2020-01-01",
                        help="Start date (default: 2020-01-01)")
    parser.add_argument("--end-date", default="2025-12-31",
                        help="End date (default: 2025-12-31)")
    parser.add_argument("--sse-tickers", default=None,
                        help="File with SSE ticker list (one per line)")
    parser.add_argument("--szse-tickers", default=None,
                        help="File with SZSE ticker list (one per line)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")

    # Select tickers
    if args.sse_tickers:
        with open(args.sse_tickers) as f:
            available_sse = [l.strip() for l in f if l.strip()]
    else:
        available_sse = SSE_TICKERS

    if args.szse_tickers:
        with open(args.szse_tickers) as f:
            available_szse = [l.strip() for l in f if l.strip()]
    else:
        available_szse = SZSE_TICKERS

    n_sse = min(args.stocks // 2 + args.stocks % 2, len(available_sse))
    n_szse = min(args.stocks - n_sse, len(available_szse))
    sse_selected = rng.sample(available_sse, n_sse)
    szse_selected = rng.sample(available_szse, n_szse)

    total_rows = 0
    total_files = 0

    for ticker in sse_selected:
        out_dir = Path(args.output) / "sse" / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ticker}.csv"
        rows = generate_factor_csv(str(out_path), ticker, start_date, end_date, rng)
        total_rows += rows
        total_files += 1

    for ticker in szse_selected:
        out_dir = Path(args.output) / "szse" / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ticker}.csv"
        rows = generate_factor_csv(str(out_path), ticker, start_date, end_date, rng)
        total_rows += rows
        total_files += 1

    print(f"Generated {total_files} factor CSV files with {total_rows} total rows")
    print(f"  SSE: {n_sse} stocks → {args.output}/sse/daily/")
    print(f"  SZSE: {n_szse} stocks → {args.output}/szse/daily/")
    print(f"  Date range: {args.start_date} to {args.end_date}")


if __name__ == "__main__":
    main()
