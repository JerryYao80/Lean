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
]


def parse_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y%m%d").replace(
        hour=15, tzinfo=timezone.utc
    )


def esc(s: str) -> str:
    return s.replace(" ", "\\ ").replace(",", "\\,")


def write_influx(lines: list[str], dry_run: bool = False) -> int:
    if not lines:
        return 0
    if dry_run:
        for line in lines[:5]:
            print(line)
        print(f"... ({len(lines)} total)")
        return len(lines)
    body = "\n".join(lines)
    url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=s"
    req = request.Request(url, data=body.encode(), method="POST")
    req.add_header("Authorization", f"Token {INFLUX_TOKEN}")
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    with request.urlopen(req, timeout=30) as resp:
        if resp.status >= 300:
            print(f"Write failed: {resp.status} {resp.read().decode()[:200]}", file=sys.stderr)
            return 0
    return len(lines)


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
                               "stop_loss_exits", "trailing_stop_exits"]:
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = {}
    results["daily_summary"] = export_daily_summary(args.daily_summary, args.algorithm_id, args.mode, args.dry_run)
    results["family_exposure"] = export_family_exposure(args.family_exposure, args.algorithm_id, args.mode, args.dry_run)
    results["trades"] = export_trades(args.trades, args.algorithm_id, args.mode, args.dry_run)

    print(json.dumps({"mode": args.mode, "results": results}))


if __name__ == "__main__":
    import json
    main()
