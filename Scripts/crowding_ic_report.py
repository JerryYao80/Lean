"""Crowding factor IC / decile / IR analysis.

Computes:
  - Daily IC (Spearman rank correlation of composite crowding vs forward N-day returns)
  - IC mean, IC IR (mean / std * sqrt(252))
  - 5-decile cumulative return spread (Q1 low-crowding vs Q5 high-crowding)
  - Monthly turnover of the low-crowding 30% portfolio

Usage:
  python Scripts/crowding_ic_report.py \\
    --result-root /home/project/hope/Lean/result \\
    --data-root /home/project/tushare-downloader/tushare_data_v2 \\
    --start 2024-01-01 --end 2024-06-28 \\
    --forward-days 5 10 20 \\
    --output /home/project/hope/Lean/docs/crowding-ic-results.json
"""
import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def load_crowding_scores(result_root, date_str):
    """Load all crowding parquet files for a given date (YYYY-MM-DD)."""
    date_dir = Path(result_root) / "crowding-factor" / date_str
    if not date_dir.exists():
        return pd.DataFrame()
    rows = []
    for p in sorted(date_dir.glob("*.parquet")):
        try:
            df = pd.read_parquet(p)
            if not df.empty:
                rows.append(df.iloc[0])
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_close_prices(data_root, ts_code):
    """Load daily close prices for a ts_code from daily_basic parquet."""
    p = Path(data_root) / "daily_basic" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(p)
    if "trade_date" not in df.columns or "close" not in df.columns:
        return pd.Series(dtype=float)
    df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    df = df.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return df.set_index("trade_date")["close"].astype(float)


def compute_forward_returns(close_series, forward_days):
    """Compute forward N-day returns: (close[t+N] / close[t]) - 1."""
    fwd = close_series.shift(-forward_days) / close_series - 1.0
    return fwd


