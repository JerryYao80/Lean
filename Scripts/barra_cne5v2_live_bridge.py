"""Bridge: export Barra CNE5 V2 live factor exposure and trades to InfluxDB.

Reads V2's factor exposure from lean_metric (runtime FX.* metrics) and
order data from lean_order_event, then writes to lean_family_exposure and
lean_mf_order so the Live Paper Trading dashboard panels display data.

Run alongside the V2 live paper process.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib import request
from urllib.parse import quote

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASS = os.environ.get("GRAFANA_ADMIN_PASS", "")

ALGORITHM_ID = os.environ.get("BARRA_V2_ALGORITHM_ID", "AShareBarraCNE5V2Algorithm")
MODE = "live"

# Mapping from V2 FX.* metric suffix to dashboard's lean_family_exposure column names
FACTOR_MAP = {
    "beta": "barra_beta",
    "momentum": "barra_momentum",
    "size": "barra_value",
    "earnyld": "earnings_surprise",
    "resvol": "barra_resvol",
    "growth": "multi_factor",
    "btop": "value_quality",
    "leverage": "margin_signal",
    "liquidity": "barra_liquidity",
    "nlsize": "barra_nlsize",
    "moneyflow": "money_flow",
    "quality": "barra_quality",
    "northbound": "northbound_flow",
    "margin": "margin_signal",
    "chipcost": "chip_cost",
}

ZERO_FIELDS = [
    "momentum_reversal", "etf_premium", "sector_rotation",
    "low_volatility", "size_tilt", "liquidity_premium",
    "chip_concentration", "rate_sensitivity", "basis_sentiment",
    "options_pcr", "margin_short_ratio", "macro_rate", "analyst_signal",
]


def esc(s: str) -> str:
    return s.replace(" ", "\\ ").replace(",", "\\,")


def write_influx(lines: list[str]) -> int:
    if not lines:
        return 0
    total = 0
    for i in range(0, len(lines), 5000):
        batch = lines[i:i + 5000]
        body = "\n".join(batch)
        url = f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=s"
        req = request.Request(url, data=body.encode(), method="POST")
        req.add_header("Authorization", f"Token {INFLUX_TOKEN}")
        req.add_header("Content-Type", "text/plain; charset=utf-8")
        with request.urlopen(req, timeout=30) as resp:
            if resp.status >= 300:
                print(f"Write failed: {resp.status}", file=sys.stderr)
                return total
        total += len(batch)
    return total


def query_influxql(query: str) -> list[dict]:
    """Execute InfluxQL query via Grafana proxy and return results."""
    url = f"{GRAFANA_URL}/api/datasources/proxy/1/query?db={INFLUX_BUCKET}&q={quote(query)}"
    req = request.Request(url)
    req.add_header("Authorization", f"Basic {__import__('base64').b64encode(f'{GRAFANA_USER}:{GRAFANA_PASS}'.encode()).decode()}")
    try:
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            results = []
            for r in data.get("results", []):
                for s in r.get("series", []):
                    cols = s.get("columns", [])
                    for v in s.get("values", []):
                        results.append(dict(zip(cols, v)))
            return results
    except Exception as e:
        print(f"Query error: {e}", file=sys.stderr)
        return []


def export_factor_exposure_from_metrics() -> int:
    """Read V2 FX.* runtime metrics from lean_metric and write to lean_family_exposure."""
    rows = query_influxql(
        f'SELECT last("numeric_value") FROM "lean_metric" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "category" = \'runtime\' AND "metric" =~ /^FX\\./ '
        f'GROUP BY "metric"'
    )
    if not rows:
        return 0

    # Build a dict of factor_name -> value
    factor_values = {}
    for row in rows:
        # We need the metric tag value, not in the row dict
        pass

    # Re-query with explicit metric names
    factor_values = {}
    for fx_name in FACTOR_MAP.keys():
        metric_name = f"FX.{fx_name}"
        result = query_influxql(
            f'SELECT last("numeric_value") FROM "lean_metric" '
            f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
            f'AND "metric" = \'{metric_name}\''
        )
        if result and result[0].get("last") is not None:
            factor_values[fx_name] = float(result[0]["last"])

    if not factor_values:
        return 0

    # Also get holdings count
    holdings_result = query_influxql(
        f'SELECT last("numeric_value") FROM "lean_metric" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "metric" = \'Holdings\''
    )
    holdings_count = 0
    if holdings_result and holdings_result[0].get("last") is not None:
        try:
            holdings_count = int(float(holdings_result[0]["last"]))
        except (ValueError, TypeError):
            pass

    ts = int(datetime.now(tz=timezone.utc).timestamp())
    tags = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"
    fields = [f"holdings_count={holdings_count}i"]

    for v2_name, dashboard_name in FACTOR_MAP.items():
        val = factor_values.get(v2_name, 0)
        fields.append(f"{dashboard_name}={val}")

    for zf in ZERO_FIELDS:
        fields.append(f"{zf}=0")

    line = f"lean_family_exposure{tags} {','.join(fields)} {ts}"
    return write_influx([line])


def export_trades_from_order_events() -> int:
    """Read V2 order events from lean_order_event and write to lean_mf_order."""
    rows = query_influxql(
        f'SELECT "symbol", "direction", "fill_quantity", "fill_price", "order_fee" '
        f'FROM "lean_order_event" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'ORDER BY time DESC LIMIT 100'
    )
    if not rows:
        return 0

    lines: list[str] = []
    tags_base = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"

    for row in rows:
        symbol = esc(str(row.get("symbol", "")))
        action = "BUY" if row.get("direction") == "Buy" else "SELL"
        tags = f"{tags_base},symbol={symbol},action={action}"

        fields = []
        qty = row.get("fill_quantity")
        if qty is not None:
            fields.append(f"quantity={int(float(qty))}i")
        price = row.get("fill_price")
        if price is not None:
            fields.append(f"price={float(price)}")
        fee = row.get("order_fee")
        if fee is not None:
            fields.append(f"fee={float(fee)}")

        if fields:
            ts = int(datetime.now(tz=timezone.utc).timestamp())
            lines.append(f"lean_mf_order{tags} {','.join(fields)} {ts}")

    return write_influx(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Bridge V2 live data to dashboard measurements")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default 300)")
    args = parser.parse_args()

    if args.once:
        fe = export_factor_exposure_from_metrics()
        tr = export_trades_from_order_events()
        print(json.dumps({"factor_exposure": fe, "trades": tr}))
        return

    while True:
        try:
            fe = export_factor_exposure_from_metrics()
            tr = export_trades_from_order_events()
            if fe or tr:
                print(f"{datetime.now():%Y-%m-%d %H:%M:%S} factor_exposure={fe} trades={tr}")
            else:
                print(f"{datetime.now():%Y-%m-%d %H:%M:%S} no new data")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
