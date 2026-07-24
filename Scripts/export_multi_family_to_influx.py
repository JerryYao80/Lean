"""Export multi-family strategy backtest and live-paper data to InfluxDB for Grafana.

Reads the daily summary, trades, family exposure, and allocation CSVs produced
by AShareDataDrivenMultiFamilyAlgorithm and writes them as InfluxDB line protocol
points to the configured bucket.
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

def esc(s):
    return s.replace(" ", "\\ ").replace(",", "\\,")


ALGORITHM_ID_ENV = os.environ.get("MF_ALGORITHM_ID", "")
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")

FAMILY_NAMES = [
    "momentum_reversal", "value_quality", "money_flow", "earnings_surprise",
    "chip_cost", "etf_premium", "sector_rotation", "margin_signal",
    "northbound_flow", "multi_factor", "analyst_signal", "macro_rate",
    "barra_momentum", "barra_value", "barra_quality",
    "low_volatility", "size_tilt", "liquidity_premium", "chip_concentration", "rate_sensitivity",
    "basis_sentiment", "options_pcr", "margin_short_ratio",
    "barra_beta", "barra_nlsize", "barra_resvol", "barra_liquidity",
]


def parse_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y%m%d").replace(
        hour=15, tzinfo=timezone.utc
    )


def esc(s: str) -> str:
    return s.replace(" ", "\\ ").replace(",", "\\,")


def write_influx(lines: list[str], dry_run: bool = False, batch_size: int = 5000) -> int:
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
        with request.urlopen(req, timeout=60) as resp:
            if resp.status >= 300:
                print(f"Write failed: {resp.status} {resp.read().decode()[:200]}", file=sys.stderr)
                return total
        total += len(batch)
    return total


def export_daily_summary(csv_path: str, algorithm_id: str, mode: str, dry_run: bool = False) -> int:
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())
            # lean_chart: equity curve
            equity = row.get("equity", "0")
            lines.append(f'lean_chart{tags},chart=Strategy\\ Equity,series=Equity value={equity} {ts}')
            # lean_portfolio
            cash = row.get("cash", "0")
            invested = row.get("invested", "0")
            holdings_count = row.get("holdings", "0")
            gross_return = row.get("gross_return", "0")
            net_return = row.get("net_return", "0")
            lines.append(
                f'lean_portfolio{tags} cash={cash},total_holdings_value={invested},'
                f'total_portfolio_value={equity},gross_return={gross_return},'
                f'net_return={net_return},holdings_count={holdings_count}i {ts}'
            )
            # lean_metric: summary
            for metric_key in ["turnover", "score_spread", "kelly_scale", "effective_exposure",
                               "regime_adjustment", "eligible_symbols", "selected_symbols",
                               "stop_loss_exits", "trailing_stop_exits",
                               "basis_composite", "pcr_composite", "vix_composite",
                               "dynamic_stop_loss", "dynamic_trailing_stop"]:
                val = row.get(metric_key, "")
                if val:
                    cat = "runtime" if metric_key in ("kelly_scale", "effective_exposure",
                                                       "regime_adjustment") else "summary"
                    lines.append(
                        f'lean_metric{tags},category={cat},metric={esc(metric_key)} '
                        f'numeric_value={val} {ts}'
                    )
    return write_influx(lines, dry_run=dry_run)


def export_family_exposure(csv_path: str, algorithm_id: str, mode: str, dry_run: bool = False) -> int:
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())
            fields = [f'holdings_count={row.get("holdings","0")}i']
            for family in FAMILY_NAMES:
                val = row.get(family, "0")
                fields.append(f'{family}={val}')
            lines.append(f'lean_family_exposure{tags} {",".join(fields)} {ts}')
    return write_influx(lines, dry_run=dry_run)


def export_trades(csv_path: str, algorithm_id: str, mode: str, dry_run: bool = False) -> int:
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
            for k in ["quantity", "price", "trade_value", "fee", "score"]:
                val = row.get(k, "")
                if val:
                    suffix = "i" if k == "quantity" else ""
                    fields.append(f'{k}={val}{suffix}')
            reason = row.get("reason", "")
            if reason:
                fields.append(f'reason="{esc(reason)}"')
            if fields:
                lines.append(f'lean_mf_order{tags} {",".join(fields)} {ts}')
    return write_influx(lines, dry_run=dry_run)


def read_lean_summary_stats(summary_json_path: str) -> dict | None:
    """Read statistics from LEAN engine's -summary.json output file."""
    import json as _json

    if not summary_json_path or not os.path.isfile(summary_json_path):
        return None

    with open(summary_json_path, encoding="utf-8") as f:
        data = _json.load(f)

    statistics = data.get("statistics", {})
    portfolio = data.get("totalPerformance", {}).get("portfolioStatistics", {})
    if not statistics and not portfolio:
        return None

    # Parse percentage strings like "11.358%" → 11.358, "0.067" → 0.067
    def parse_pct(s):
        if not s:
            return None
        s = str(s).strip().rstrip("%")
        s = s.lstrip("¥$€£")  # strip currency symbols from LEAN display values
        s = s.replace(",", "")  # strip thousands separators (e.g. "94,875.29")
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
        "Monte Carlo Trials", "Monte Carlo Horizon Days",
        "Monte Carlo Baseline Loss Probability",
        "Monte Carlo Combined Loss Probability",
        "Monte Carlo Combined Median Return",
        "Monte Carlo Combined P95 Drawdown",
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

    # Add CAGR alias (dashboard uses "CAGR" but LEAN writes "Compounding Annual Return")
    car = stats.get("Compounding Annual Return")
    if car is not None:
        stats["CAGR"] = car

    return stats


