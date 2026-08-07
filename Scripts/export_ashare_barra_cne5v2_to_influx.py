#!/usr/bin/env python3
"""Export Barra CNE5 V2 strategy backtest and live-paper data to InfluxDB for Grafana.

Reads the daily summary, factor exposure, Monte Carlo, trades, and allocation CSVs
produced by AShareBarraCNE5V2Algorithm and writes them as InfluxDB line protocol
points to the configured bucket.

Usage:
    python export_ashare_barra_cne5v2_to_influx.py --mode backtesting --algorithm-id BarraCNE5V2
    python export_ashare_barra_cne5v2_to_influx.py --mode live --algorithm-id BarraCNE5V2Live

The script reads from:
    - Data/alternative/barra-cne5v2-outputs/barra-cne5v2-daily-summary.csv (primary)
    - Launcher/bin/Debug/barra-cne5v2-daily-summary.csv (fallback)
    - Similarly for factor-exposure, monte-carlo, trades, allocation CSVs
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request
from typing import Optional


# InfluxDB configuration
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem")

# Barra CNE5 V2 factor names (15 factors: 10 classic + 5 new)
BARRA_CNE5V2_FACTORS = [
    "beta", "momentum", "size", "earnyld", "resvol", "growth", "btop",
    "leverage", "liquidity", "nlsize",
    # New factors added in V2
    "moneyflow", "quality", "northbound", "margin", "chipcost",
]

# Monte Carlo statistics
MONTE_CARLO_STATS = [
    "terminal_value_p05", "terminal_value_p25", "terminal_value_p50",
    "terminal_value_p75", "terminal_value_p95",
    "max_drawdown_p05", "max_drawdown_p50", "max_drawdown_p95",
    "sharpe_p05", "sharpe_p50", "sharpe_p95",
]


def esc(s: str) -> str:
    """Escape special characters for InfluxDB line protocol."""
    return s.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def parse_date(s: str) -> datetime:
    """Parse YYYYMMDD date string to UTC datetime at 15:00 (market close)."""
    s = s.strip()
    if "-" in s:
        # Handle YYYY-MM-DD format
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(
            hour=15, tzinfo=timezone.utc
        )
    return datetime.strptime(s, "%Y%m%d").replace(
        hour=15, tzinfo=timezone.utc
    )


def write_influx(lines: list[str], dry_run: bool = False, batch_size: int = 5000) -> int:
    """Write lines to InfluxDB in batches."""
    if not lines:
        return 0
    if dry_run:
        for line in lines[:5]:
            print(line)
        print(f"... ({len(lines)} total)")
        return len(lines)

    total = 0
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i + batch_size]
        body = "\n".join(batch)
        url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=s"
        req = request.Request(url, data=body.encode(), method="POST")
        req.add_header("Authorization", f"Token {INFLUX_TOKEN}")
        req.add_header("Content-Type", "text/plain; charset=utf-8")
        try:
            with request.urlopen(req, timeout=60) as resp:
                if resp.status >= 300:
                    print(f"Write failed: {resp.status} {resp.read().decode()[:200]}", file=sys.stderr)
                    return total
        except Exception as e:
            print(f"Write error: {e}", file=sys.stderr)
            return total
        total += len(batch)
    return total


def find_csv_path(filename: str, data_dir: Optional[str] = None) -> Optional[str]:
    """Find CSV file in primary or fallback locations."""
    # Primary: Data/alternative/barra-cne5v2-outputs/
    repo_root = Path(__file__).resolve().parents[1]
    primary_path = repo_root / "Data" / "alternative" / "barra-cne5v2-outputs" / filename
    if primary_path.exists():
        return str(primary_path)

    # Fallback: Launcher/bin/Debug/
    fallback_path = repo_root / "Launcher" / "bin" / "Debug" / filename
    if fallback_path.exists():
        return str(fallback_path)

    # Custom data directory
    if data_dir:
        custom_path = Path(data_dir) / filename
        if custom_path.exists():
            return str(custom_path)

    return None


def export_daily_summary(csv_path: str, algorithm_id: str, mode: str,
                         dry_run: bool = False) -> int:
    """Export daily summary data to barra_cne5v2_daily measurement."""
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())

            # Build fields for barra_cne5v2_daily
            fields = []

            # Portfolio metrics
            for field_name in ["equity", "cash", "invested", "gross_return", "net_return"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={val}')

            # Integer fields
            for field_name in ["holdings", "eligible_symbols", "selected_symbols",
                               "stop_loss_exits", "trailing_stop_exits"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={int(float(val))}i')

            # Float fields
            for field_name in ["turnover", "score_spread", "kelly_scale",
                               "effective_exposure"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={val}')

            # Boolean field
            rebalanced = row.get("rebalanced", "0")
            fields.append(f'rebalanced={int(float(rebalanced))}i')

            if fields:
                lines.append(f'barra_cne5v2_daily{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_factor_exposure(csv_path: str, algorithm_id: str, mode: str,
                           dry_run: bool = False) -> int:
    """Export factor exposure data to barra_cne5v2_factor_exposure measurement."""
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())

            fields = []

            # Holdings count (integer)
            holdings = row.get("holdings", "0")
            fields.append(f'holdings={int(float(holdings))}i')

            # Factor exposures (float)
            for factor in BARRA_CNE5V2_FACTORS:
                val = row.get(factor, "")
                if val:
                    fields.append(f'{factor}={val}')

            if fields:
                lines.append(f'barra_cne5v2_factor_exposure{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_monte_carlo(csv_path: str, algorithm_id: str, mode: str,
                       dry_run: bool = False) -> int:
    """Export Monte Carlo simulation results to barra_cne5v2_monte_carlo measurement."""
    if not csv_path or not Path(csv_path).exists():
        return 0

    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    # Read Monte Carlo CSV (format: statistic,p05,p25,p50,p75,p95)
    mc_data = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stat = row.get("statistic", "").strip().lower()
            if stat:
                mc_data[stat] = row

    if not mc_data:
        return 0

    # Build a single point with all Monte Carlo fields
    # Use current timestamp for Monte Carlo results (they are summary stats)
    ts = int(datetime.now(tz=timezone.utc).timestamp())

    fields = []

    # Terminal value percentiles
    tv = mc_data.get("terminal_value", {})
    for pct, col in [("p05", "p05"), ("p25", "p25"), ("p50", "p50"),
                     ("p75", "p75"), ("p95", "p95")]:
        val = tv.get(col, "")
        if val:
            fields.append(f'terminal_value_{pct}={val}')

    # Max drawdown percentiles
    md = mc_data.get("max_drawdown", {})
    for pct, col in [("p05", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = md.get(col, "")
        if val:
            fields.append(f'max_drawdown_{pct}={val}')

    # Sharpe ratio percentiles
    sr = mc_data.get("sharpe_ratio", {})
    for pct, col in [("p05", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = sr.get(col, "")
        if val:
            fields.append(f'sharpe_{pct}={val}')

    if fields:
        lines.append(f'barra_cne5v2_monte_carlo{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_trades(csv_path: str, algorithm_id: str, mode: str,
                  dry_run: bool = False) -> int:
    """Export trade data to barra_cne5v2_trades measurement."""
    if not csv_path or not Path(csv_path).exists():
        return 0

    lines: list[str] = []
    tags_base = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trade_date = row.get("trade_date", "").strip()
            if not trade_date:
                continue

            ts = int(parse_date(trade_date).timestamp())
            symbol = esc(row.get("symbol", ""))
            action = row.get("action", "")

            tags = f'{tags_base},symbol={symbol},action={action}'

            fields = []

            # Quantity (integer)
            quantity = row.get("quantity", "")
            if quantity:
                fields.append(f'quantity={int(float(quantity))}i')

            # Float fields
            for k in ["price", "trade_value", "fee", "score"]:
                val = row.get(k, "")
                if val:
                    fields.append(f'{k}={val}')

            # Reason (string)
            reason = row.get("reason", "")
            if reason:
                fields.append(f'reason="{esc(reason)}"')

            if fields:
                lines.append(f'barra_cne5v2_trades{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_allocation(csv_path: str, algorithm_id: str, mode: str,
                      dry_run: bool = False) -> int:
    """Export allocation data to barra_cne5v2_allocation measurement."""
    if not csv_path or not Path(csv_path).exists():
        return 0

    lines: list[str] = []
    tags_base = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trade_date = row.get("trade_date", "").strip()
            if not trade_date:
                continue

            ts = int(parse_date(trade_date).timestamp())
            symbol = esc(row.get("symbol", ""))

            tags = f'{tags_base},symbol={symbol}'

            fields = []

            # Weight (float)
            weight = row.get("weight", "")
            if weight:
                fields.append(f'weight={weight}')

            # Quantity (integer)
            quantity = row.get("quantity", "")
            if quantity:
                fields.append(f'quantity={int(float(quantity))}i')

            # Float fields
            for k in ["price", "market_price", "market_value", "score"]:
                val = row.get(k, "")
                if val:
                    fields.append(f'{k}={val}')

            if fields:
                lines.append(f'barra_cne5v2_allocation{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def read_lean_summary_stats(summary_json_path: str) -> Optional[dict]:
    """Read statistics from LEAN engine's -summary.json output file."""
    if not summary_json_path or not os.path.isfile(summary_json_path):
        return None

    with open(summary_json_path, encoding="utf-8") as f:
        data = json.load(f)

    statistics = data.get("statistics", {})
    portfolio = data.get("totalPerformance", {}).get("portfolioStatistics", {})
    if not statistics and not portfolio:
        return None

    def parse_pct(s):
        if not s:
            return None
        s = str(s).strip().rstrip("%")
        try:
            return float(s)
        except ValueError:
            return None

    stats = {}
    # From statistics dict (display values)
    for key in [
        "Total Orders", "Average Win", "Average Loss",
        "Compounding Annual Return", "Drawdown", "Expectancy",
        "Start Equity", "End Equity", "Net Profit",
        "Sharpe Ratio", "Sortino Ratio", "Probabilistic Sharpe Ratio",
        "Loss Rate", "Win Rate", "Profit-Loss Ratio",
        "Alpha", "Beta", "Annual Standard Deviation", "Annual Variance",
        "Information Ratio", "Tracking Error", "Treynor Ratio",
        "Total Fees", "Portfolio Turnover", "Drawdown Recovery",
    ]:
        val = statistics.get(key)
        if val is not None:
            parsed = parse_pct(val)
            if parsed is not None:
                stats[key] = parsed

    # From totalPerformance.portfolioStatistics (raw numeric values)
    for key in [
        "startEquity", "endEquity", "compoundingAnnualReturn", "drawdown",
        "totalNetProfit", "sharpeRatio", "sortinoRatio", "probabilisticSharpeRatio",
        "alpha", "beta", "annualStandardDeviation", "annualVariance",
        "informationRatio", "trackingError", "treynorRatio",
        "portfolioTurnover", "valueAtRisk99", "valueAtRisk95", "drawdownRecovery",
        "averageWinRate", "averageLossRate", "profitLossRatio",
        "winRate", "lossRate", "expectancy",
    ]:
        val = portfolio.get(key)
        if val is not None:
            parsed = parse_pct(val)
            if parsed is not None:
                stats[key] = parsed

    return stats


