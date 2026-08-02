"""Backtest validation script for manipulation detection factors.

Loads factor values from parquet files and tests correlation with future
returns to validate signal quality. Computes Information Coefficient (IC)
via Spearman rank correlation for each factor.

Usage:
    python backtest_manipulation_signals.py \
        --start-date 2024-01-01 \
        --end-date 2024-06-30 \
        --result-root /home/project/hope/Lean/result \
        --data-root /home/project/tushare-downloader/tushare_data_v2
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("backtest_manipulation_signals")

# ── factor IDs to evaluate ──
MANIPULATION_FACTORS = [
    "turnover_anomaly",
    "amplitude_anomaly",
    "limit_behavior",
    "intraday_reversal",
]


def load_factor(factor_id: str, date: str, result_root: str) -> pd.DataFrame:
    """Load factor parquet for a date."""
    p = Path(result_root) / "factor-zoo" / factor_id / f"{date}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _read_daily_partition(ts_code: str, data_root: str) -> pd.DataFrame:
    """Read a single stock's daily data from tushare parquet."""
    p = Path(data_root) / "daily" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def compute_forward_returns(data_root: str, date: str, forward_days: int = 5) -> pd.DataFrame:
    """Compute forward returns for all stocks on a date.

    Forward return is defined as (close_{t+n} - close_t) / close_t,
    where t is the given date and n = forward_days.
    """
    date_compact = date.replace("-", "")

    # Need trade calendar to map t -> t+n
    cal_path = Path(data_root) / "trade_cal" / "data.parquet"
    if not cal_path.exists():
        return pd.DataFrame()

    cal = pd.read_parquet(cal_path)
    cal["cal_date"] = cal["cal_date"].astype(str)
    cal = cal[cal["is_open"].isin({1, "1", "True", "true"})]
    trade_dates = sorted(cal["cal_date"].tolist())

    if date_compact not in trade_dates:
        return pd.DataFrame()

    idx = trade_dates.index(date_compact)
    if idx + forward_days >= len(trade_dates):
        return pd.DataFrame()

    future_date = trade_dates[idx + forward_days]

    # List all available ts_codes from daily partitions
    daily_dir = Path(data_root) / "daily"
    if not daily_dir.exists():
        return pd.DataFrame()

    ts_codes = sorted(
        p.name.replace("ts_code=", "")
        for p in daily_dir.iterdir()
        if p.is_dir() and p.name.startswith("ts_code=")
    )

    rows: list[dict] = []
    for ts_code in ts_codes:
        df = _read_daily_partition(ts_code, data_root)
        if df.empty or "trade_date" not in df.columns or "close" not in df.columns:
            continue
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        current = df[df["trade_date"] == date_compact]
        future = df[df["trade_date"] == future_date]
        if current.empty or future.empty:
            continue
        close_t = float(current["close"].iloc[0])
        close_future = float(future["close"].iloc[0])
        if close_t == 0:
            continue
        ret = (close_future - close_t) / close_t
        rows.append({"ts_code": ts_code, "return": ret})

    return pd.DataFrame(rows)


def compute_ic(factor_df: pd.DataFrame, returns_df: pd.DataFrame) -> float:
    """Compute Spearman rank correlation between factor and forward returns."""
    if factor_df.empty or returns_df.empty:
        return float("nan")

    # Determine factor column name (not ts_code)
    factor_cols = [c for c in factor_df.columns if c != "ts_code"]
    if not factor_cols:
        return float("nan")
    factor_col = factor_cols[0]

    merged = factor_df.merge(returns_df, on="ts_code", how="inner")
    if len(merged) < 2:
        return float("nan")

    return float(merged[factor_col].corr(merged["return"], method="spearman"))


def analyze_factor(factor_id: str, dates: list[str], result_root: str, data_root: str, forward_days: int = 5) -> dict:
    """Analyze factor over a date range."""
    ics: list[float] = []
    for date in dates:
        factor_df = load_factor(factor_id, date, result_root)
        if factor_df.empty:
            continue
        returns_df = compute_forward_returns(data_root, date, forward_days=forward_days)
        if returns_df.empty:
            continue
        ic = compute_ic(factor_df, returns_df)
        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        return {
            "mean_ic": float("nan"),
            "ic_std": float("nan"),
            "ir": float("nan"),
            "sample_count": 0,
        }

    mean_ic = float(np.mean(ics))
    ic_std = float(np.std(ics, ddof=1))
    ir = mean_ic / ic_std if ic_std > 0 else 0.0
    return {
        "mean_ic": mean_ic,
        "ic_std": ic_std,
        "ir": ir,
        "sample_count": len(ics),
    }


def trade_days_in_range(start: str, end: str, data_root: str) -> list[str]:
    """Return trade days (YYYY-MM-DD) between start and end inclusive."""
    cal_path = Path(data_root) / "trade_cal" / "data.parquet"
    if not cal_path.exists():
        return []

    cal = pd.read_parquet(cal_path)
    cal["cal_date"] = cal["cal_date"].astype(str)
    cal = cal[cal["is_open"].isin({1, "1", "True", "true"})]
    cal = cal[(cal["cal_date"] >= start.replace("-", "")) & (cal["cal_date"] <= end.replace("-", ""))]
    compact = sorted(cal["cal_date"].tolist())
    return [f"{c[:4]}-{c[4:6]}-{c[6:]}" for c in compact]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backtest validation for manipulation detection factors"
    )
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--result-root", required=True, help="Path to result/ directory")
    parser.add_argument("--data-root", required=True, help="Path to tushare_data_v2/ directory")
    parser.add_argument("--forward-days", type=int, default=5, help="Forward return horizon (default 5)")
    parser.add_argument("--output", default=None, help="Output JSON path (default result/manipulation_factor_analysis.json)")
    parser.add_argument("--factors", nargs="*", default=None, help="Factor IDs to evaluate (default: all manipulation factors)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result_root = args.result_root
    data_root = args.data_root
    factors = args.factors or MANIPULATION_FACTORS

    dates = trade_days_in_range(args.start_date, args.end_date, data_root)
    log.info("Trade days in range: %d", len(dates))
    if not dates:
        log.error("No trade days in range; aborting")
        return 1

    results: dict[str, dict] = {}
    for factor_id in factors:
        log.info("Analyzing factor: %s", factor_id)
        stats = analyze_factor(factor_id, dates, result_root, data_root, forward_days=args.forward_days)
        results[factor_id] = stats
        log.info("%s -> mean_ic=%.4f, ic_std=%.4f, ir=%.4f, n=%d",
                 factor_id, stats["mean_ic"], stats["ic_std"], stats["ir"], stats["sample_count"])

    output = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "forward_days": args.forward_days,
        "factors": results,
    }

    # Print to stdout
    print(json.dumps(output, ensure_ascii=False, indent=2))

    # Save to file
    out_path = Path(args.output) if args.output else Path(result_root) / "manipulation_factor_analysis.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Results saved to %s", out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
