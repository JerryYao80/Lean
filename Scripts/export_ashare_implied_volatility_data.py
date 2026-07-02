#!/usr/bin/env python3
"""Export A-share implied volatility data to LEAN-format CSV files.

Wraps ashare_implied_volatility.py to produce daily CSV files under
Data/alternative/ashare-implied-volatility/{market}/daily/{ticker}.csv
for consumption by AShareImpliedVolatilityData (BaseData subclass).

CSV format (13 columns):
  trade_date,atm_iv,iv_call_25delta,iv_put_25delta,skew,
  term_days_near,term_days_next,option_count,vix,sigma_near,
  sigma_next,t_near,t_next

- trade_date = yyyyMMdd
- IV values as annualized fractions (0.25 not 25%)
- VIX as percent (25.0)
- Missing values as empty fields
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import ashare_implied_volatility as iv_mod


CSV_HEADER = (
    "trade_date,atm_iv,iv_call_25delta,iv_put_25delta,skew,"
    "iv_skew_surface_minus,skew_near_term,skew_next_term,"
    "term_days_near,term_days_next,option_count,vix,sigma_near,"
    "sigma_next,t_near,t_next"
)

# Map underlying opt_code to ETF ticker used for CSV filename
UNDERLYING_TO_TICKER = {
    "OP510050.SH": "510050",
    "OP510300.SH": "510300",
    "OP510500.SH": "510500",
}


def _fmt(value: float | None, precision: int = 8) -> str:
    if value is None:
        return ""
    return format(value, f".{precision}f")


def merge_row(
    iv: iv_mod.IvResult | None,
    vix: iv_mod.VixResult | None,
) -> str | None:
    """Merge IvResult + VixResult into one CSV row."""
    if iv is None:
        return None
    parts = [
        iv.trade_date,
        _fmt(iv.atm_iv, 8),
        _fmt(iv.iv_call_25delta, 8),
        _fmt(iv.iv_put_25delta, 8),
        _fmt(iv.skew, 8),
        _fmt(iv.iv_skew_surface_minus, 8),
        _fmt(iv.skew_near_term, 8),
        _fmt(iv.skew_next_term, 8),
        str(iv.term_days_near),
        str(iv.term_days_next),
        str(iv.option_count),
    ]
    if vix is not None:
        parts.extend([
            _fmt(vix.vix, 6),
            _fmt(vix.sigma_near, 6),
            _fmt(vix.sigma_next, 6),
            _fmt(vix.t_near, 8),
            _fmt(vix.t_next, 8),
        ])
    else:
        parts.extend(["", "", "", "", ""])
    return ",".join(parts)


def export_underlying(
    tushare_data_path: str,
    underlying_code: str,
    output_dir: Path,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Export IV CSV for one underlying ETF."""
    ticker = UNDERLYING_TO_TICKER.get(underlying_code)
    if ticker is None:
        return {"underlying": underlying_code, "error": "no ticker mapping"}

    fund_ts_code = iv_mod.UNDERLYING_MAP.get(underlying_code)
    if fund_ts_code is None:
        return {"underlying": underlying_code, "error": "no fund mapping"}

    print(f"  Loading opt_basic...")
    opt_basic = iv_mod.load_opt_basic(tushare_data_path)

    print(f"  Loading opt_daily...")
    opt_daily = iv_mod.load_opt_daily(tushare_data_path, start_date, end_date)
    if opt_daily.empty:
        return {"underlying": underlying_code, "error": "no opt_daily data"}

    trade_dates = sorted(opt_daily["trade_date"].unique())
    print(f"  {len(trade_dates)} trade dates ({trade_dates[0]} to {trade_dates[-1]})")

    print(f"  Loading SHIBOR rates...")
    shibor_df = iv_mod.load_shibor(tushare_data_path)

    print(f"  Loading underlying price for {fund_ts_code}...")
    underlying_prices = iv_mod.load_underlying_price(tushare_data_path, fund_ts_code)
    if underlying_prices.empty:
        return {"underlying": underlying_code, "error": "no price data"}

    opt_dates_set = set(trade_dates)
    underlying_prices = underlying_prices[underlying_prices["trade_date"].isin(opt_dates_set)]

    rows: list[str] = []
    computed = 0
    skipped = 0

    for trade_date in trade_dates:
        day_options = opt_daily[opt_daily["trade_date"] == trade_date]
        price_row = underlying_prices[underlying_prices["trade_date"] == trade_date]
        if price_row.empty:
            skipped += 1
            continue
        S = iv_mod.to_float(price_row.iloc[0]["underlying_close"])
        if S is None or S <= 0:
            skipped += 1
            continue

        r = iv_mod.get_risk_free_rate(shibor_df, trade_date)
        iv_result, vix_result = iv_mod.compute_daily_iv(
            trade_date, opt_basic, day_options, S, r, underlying_code,
        )

        line = merge_row(iv_result, vix_result)
        if line is not None:
            rows.append(line)
            computed += 1
        else:
            skipped += 1

    # Write CSV
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{ticker}.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        f.write(CSV_HEADER)
        f.write("\n")
        for row in rows:
            f.write(row)
            f.write("\n")

    print(f"  Written {csv_path}: {computed} rows, {skipped} skipped")
    return {
        "underlying": underlying_code,
        "ticker": ticker,
        "csv_path": str(csv_path),
        "rows": computed,
        "skipped": skipped,
    }


def run_export(
    tushare_data_path: str,
    output_root: str,
    market: str = "sse",
    underlyings: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Export IV CSV files for all underlyings."""
    if underlyings is None:
        underlyings = list(UNDERLYING_TO_TICKER.keys())

    results = []
    for underlying_code in underlyings:
        ticker = UNDERLYING_TO_TICKER.get(underlying_code, "unknown")
        output_dir = Path(output_root) / market / "daily"
        print(f"\nExporting {underlying_code} ({ticker})...")
        result = export_underlying(
            tushare_data_path, underlying_code, output_dir,
            start_date, end_date,
        )
        results.append(result)

    total_rows = sum(r.get("rows", 0) for r in results)
    errors = [r for r in results if "error" in r]
    return {
        "results": results,
        "total_rows": total_rows,
        "errors": len(errors),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export A-share implied volatility data to LEAN-format CSV files.",
    )
    parser.add_argument(
        "--tushare-data-path",
        default=iv_mod.DEFAULT_TUSHARE_DATA_PATH,
    )
    parser.add_argument(
        "--output-root",
        default=str(Path(__file__).resolve().parent.parent / "Data" / "alternative" / "ashare-implied-volatility"),
    )
    parser.add_argument(
        "--market", default="sse",
    )
    parser.add_argument(
        "--underlyings",
        default=",".join(UNDERLYING_TO_TICKER.keys()),
        help="Comma-separated underlying codes",
    )
    parser.add_argument("--start-date", help="Start date YYYYMMDD")
    parser.add_argument("--end-date", help="End date YYYYMMDD")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    underlyings = [u.strip() for u in args.underlyings.split(",") if u.strip()]

    summary = run_export(
        tushare_data_path=args.tushare_data_path,
        output_root=args.output_root,
        market=args.market,
        underlyings=underlyings,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    print(f"\nExport complete: {summary['total_rows']} total rows, {summary['errors']} errors")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