def export_backtest_stats(stats: dict, algorithm_id: str, mode: str, ts: int,
                          dry_run: bool = False) -> int:
    """Write LEAN-computed summary statistics as lean_metric and lean_portfolio_statistics points."""
    if not stats:
        return 0

    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    # Write all statistics as lean_metric points (matching InfluxDbResultExporter format)
    for metric_name, value in stats.items():
        # Always use float for numeric_value to match existing schema
        lines.append(
            f'lean_metric{tags},category=summary,metric={esc(metric_name)} '
            f'numeric_value={float(value)} {ts}'
        )

    # Write lean_portfolio_statistics (matching InfluxDbResultExporter RecordStatisticsObject format)
    # Map statistics keys to portfolio_statistics field names
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
        "drawdown_recovery": stats.get("Drawdown Recovery") or stats.get("drawdownRecovery"),
        "annual_standard_deviation": stats.get("Annual Standard Deviation") or stats.get("annualStandardDeviation"),
        "alpha": stats.get("Alpha") or stats.get("alpha"),
        "beta": stats.get("Beta") or stats.get("beta"),
        "information_ratio": stats.get("Information Ratio") or stats.get("informationRatio"),
        "tracking_error": stats.get("Tracking Error") or stats.get("trackingError"),
        "treynor_ratio": stats.get("Treynor Ratio") or stats.get("treynorRatio"),
        "portfolio_turnover": stats.get("Portfolio Turnover") or stats.get("portfolioTurnover"),
        "closed_trades": stats.get("Synthetic Closed Trades"),
    }

    pfields = []
    for fk, fv in field_map.items():
        if fv is None:
            continue
        # closed_trades and total_orders exist as integer; all others as float
        if fk in ("closed_trades", "total_orders", "drawdown_recovery"):
            pfields.append(f'{fk}={int(float(fv))}i')
        else:
            pfields.append(f'{fk}={float(fv)}')

    if pfields:
        lines.append(f'lean_portfolio_statistics{tags} {",".join(pfields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Export multi-family CSV data to InfluxDB")
    parser.add_argument("--daily-summary", required=True, help="Path to daily-summary CSV")
    parser.add_argument("--family-exposure", required=True, help="Path to family-exposure CSV")
    parser.add_argument("--trades", required=True, help="Path to trades CSV")
    parser.add_argument("--mode", required=True, choices=["backtesting", "live"],
                        help="backtesting or live")
    parser.add_argument("--algorithm-id", required=True,
                        help="Algorithm ID tag for InfluxDB (e.g. AShareMultiFamilyV1)")
    parser.add_argument("--summary-json", default=None,
                        help="Path to LEAN engine -summary.json file (required for backtest stats)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = {}
    results["daily_summary"] = export_daily_summary(args.daily_summary, args.algorithm_id, args.mode, args.dry_run)
    results["family_exposure"] = export_family_exposure(args.family_exposure, args.algorithm_id, args.mode, args.dry_run)
    results["trades"] = export_trades(args.trades, args.algorithm_id, args.mode, args.dry_run)

    # Export LEAN-computed summary statistics from -summary.json
    if args.mode == "backtesting" and args.summary_json:
        stats = read_lean_summary_stats(args.summary_json)
        if stats:
            # Use last date timestamp for summary stats
            with open(args.daily_summary, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                last_ts = int(parse_date(rows[-1]["trade_date"]).timestamp()) if rows else 0
            results["backtest_stats"] = export_backtest_stats(stats, args.algorithm_id, args.mode, last_ts, args.dry_run)

    print(json.dumps({"mode": args.mode, "results": results}))


if __name__ == "__main__":
    import json
    main()
