#!/usr/bin/env python3
"""Unified export script for Barra CNE5 V2/V2.1/V3/V3.2 strategies to InfluxDB.

Writes to BOTH:
1. Barra-specific measurements (barra_cne5v*_daily, etc.) for backward compatibility
2. Shared measurements (lean_chart, lean_portfolio, lean_metric, lean_family_exposure, 
   lean_mf_order, lean_portfolio_statistics) for dashboard compatibility with AShareMultiFamily

Usage:
    python export_barra_cne5_unified.py --version v2 --mode backtesting
    python export_barra_cne5_unified.py --version v2_1 --mode backtesting
    python export_barra_cne5_unified.py --version v3 --mode backtesting
    python export_barra_cne5_unified.py --version v3_2 --mode backtesting
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

# Barra CNE5 factors (15 factors)
BARRA_FACTORS = [
    "beta", "momentum", "size", "earnyld", "resvol", "growth", "btop",
    "leverage", "liquidity", "nlsize",
    "moneyflow", "quality", "northbound", "margin", "chipcost",
]

# Mapping from Barra factors to AShareMultiFamily family names
# This allows the same dashboard panels to work for both strategy types
BARRA_TO_FAMILY_MAP = {
    "beta": "barra_beta",
    "momentum": "barra_momentum",
    "size": "size_tilt",
    "earnyld": "earnings_surprise",
    "resvol": "barra_resvol",
    "growth": "multi_factor",  # growth factor maps to multi_factor
    "btop": "barra_value",  # book-to-price is value
    "leverage": "rate_sensitivity",  # leverage sensitive to rates
    "liquidity": "barra_liquidity",
    "nlsize": "barra_nlsize",
    "moneyflow": "money_flow",
    "quality": "barra_quality",
    "northbound": "northbound_flow",
    "margin": "margin_signal",
    "chipcost": "chip_cost",
}

# All 27 family names (for lean_family_exposure)
FAMILY_NAMES = [
    "momentum_reversal", "value_quality", "money_flow", "earnings_surprise",
    "chip_cost", "etf_premium", "sector_rotation", "margin_signal",
    "northbound_flow", "multi_factor", "analyst_signal", "macro_rate",
    "barra_momentum", "barra_value", "barra_quality",
    "low_volatility", "size_tilt", "liquidity_premium", "chip_concentration", "rate_sensitivity",
    "basis_sentiment", "options_pcr", "margin_short_ratio",
    "barra_beta", "barra_nlsize", "barra_resvol", "barra_liquidity",
]

# Version-specific configuration
VERSION_CONFIG = {
    "v2": {
        "algorithm_id": "AShareBarraCNE5V2Algorithm",
        "csv_prefix": "barra-cne5v2",
        "summary_json": "AShareBarraCNE5V2Algorithm-summary.json",
        "daily_fields": ["equity", "cash", "invested", "gross_return", "net_return"],
        "has_oos": True,
    },
    "v2_1": {
        "algorithm_id": "AShareBarraCNE5V2_1Algorithm",
        "csv_prefix": "barra-cne5v2-1",
        "summary_json": "AShareBarraCNE5V2_1Algorithm-summary.json",
        "daily_fields": ["equity", "cash", "invested", "daily_return"],
        "has_oos": False,
    },
    "v3": {
        "algorithm_id": "AShareBarraCNE5V3Algorithm",
        "csv_prefix": "barra-cne5v3",
        "summary_json": "AShareBarraCNE5V3Algorithm-summary.json",
        "daily_fields": ["equity", "cash", "invested", "daily_return"],
        "has_oos": False,
    },
    "v3_2": {
        "algorithm_id": "AShareBarraCNE5V3_2Algorithm",
        "csv_prefix": "barra-cne5v3-2",
        "summary_json": "AShareBarraCNE5V3_2Algorithm-summary.json",
        "daily_fields": ["equity", "cash", "invested", "daily_return"],
        "has_oos": False,
    },
}


def esc(s: str) -> str:
    """Escape special characters for InfluxDB line protocol."""
    return s.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def parse_date(s: str) -> datetime:
    """Parse YYYYMMDD date string to UTC datetime at 15:00 (market close)."""
    s = s.strip()
    if "-" in s:
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
    repo_root = Path(__file__).resolve().parents[1]
    
    # Primary: Launcher/bin/Debug/
    fallback_path = repo_root / "Launcher" / "bin" / "Debug" / filename
    if fallback_path.exists():
        return str(fallback_path)
    
    # Custom data directory
    if data_dir:
        custom_path = Path(data_dir) / filename
        if custom_path.exists():
            return str(custom_path)
    
    return None


def find_summary_json(version: str) -> Optional[str]:
    """Find summary JSON file."""
    repo_root = Path(__file__).resolve().parents[1]
    config = VERSION_CONFIG[version]
    
    # Try Results/ first
    results_path = repo_root / "Results" / config["summary_json"]
    if results_path.exists():
        return str(results_path)
    
    # Try Launcher/bin/Debug/
    launcher_path = repo_root / "Launcher" / "bin" / "Debug" / config["summary_json"]
    if launcher_path.exists():
        return str(launcher_path)
    
    return None


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


def export_daily_summary(csv_path: str, version: str, mode: str, dry_run: bool = False) -> int:
    """Export daily summary data to both barra-specific and shared measurements."""
    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]
    
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_daily"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())

            # === Barra-specific measurement ===
            fields = []
            for field_name in config["daily_fields"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={val}')
            
            # Integer fields
            for field_name in ["holdings"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={int(float(val))}i')
            
            # Float fields (version-specific)
            for field_name in ["exposure_scale", "effective_exposure", "sharpe_annualized",
                               "kelly_scale", "turnover", "avg_ic", "avg_ir", "vol_target_scale"]:
                val = row.get(field_name, "")
                if val:
                    fields.append(f'{field_name}={val}')
            
            # Regime (string)
            regime = row.get("regime", "")
            if regime:
                fields.append(f'regime="{esc(regime)}"')
            
            if fields:
                lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')

            # === Shared measurements ===
            equity = row.get("equity", "0")
            cash = row.get("cash", "0")
            invested = row.get("invested", "0")
            holdings_count = row.get("holdings", "0")
            
            # lean_chart: equity curve
            lines.append(f'lean_chart{tags},chart=Strategy\\ Equity,series=Equity value={equity} {ts}')
            
            # lean_portfolio
            gross_return = row.get("gross_return", row.get("daily_return", "0"))
            net_return = row.get("net_return", gross_return)
            lines.append(
                f'lean_portfolio{tags} cash={cash},total_holdings_value={invested},'
                f'total_portfolio_value={equity},gross_return={gross_return},'
                f'net_return={net_return},holdings_count={holdings_count}i {ts}'
            )
            
            # lean_metric: runtime metrics from daily CSV
            for metric_key in ["turnover", "kelly_scale", "effective_exposure",
                               "exposure_scale", "sharpe_annualized", "avg_ic", "avg_ir",
                               "vol_target_scale", "eligible_symbols", "selected_symbols",
                               "stop_loss_exits", "trailing_stop_exits", "score_spread"]:
                val = row.get(metric_key, "")
                if val:
                    cat = "runtime" if metric_key in ("kelly_scale", "effective_exposure",
                                                       "exposure_scale", "sharpe_annualized") else "summary"
                    lines.append(
                        f'lean_metric{tags},category={cat},metric={esc(metric_key)} '
                        f'numeric_value={val} {ts}'
                    )

    return write_influx(lines, dry_run=dry_run)


def export_factor_exposure(csv_path: str, version: str, mode: str, dry_run: bool = False) -> int:
    """Export factor exposure data to both barra-specific and shared measurements."""
    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]
    
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_factor_exposure"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = int(parse_date(row["trade_date"]).timestamp())

            # === Barra-specific measurement ===
            fields = []
            holdings = row.get("holdings", "0")
            fields.append(f'holdings={int(float(holdings))}i')
            
            for factor in BARRA_FACTORS:
                val = row.get(factor, "")
                if val:
                    fields.append(f'{factor}={val}')
            
            if fields:
                lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')

            # === Shared measurement: lean_family_exposure ===
            # Map Barra factors to family names
            family_fields = [f'holdings_count={holdings}i']
            
            # Initialize all families to 0
            family_values = {family: 0.0 for family in FAMILY_NAMES}
            
            # Map Barra factors to families
            for factor in BARRA_FACTORS:
                val = row.get(factor, "0")
                try:
                    val_float = float(val)
                except:
                    val_float = 0.0
                
                family_name = BARRA_TO_FAMILY_MAP.get(factor)
                if family_name and family_name in family_values:
                    family_values[family_name] = val_float
            
            # Build fields string
            for family, val in family_values.items():
                family_fields.append(f'{family}={val}')
            
            lines.append(f'lean_family_exposure{tags} {",".join(family_fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_trades(csv_path: str, version: str, mode: str, dry_run: bool = False) -> int:
    """Export trade data to both barra-specific and shared measurements."""
    if not csv_path or not Path(csv_path).exists():
        return 0

    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]

    lines: list[str] = []
    tags_base = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_trades"

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
            quantity = row.get("quantity", "")
            if quantity:
                fields.append(f'quantity={int(float(quantity))}i')

            for k in ["price", "trade_value", "fee", "score"]:
                val = row.get(k, "")
                if val:
                    fields.append(f'{k}={val}')

            reason = row.get("reason", "")
            if reason:
                fields.append(f'reason="{esc(reason)}"')

            if fields:
                # Barra-specific measurement
                lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')
                # Shared measurement
                lines.append(f'lean_mf_order{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def find_order_events_json(version: str) -> Optional[str]:
    """Find LEAN order-events.json file."""
    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    repo_root = Path(__file__).resolve().parents[1]

    path = repo_root / "Results" / f"{algorithm_id}-order-events.json"
    if path.exists():
        return str(path)
    return None


def export_trades_from_order_events(json_path: str, version: str, mode: str,
                                    dry_run: bool = False) -> int:
    """Export trade data from LEAN order-events.json to both barra-specific and shared measurements."""
    if not json_path or not Path(json_path).exists():
        return 0

    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]

    with open(json_path, encoding="utf-8") as f:
        events = json.load(f)

    # Only process filled orders
    filled = [ev for ev in events if ev.get("status") == "filled"]
    if not filled:
        return 0

    lines: list[str] = []
    tags_base = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_trades"

    # Also read the results JSON to get order tags (score/reason)
    repo_root = Path(__file__).resolve().parents[1]
    results_json = repo_root / "Results" / f"{algorithm_id}.json"
    order_tags = {}
    if results_json.exists():
        with open(results_json, encoding="utf-8") as f:
            results = json.load(f)
        orders = results.get("orders", {})
        for oid_str, order in orders.items():
            tag = order.get("tag", "")
            order_tags[int(oid_str)] = tag

    for ev in filled:
        ts = int(ev["time"])
        symbol = esc(ev.get("symbolPermtick", ev.get("symbolValue", "")))
        direction = ev.get("direction", "buy")
        action = "BUY" if direction == "buy" else "SELL"

        tags = f'{tags_base},symbol={symbol},action={action}'

        fields = []

        quantity = ev.get("fillQuantity", 0)
        abs_qty = abs(int(quantity))
        fields.append(f'quantity={abs_qty}i')

        fill_price = ev.get("fillPrice", 0)
        if fill_price:
            fields.append(f'price={fill_price}')

        trade_value = abs_qty * fill_price
        fields.append(f'trade_value={trade_value}')

        fee = ev.get("orderFeeAmount", 0)
        if fee:
            fields.append(f'fee={fee}')

        # Extract score and reason from order tag
        order_id = ev.get("orderId", 0)
        tag = order_tags.get(order_id, "")
        score = 0.0
        if tag:
            import re
            m = re.search(r'score=([0-9.]+)', tag)
            if m:
                score = float(m.group(1))
            reason = tag.split(' ')[0] if tag else ""
        else:
            reason = ""

        fields.append(f'score={score}')
        if reason:
            fields.append(f'reason="{esc(reason)}"')

        if fields:
            lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')
            lines.append(f'lean_mf_order{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_allocation(csv_path: str, version: str, mode: str, dry_run: bool = False) -> int:
    """Export allocation data to barra-specific measurement."""
    if not csv_path or not Path(csv_path).exists():
        return 0
    
    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]
    
    lines: list[str] = []
    tags_base = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_allocation"

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
            weight = row.get("weight", "")
            if weight:
                fields.append(f'weight={weight}')

            quantity = row.get("quantity", "")
            if quantity:
                fields.append(f'quantity={int(float(quantity))}i')

            for k in ["price", "market_price", "market_value", "score"]:
                val = row.get(k, "")
                if val:
                    fields.append(f'{k}={val}')

            if fields:
                lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_monte_carlo(csv_path: str, version: str, mode: str, dry_run: bool = False) -> int:
    """Export Monte Carlo simulation results to both barra-specific and shared measurements."""
    if not csv_path or not Path(csv_path).exists():
        return 0

    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]

    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"
    barra_measurement = f"{csv_prefix.replace('-', '_')}_monte_carlo"

    mc_data = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stat = row.get("statistic", "").strip().lower()
            if stat:
                mc_data[stat] = row

    if not mc_data:
        return 0

    ts = int(datetime.now(tz=timezone.utc).timestamp())

    # === Barra-specific measurement (wide format) ===
    fields = []

    tv = mc_data.get("terminal_value", {})
    for pct, col in [("p05", "p05"), ("p25", "p25"), ("p50", "p50"),
                     ("p75", "p75"), ("p95", "p95")]:
        val = tv.get(col, "")
        if val:
            fields.append(f'terminal_value_{pct}={val}')

    md = mc_data.get("max_drawdown", {})
    for pct, col in [("p05", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = md.get(col, "")
        if val:
            fields.append(f'max_drawdown_{pct}={val}')

    sr = mc_data.get("sharpe_ratio", {})
    for pct, col in [("p05", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = sr.get(col, "")
        if val:
            fields.append(f'sharpe_{pct}={val}')

    if fields:
        lines.append(f'{barra_measurement}{tags} {",".join(fields)} {ts}')

    # === Shared measurement: lean_monte_carlo (normalized key-value format) ===
    # Maps to the same schema used by export_backtest_results_to_influx.py
    mc_tags_base = f',algorithm_id={esc(algorithm_id)},mode={esc(mode)},source=algorithm_monte_carlo,run_id=backtest'

    # Terminal value percentiles
    tv = mc_data.get("terminal_value", {})
    for pct_label, col in [("p5", "p05"), ("p25", "p25"), ("p50", "p50"),
                            ("p75", "p75"), ("p95", "p95")]:
        val = tv.get(col, "")
        if val:
            metric = f"terminal_value_{pct_label}"
            lines.append(f'lean_monte_carlo{mc_tags_base},metric={esc(metric)},unit=ratio numeric_value={val} {ts}')

    # Max drawdown percentiles
    md = mc_data.get("max_drawdown", {})
    for pct_label, col in [("p5", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = md.get(col, "")
        if val:
            metric = f"max_drawdown_{pct_label}"
            lines.append(f'lean_monte_carlo{mc_tags_base},metric={esc(metric)},unit=percent numeric_value={float(val)*100} {ts}')

    # Sharpe ratio percentiles
    sr = mc_data.get("sharpe_ratio", {})
    for pct_label, col in [("p5", "p05"), ("p50", "p50"), ("p95", "p95")]:
        val = sr.get(col, "")
        if val:
            metric = f"sharpe_{pct_label}"
            lines.append(f'lean_monte_carlo{mc_tags_base},metric={esc(metric)},unit=ratio numeric_value={val} {ts}')

    # Derived: loss probability (terminal_value_p5 < 1 means loss)
    tv_p5 = tv.get("p05", "")
    if tv_p5:
        loss_prob = 1.0 if float(tv_p5) < 1.0 else 0.0
        lines.append(f'lean_monte_carlo{mc_tags_base},metric=baseline_loss_probability,unit=percent numeric_value={loss_prob*100} {ts}')

    return write_influx(lines, dry_run=dry_run)


def export_backtest_stats(stats: dict, version: str, mode: str, ts: int,
                          dry_run: bool = False) -> int:
    """Write LEAN-computed summary statistics to shared measurements."""
    if not stats:
        return 0

    config = VERSION_CONFIG[version]
    algorithm_id = config["algorithm_id"]
    csv_prefix = config["csv_prefix"]
    
    lines: list[str] = []
    tags = f",algorithm_id={esc(algorithm_id)},mode={esc(mode)}"

    # Write all statistics as lean_metric points
    for metric_name, value in stats.items():
        lines.append(
            f'lean_metric{tags},category=summary,metric={esc(metric_name)} '
            f'numeric_value={float(value)} {ts}'
        )

    # Write lean_portfolio_statistics
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
        if fk in ("closed_trades", "total_orders", "drawdown_recovery"):
            pfields.append(f'{fk}={int(float(fv))}i')
        else:
            pfields.append(f'{fk}={float(fv)}')

    if pfields:
        lines.append(f'lean_portfolio_statistics{tags} {",".join(pfields)} {ts}')

    # Also write barra-specific stats measurement
    barra_measurement = f"{csv_prefix.replace('-', '_')}_stats"
    barra_fields = []
    for fk, fv in field_map.items():
        if fv is None:
            continue
        if fk in ("total_orders",):
            barra_fields.append(f'{fk}={int(float(fv))}i')
        else:
            barra_fields.append(f'{fk}={float(fv)}')
    
    if barra_fields:
        lines.append(f'{barra_measurement}{tags} {",".join(barra_fields)} {ts}')

    return write_influx(lines, dry_run=dry_run)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Export Barra CNE5 CSV data to InfluxDB (unified)"
    )
    parser.add_argument(
        "--version", required=True, choices=["v2", "v2_1", "v3", "v3_2"],
        help="Barra CNE5 version"
    )
    parser.add_argument(
        "--mode", required=True, choices=["backtesting", "live"],
        help="backtesting or live"
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Custom data directory"
    )
    parser.add_argument(
        "--oos", action="store_true",
        help="Use OOS (out-of-sample) files (only for v2)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print lines instead of writing to InfluxDB"
    )
    args = parser.parse_args()

    config = VERSION_CONFIG[args.version]
    csv_prefix = config["csv_prefix"]
    
    # Determine file paths
    suffix = "-oos" if args.oos else ""
    daily_summary_path = find_csv_path(
        f"{csv_prefix}{suffix}-daily-summary.csv", args.data_dir
    )
    factor_exposure_path = find_csv_path(
        f"{csv_prefix}{suffix}-factor-exposure.csv", args.data_dir
    )
    monte_carlo_path = find_csv_path(
        f"{csv_prefix}{suffix}-monte-carlo.csv", args.data_dir
    )
    trades_path = find_csv_path(
        f"{csv_prefix}{suffix}-trades.csv", args.data_dir
    )
    allocation_path = find_csv_path(
        f"{csv_prefix}{suffix}-allocation.csv", args.data_dir
    )
    summary_json_path = find_summary_json(args.version)

    results = {}
    errors = []

    # Export daily summary
    if daily_summary_path:
        print(f"Exporting daily summary from: {daily_summary_path}")
        results["daily_summary"] = export_daily_summary(
            daily_summary_path, args.version, args.mode, args.dry_run
        )
    else:
        errors.append("daily-summary CSV not found")

    # Export factor exposure
    if factor_exposure_path:
        print(f"Exporting factor exposure from: {factor_exposure_path}")
        results["factor_exposure"] = export_factor_exposure(
            factor_exposure_path, args.version, args.mode, args.dry_run
        )
    else:
        errors.append("factor-exposure CSV not found")

    # Export Monte Carlo
    if monte_carlo_path:
        print(f"Exporting Monte Carlo from: {monte_carlo_path}")
        results["monte_carlo"] = export_monte_carlo(
            monte_carlo_path, args.version, args.mode, args.dry_run
        )
    else:
        if args.mode == "backtesting":
            errors.append("monte-carlo CSV not found")

    # Export trades
    if trades_path:
        print(f"Exporting trades from: {trades_path}")
        results["trades"] = export_trades(
            trades_path, args.version, args.mode, args.dry_run
        )
    else:
        # Fallback: read from LEAN order-events.json
        order_events_path = find_order_events_json(args.version)
        if order_events_path:
            print(f"Exporting trades from order events: {order_events_path}")
            results["trades"] = export_trades_from_order_events(
                order_events_path, args.version, args.mode, args.dry_run
            )
        # Trades are optional

    # Export allocation
    if allocation_path:
        print(f"Exporting allocation from: {allocation_path}")
        results["allocation"] = export_allocation(
            allocation_path, args.version, args.mode, args.dry_run
        )
    # Allocation is optional

    # Export LEAN summary stats for backtesting mode
    if args.mode == "backtesting" and summary_json_path:
        print(f"Exporting backtest stats from: {summary_json_path}")
        stats = read_lean_summary_stats(summary_json_path)
        if stats and daily_summary_path:
            with open(daily_summary_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                last_ts = int(parse_date(rows[-1]["trade_date"]).timestamp()) if rows else 0
            results["backtest_stats"] = export_backtest_stats(
                stats, args.version, args.mode, last_ts, args.dry_run
            )

    output = {
        "version": args.version,
        "mode": args.mode,
        "algorithm_id": config["algorithm_id"],
        "oos": args.oos,
        "results": results,
    }
    if errors:
        output["errors"] = errors

    print(json.dumps(output, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