def main():
    parser = argparse.ArgumentParser(description="Crowding IC/decile/IR report")
    parser.add_argument("--result-root", default="/home/project/hope/Lean/result")
    parser.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    parser.add_argument("--start", default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2024-06-28", help="End date YYYY-MM-DD")
    parser.add_argument("--forward-days", nargs="+", type=int, default=[5, 10, 20])
    parser.add_argument("--output", default="/home/project/hope/Lean/docs/crowding-ic-results.json")
    args = parser.parse_args()

    # Gather all crowding dates
    crowding_root = Path(args.result_root) / "crowding-factor"
    if not crowding_root.exists():
        print(f"ERROR: {crowding_root} does not exist", file=sys.stderr)
        return 1

    crowding_dates = sorted([d.name for d in crowding_root.iterdir() if d.is_dir()])
    crowding_dates = [d for d in crowding_dates if args.start <= d <= args.end]
    print(f"Crowding dates: {len(crowding_dates)} ({crowding_dates[0]} to {crowding_dates[-1]})")

    if not crowding_dates:
        print("ERROR: no crowding dates found", file=sys.stderr)
        return 1

    # Get all ts_codes from the first date
    first_scores = load_crowding_scores(args.result_root, crowding_dates[0])
    all_ts_codes = sorted(first_scores["ts_code"].tolist())
    print(f"Unique ts_codes: {len(all_ts_codes)}")

    # Load close prices for all ts_codes
    print("Loading close prices...", flush=True)
    close_data = {}  # ts_code -> pd.Series indexed by trade_date
    for i, tc in enumerate(all_ts_codes):
        s = load_close_prices(args.data_root, tc)
        if not s.empty:
            close_data[tc] = s
        if (i + 1) % 100 == 0:
            print(f"  loaded {i+1}/{len(all_ts_codes)}", flush=True)
    print(f"Loaded close prices for {len(close_data)} ts_codes")

    # Compute forward returns for each forward day horizon
    fwd_returns = {}  # forward_days -> {ts_code -> pd.Series}
    for fd in args.forward_days:
        fwd_returns[fd] = {}
        for tc, close_s in close_data.items():
            fwd_returns[fd][tc] = compute_forward_returns(close_s, fd)

    # Compute daily IC for each forward horizon
    ic_results = {}
    for fd in args.forward_days:
        daily_ics = []
        for date_str in crowding_dates:
            scores = load_crowding_scores(args.result_root, date_str)
            if scores.empty:
                continue
            # Convert date_str (YYYY-MM-DD) to trade_date (YYYYMMDD)
            td = date_str.replace("-", "")
            # Get forward returns for each ts_code on this date
            rets = {}
            for _, row in scores.iterrows():
                tc = row["ts_code"]
                if tc in fwd_returns[fd] and td in fwd_returns[fd][tc].index:
                    r = fwd_returns[fd][tc].loc[td]
                    if pd.notna(r):
                        rets[tc] = r
            if len(rets) < 10:
                continue
            # Align scores and returns
            common_tcs = [tc for tc in rets if tc in scores["ts_code"].values]
            if len(common_tcs) < 10:
                continue
            score_vals = [float(scores[scores["ts_code"] == tc]["composite"].iloc[0]) for tc in common_tcs]
            ret_vals = [rets[tc] for tc in common_tcs]
            if np.std(score_vals) < 1e-10 or np.std(ret_vals) < 1e-10:
                daily_ics.append(0.0)
                continue
            ic, _ = spearmanr(score_vals, ret_vals)
            daily_ics.append(ic if not np.isnan(ic) else 0.0)
        if daily_ics:
            ic_arr = np.array(daily_ics)
            ic_mean = float(np.mean(ic_arr))
            ic_std = float(np.std(ic_arr, ddof=1)) if len(ic_arr) > 1 else 0.0
            ic_ir = ic_mean / ic_std * np.sqrt(252) if ic_std > 1e-10 else 0.0
            ic_results[fd] = {
                "ic_mean": ic_mean,
                "ic_std": ic_std,
                "ic_ir": float(ic_ir),
                "n_days": len(daily_ics),
                "ic_positive_pct": float(np.mean(np.array(daily_ics) > 0)),
                "ic_negative_pct": float(np.mean(np.array(daily_ics) < 0)),
                "daily_ics_sample": daily_ics[:20],
            }
            print(f"  Forward {fd}d: IC mean={ic_mean:.4f}, IR={ic_ir:.4f}, n={len(daily_ics)}, "
                  f"positive%={ic_results[fd]['ic_positive_pct']:.1%}")

    # Decile analysis: 5 deciles based on crowding score, cumulative returns
    print("\nComputing decile analysis...", flush=True)
    # Use 10-day forward returns for decile analysis
    fd_decile = 10
    decile_returns = {i: [] for i in range(5)}  # 5 deciles, 0=lowest crowding
    monthly_turnover = []

    prev_q1_set = None
    for date_str in crowding_dates:
        scores = load_crowding_scores(args.result_root, date_str)
        if scores.empty:
            continue
        td = date_str.replace("-", "")
        scores_sorted = scores.sort_values("composite")
        n = len(scores_sorted)
        if n < 10:
            continue
        # Split into 5 deciles
        decile_size = n // 5
        for qi in range(5):
            start_idx = qi * decile_size
            end_idx = (qi + 1) * decile_size if qi < 4 else n
            q_tcs = scores_sorted.iloc[start_idx:end_idx]["ts_code"].tolist()
            # Average forward return for this decile
            rets = []
            for tc in q_tcs:
                if tc in fwd_returns[fd_decile] and td in fwd_returns[fd_decile][tc].index:
                    r = fwd_returns[fd_decile][tc].loc[td]
                    if pd.notna(r):
                        rets.append(r)
            if rets:
                decile_returns[qi].append(float(np.mean(rets)))

        # Turnover: Q1 (lowest crowding 30%) membership change
        q1_end = max(1, int(n * 0.30))
        q1_set = set(scores_sorted.iloc[:q1_end]["ts_code"].tolist())
        if prev_q1_set is not None:
            overlap = len(q1_set & prev_q1_set)
            turnover = 1.0 - overlap / max(len(q1_set), 1)
            monthly_turnover.append(turnover)
        prev_q1_set = q1_set

    decile_summary = {}
    for qi in range(5):
        if decile_returns[qi]:
            arr = np.array(decile_returns[qi])
            decile_summary[f"Q{qi+1}"] = {
                "mean_return": float(np.mean(arr)),
                "std_return": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
                "cumulative_return": float(np.prod(1 + arr) - 1),
                "n_periods": len(arr),
            }
            print(f"  Q{qi+1}: mean={np.mean(arr):.4%}, cumul={np.prod(1+arr)-1:.4%}, n={len(arr)}")

    # Q1 (low crowding) vs Q5 (high crowding) spread
    if decile_returns[0] and decile_returns[4]:
        q1_arr = np.array(decile_returns[0])
        q5_arr = np.array(decile_returns[4])
        min_len = min(len(q1_arr), len(q5_arr))
        spread = q1_arr[:min_len] - q5_arr[:min_len]
        decile_summary["Q1_Q5_spread"] = {
            "mean_spread": float(np.mean(spread)),
            "t_stat": float(np.mean(spread) / (np.std(spread, ddof=1) / np.sqrt(len(spread)))) if len(spread) > 1 and np.std(spread, ddof=1) > 1e-10 else 0.0,
            "n_periods": len(spread),
        }
        print(f"  Q1-Q5 spread: mean={np.mean(spread):.4%}, t={decile_summary['Q1_Q5_spread']['t_stat']:.2f}")

    turnover_summary = {
        "mean_monthly_turnover": float(np.mean(monthly_turnover)) if monthly_turnover else 0.0,
        "n_rebalances": len(monthly_turnover),
    }
    print(f"\nMonthly turnover (Q1 30%): mean={turnover_summary['mean_monthly_turnover']:.2%}, "
          f"n={turnover_summary['n_rebalances']}")

    # Degradation stats from builder summary
    build_summary_path = Path("/tmp/crowding_build_summary.json")
    degradation = {}
    if build_summary_path.exists():
        with open(build_summary_path) as f:
            degradation = json.load(f)

    results = {
        "analysis_date": datetime.now().isoformat(),
        "backtest_window": {"start": args.start, "end": args.end},
        "crowding_dates": len(crowding_dates),
        "ts_codes": len(all_ts_codes),
        "ic_analysis": ic_results,
        "decile_analysis": decile_summary,
        "turnover": turnover_summary,
        "degradation": degradation,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