def export_backtest_stats(stats: dict, algorithm_id: str, mode: str, ts: int,
                          dry_run: bool = False) -> int:
    """Write LEAN-computed summary statistics as lean_metric points."""
    if not stats:
        return 0

    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    # Write all statistics as lean_metric points
    for metric_name, value in stats.items():
        lines.append(
            f'lean_metric{tags},category=summary,metric={esc(metric_name)} '
            f'numeric_value={float(value)} {ts}'
        )

    # Write barra_cne5v2_stats measurement for V2-specific dashboard
    field_map = {
        "total_return": stats.get("Net Profit") or stats.get("totalNetProfit"),
        "cagr": stats.get("Compounding Annual Return") or stats.get("compoundingAnnualReturn"),
        "sharpe_ratio": stats.get("Sharpe Ratio") or stats.get("sharpeRatio"),
        "max_drawdown": stats.get("Drawdown") or stats.get("drawdown"),
        "win_rate": stats.get("Win Rate") or stats.get("winRate"),
        "loss_rate": stats.get("Loss Rate") or stats.get("lossRate"),
        "profit_loss_ratio": stats.get("Profit-Loss Ratio") or stats.get("profitLossRatio"),
        "sortino_ratio": stats.get("Sortino Ratio") or stats.get("sortinoRatio"),
        "expectancy": stats.get("Expectancy") or stats.get("expectancy"),
        "total_fees": stats.get("Total Fees"),
        "total_orders": stats.get("Total Orders"),
        "alpha": stats.get("Alpha") or stats.get("alpha"),
        "beta": stats.get("Beta") or stats.get("beta"),
        "information_ratio": stats.get("Information Ratio") or stats.get("informationRatio"),
        "portfolio_turnover": stats.get("Portfolio Turnover") or stats.get("portfolioTurnover"),
    }

    pfields = []
    for fk, fv in field_map.items():
        if fv is None:
            continue
        if fk in ("total_orders",):
            pfields.append(f'{fk}={int(float(fv))}i')
        else:
            pfields.append(f'{fk}={float(fv)}')

    if pfields:
        lines.append(f'barra_cne5v2_stats{tags} {",".join(pfields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Export Barra CNE5 V2 CSV data to InfluxDB"
    )
    parser.add_argument(
        "--mode", required=True, choices=["backtesting", "live"],
        help="backtesting or live"
    )
    parser.add_argument(
        "--algorithm-id", required=True,
        help="Algorithm ID tag for InfluxDB (e.g. BarraCNE5V2)"
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Custom data directory (default: Data/alternative/barra-cne5v2-outputs or Launcher/bin/Debug)"
    )
    parser.add_argument(
        "--daily-summary", default=None,
        help="Path to daily-summary CSV (auto-detected if not specified)"
    )
    parser.add_argument(
        "--factor-exposure", default=None,
        help="Path to factor-exposure CSV (auto-detected if not specified)"
    )
    parser.add_argument(
        "--monte-carlo", default=None,
        help="Path to monte-carlo CSV (auto-detected if not specified)"
    )
    parser.add_argument(
        "--trades", default=None,
        help="Path to trades CSV (auto-detected if not specified)"
    )
    parser.add_argument(
        "--allocation", default=None,
        help="Path to allocation CSV (auto-detected if not specified)"
    )
    parser.add_argument(
        "--summary-json", default=None,
        help="Path to LEAN engine -summary.json file (for backtest stats)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print lines instead of writing to InfluxDB"
    )
    parser.add_argument(
        "--oos", action="store_true",
        help="Use OOS (out-of-sample) files instead of full-sample"
    )
    args = parser.parse_args()

    # Determine file paths
    suffix = "-oos" if args.oos else ""
    daily_summary_path = args.daily_summary or find_csv_path(
        f"barra-cne5v2{suffix}-daily-summary.csv", args.data_dir
    )
    factor_exposure_path = args.factor_exposure or find_csv_path(
        f"barra-cne5v2{suffix}-factor-exposure.csv", args.data_dir
    )
    monte_carlo_path = args.monte_carlo or find_csv_path(
        f"barra-cne5v2{suffix}-monte-carlo.csv", args.data_dir
    )
    trades_path = args.trades or find_csv_path(
        f"barra-cne5v2{suffix}-trades.csv", args.data_dir
    )
    allocation_path = args.allocation or find_csv_path(
        f"barra-cne5v2{suffix}-allocation.csv", args.data_dir
    )

    results = {}
    errors = []

    # Export daily summary
    if daily_summary_path:
        print(f"Exporting daily summary from: {daily_summary_path}")
        results["daily_summary"] = export_daily_summary(
            daily_summary_path, args.algorithm_id, args.mode, args.dry_run
        )
    else:
        errors.append("daily-summary CSV not found")

    # Export factor exposure
    if factor_exposure_path:
        print(f"Exporting factor exposure from: {factor_exposure_path}")
        results["factor_exposure"] = export_factor_exposure(
            factor_exposure_path, args.algorithm_id, args.mode, args.dry_run
        )
    else:
        errors.append("factor-exposure CSV not found")

    # Export Monte Carlo
    if monte_carlo_path:
        print(f"Exporting Monte Carlo from: {monte_carlo_path}")
        results["monte_carlo"] = export_monte_carlo(
            monte_carlo_path, args.algorithm_id, args.mode, args.dry_run
        )
    else:
        # Monte Carlo is optional for live mode
        if args.mode == "backtesting":
            errors.append("monte-carlo CSV not found")

    # Export trades
    if trades_path:
        print(f"Exporting trades from: {trades_path}")
        results["trades"] = export_trades(
            trades_path, args.algorithm_id, args.mode, args.dry_run
        )
    else:
        errors.append("trades CSV not found")

    # Export allocation
    if allocation_path:
        print(f"Exporting allocation from: {allocation_path}")
        results["allocation"] = export_allocation(
            allocation_path, args.algorithm_id, args.mode, args.dry_run
        )
    else:
        errors.append("allocation CSV not found")

    # Export LEAN summary stats for backtesting mode
    if args.mode == "backtesting" and args.summary_json:
        print(f"Exporting backtest stats from: {args.summary_json}")
        stats = read_lean_summary_stats(args.summary_json)
        if stats and daily_summary_path:
            # Get last timestamp from daily summary
            with open(daily_summary_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                last_ts = int(parse_date(rows[-1]["trade_date"]).timestamp()) if rows else 0
            results["backtest_stats"] = export_backtest_stats(
                stats, args.algorithm_id, args.mode, last_ts, args.dry_run
            )

    output = {
        "mode": args.mode,
        "algorithm_id": args.algorithm_id,
        "oos": args.oos,
        "results": results,
    }
    if errors:
        output["errors"] = errors

    print(json.dumps(output, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
