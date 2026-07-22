"""Bridge: export Barra CNE5 V3.2 live data to InfluxDB for dashboard panels.

Reads V3.2's runtime metrics (FX.* factors, Exposure, Return, etc.) from lean_metric,
equity from lean_chart, and order data from lean_order_event, then writes to
barra_cne5_daily, barra_cne5_factor_exposure, barra_cne5_holding_detail,
barra_cne5_trades, and barra_cne5_decay
so the Live Paper Trading dashboard panels display data.

Run alongside the V3.2 live paper process.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from urllib import request
from urllib.parse import quote

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASS = os.environ.get("GRAFANA_ADMIN_PASS", "")

ALGORITHM_ID = os.environ.get("BARRA_V3_2_ALGORITHM_ID", "AShareBarraCNE5V3_2Algorithm")
MODE = "live"


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
    """Execute InfluxQL query via InfluxDB v1 compatibility API and return results."""
    url = f"{INFLUX_URL}/query?db={INFLUX_BUCKET}&q={quote(query)}"
    req = request.Request(url)
    req.add_header("Authorization", f"Token {INFLUX_TOKEN}")
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


def query_influxql_with_tags(query: str) -> list[dict]:
    """Execute InfluxQL query and return results with tags included as _tag_* keys."""
    url = f"{INFLUX_URL}/query?db={INFLUX_BUCKET}&q={quote(query)}"
    req = request.Request(url)
    req.add_header("Authorization", f"Token {INFLUX_TOKEN}")
    try:
        with request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            results = []
            for r in data.get("results", []):
                for s in r.get("series", []):
                    cols = s.get("columns", [])
                    tags = s.get("tags", {})
                    for v in s.get("values", []):
                        row = dict(zip(cols, v))
                        for tk, tv in tags.items():
                            row[f"_tag_{tk}"] = tv
                        results.append(row)
            return results
    except Exception as e:
        print(f"Query error: {e}", file=sys.stderr)
        return []


def export_holding_detail() -> int:
    """Read lean_holding + lean_order_event, compute derived fields, write to barra_cne5_holding_detail."""
    holding_rows = query_influxql_with_tags(
        f'SELECT last("quantity") AS "quantity", last("average_price") AS "avg_price", '
        f'last("market_price") AS "market_price", last("holdings_value") AS "value", '
        f'last("unrealized_profit") AS "pnl", last("unrealized_profit_percent") AS "pnl_pct" '
        f'FROM "lean_holding" WHERE time > 0 AND "algorithm_id" = \'{ALGORITHM_ID}\' '
        f'AND "mode" = \'live\' AND "quantity" > 0 GROUP BY "symbol"'
    )
    if not holding_rows:
        return 0

    fee_rows = query_influxql_with_tags(
        f'SELECT SUM("fee_amount") AS "total_fees" FROM "lean_order_event" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "status" = \'Filled\' GROUP BY "symbol"'
    )
    fees_by_symbol = {}
    for row in fee_rows:
        sym = row.get("_tag_symbol", "")
        val = row.get("total_fees")
        if sym and val is not None:
            fees_by_symbol[sym] = float(val)

    sell_rows = query_influxql_with_tags(
        f'SELECT "fill_quantity", "fill_price" FROM "lean_order_event" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "status" = \'Filled\' AND "direction" = \'Sell\' GROUP BY "symbol"'
    )
    sale_vol_by_symbol: dict[str, float] = {}
    for row in sell_rows:
        sym = row.get("_tag_symbol", "")
        qty = row.get("fill_quantity")
        price = row.get("fill_price")
        if sym and qty is not None and price is not None:
            sale_vol_by_symbol[sym] = sale_vol_by_symbol.get(sym, 0.0) + abs(float(qty)) * float(price)

    ts = int(datetime.now(tz=timezone.utc).timestamp())
    lines: list[str] = []
    tags_base = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"

    for row in holding_rows:
        symbol = row.get("_tag_symbol", "")
        if not symbol:
            continue

        qty = float(row.get("quantity", 0))
        avg_price = float(row.get("avg_price", 0))
        market_price = float(row.get("market_price", 0))
        value = float(row.get("value", 0))
        pnl = float(row.get("pnl", 0))
        pnl_pct = float(row.get("pnl_pct", 0))

        holdings_cost = avg_price * abs(qty)
        absolute_quantity = abs(qty)
        leverage = 1.0
        total_fees = fees_by_symbol.get(symbol, 0.0)
        total_sale_volume = sale_vol_by_symbol.get(symbol, 0.0)
        total_dividends = 0.0
        net_profit = pnl - total_fees

        tags = f"{tags_base},symbol={esc(symbol)}"
        fields = ",".join([
            f"quantity={int(qty)}i",
            f"absolute_quantity={int(absolute_quantity)}i",
            f"average_price={avg_price}",
            f"market_price={market_price}",
            f"holdings_value={value}",
            f"holdings_cost={holdings_cost}",
            f"unrealized_profit={pnl}",
            f"unrealized_profit_percent={pnl_pct}",
            f"net_profit={net_profit}",
            f"total_fees={total_fees}",
            f"total_dividends={int(total_dividends)}i",
            f"total_sale_volume={total_sale_volume}",
            f"leverage={leverage}"
        ])
        lines.append(f"barra_cne5_holding_detail{tags} {fields} {ts}")

    return write_influx(lines)


def export_daily_stats() -> int:
    """Read V3.2 runtime metrics and write to barra_cne5_daily for dashboard panels."""
    equity_result = query_influxql(
        f'SELECT last("value") FROM "lean_chart" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "chart" = \'Strategy Equity\' AND "series" = \'Equity\''
    )
    equity = 0.0
    if equity_result and equity_result[0].get("last") is not None:
        equity = float(equity_result[0]["last"])

    drawdown = 0.0
    if equity > 0:
        peak_result = query_influxql(
            f'SELECT max("value") FROM "lean_chart" '
            f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
            f'AND "chart" = \'Strategy Equity\' AND "series" = \'Equity\''
        )
        if peak_result and peak_result[0].get("max") is not None:
            peak = float(peak_result[0]["max"])
            if peak > 0:
                drawdown = (equity - peak) / peak * 100.0

    metrics_to_get = ["Return", "Exposure", "Target Exp", "Regime", "VolScale"]
    metric_values = {}
    for m in metrics_to_get:
        result = query_influxql(
            f'SELECT last("numeric_value") FROM "lean_metric" '
            f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
            f'AND "metric" = \'{m}\''
        )
        if result and result[0].get("last") is not None:
            metric_values[m] = float(result[0]["last"])

    holdings_rows = query_influxql_with_tags(
        f'SELECT last("quantity") AS "quantity" FROM "lean_holding" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "quantity" > 0 GROUP BY "symbol"'
    )
    holdings_count = len(holdings_rows)
    total_shares = sum(int(float(r.get("quantity", 0))) for r in holdings_rows)

    ts = int(datetime.now(tz=timezone.utc).timestamp())
    tags = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"

    fields = [f"equity={equity}", f"drawdown={drawdown}"]
    if "Return" in metric_values:
        fields.append(f"gross_return={metric_values['Return']}")
    fields.append(f"holdings={holdings_count}i")
    fields.append(f"total_shares={total_shares}i")
    if "Exposure" in metric_values:
        fields.append(f"exposure_scale={metric_values['Exposure']}")
    if "Target Exp" in metric_values:
        fields.append(f"effective_exposure={metric_values['Target Exp']}")

    line = f"barra_cne5_daily{tags} {','.join(fields)} {ts}"
    return write_influx([line])


def export_factor_exposure() -> int:
    """Read V3.2 FX.* runtime metrics and write to barra_cne5_factor_exposure."""
    factor_values = {}
    for fx_name in ["beta", "momentum", "size", "earnyld", "resvol", "growth",
                    "btop", "leverage", "liquidity", "nlsize", "moneyflow",
                    "quality", "northbound", "margin", "chipcost"]:
        metric_name = f"FX.{fx_name}"
        result = query_influxql(
            f'SELECT last("numeric_value") FROM "lean_metric" '
            f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
            f'AND "metric" = \'{metric_name}\''
        )
        if result and result[0].get("last") is not None:
            factor_values[fx_name] = float(result[0]["last"])

    holdings_rows = query_influxql(
        f'SELECT COUNT("quantity") FROM "lean_holding" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "quantity" > 0 GROUP BY "symbol"'
    )
    holdings_count = len(holdings_rows)

    ts = int(datetime.now(tz=timezone.utc).timestamp())
    tags = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"

    fields = [f"holdings={holdings_count}i"]
    for fx_name, val in factor_values.items():
        fields.append(f"{fx_name}={val}")

    line = f"barra_cne5_factor_exposure{tags} {','.join(fields)} {ts}"
    return write_influx([line])


def export_trades() -> int:
    """Read V3.2 order events and write to barra_cne5_trades."""
    rows = query_influxql(
        f'SELECT "symbol", "direction", "fill_quantity", "fill_price", "fee_amount", "status", "order_id", "event_id" '
        f'FROM "lean_order_event" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'AND "status" = \'Filled\' '
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
            fields.append(f"quantity={int(abs(float(qty)))}i")
        price = row.get("fill_price")
        if price is not None:
            fields.append(f"price={float(price)}")
            qty_val = abs(float(qty)) if qty else 0
            fields.append(f"trade_value={float(price) * qty_val}")
        fee = row.get("fee_amount")
        if fee is not None:
            fields.append(f"fee={float(fee)}")

        if fields:
            order_id = row.get("order_id", 0)
            try:
                ts = int(float(order_id)) + 1700000000
            except (ValueError, TypeError):
                ts = int(datetime.now(tz=timezone.utc).timestamp())
            lines.append(f"barra_cne5_trades{tags} {','.join(fields)} {ts}")

    return write_influx(lines)


def export_decay() -> int:
    """Compute rolling performance and decay metrics from equity curve."""
    rows = query_influxql(
        f'SELECT "equity" FROM "barra_cne5_daily" '
        f'WHERE "algorithm_id" = \'{ALGORITHM_ID}\' AND "mode" = \'live\' '
        f'ORDER BY time DESC LIMIT 720'
    )
    if not rows or len(rows) < 10:
        return 0

    series = []
    for row in reversed(rows):
        eq = row.get("equity")
        t = row.get("time")
        if eq is not None and t is not None:
            series.append((t, float(eq)))

    if len(series) < 10:
        return 0

    returns = []
    for i in range(1, len(series)):
        prev = max(series[i - 1][1], 0.01)
        curr = max(series[i][1], 0.01)
        returns.append(math.log(curr / prev))

    if not returns:
        return 0

    def _sharpe(rets: list[float]) -> float:
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        if var < 1e-12:
            return 0.0
        return mean / math.sqrt(var) * math.sqrt(60480)

    def _cum_return(rets: list[float]) -> float:
        if not rets:
            return 0.0
        return (math.exp(sum(rets)) - 1.0) * 100.0

    short = min(60, len(returns))
    medium = min(360, len(returns))
    long = len(returns)

    sharpe_short = _sharpe(returns[-short:])
    sharpe_medium = _sharpe(returns[-medium:])
    sharpe_long = _sharpe(returns[-long:])

    ret_short = _cum_return(returns[-short:])
    ret_medium = _cum_return(returns[-medium:])
    ret_long = _cum_return(returns[-long:])

    sharpe_decay = sharpe_short - sharpe_long
    return_decay = ret_short - ret_long

    equities = [s[1] for s in series]
    current_eq = equities[-1]
    peak_eq = max(equities)
    current_dd = (current_eq - peak_eq) / peak_eq * 100.0 if peak_eq > 0 else 0.0

    max_dd = 0.0
    running_peak = equities[0]
    for eq in equities:
        running_peak = max(running_peak, eq)
        dd = (eq - running_peak) / running_peak * 100.0 if running_peak > 0 else 0.0
        max_dd = min(max_dd, dd)

    ts = int(datetime.now(tz=timezone.utc).timestamp())
    tags = f",algorithm_id={esc(ALGORITHM_ID)},mode={esc(MODE)}"

    fields = ",".join([
        f"sharpe_1h={sharpe_short:.4f}",
        f"sharpe_6h={sharpe_medium:.4f}",
        f"sharpe_all={sharpe_long:.4f}",
        f"return_1h={ret_short:.4f}",
        f"return_6h={ret_medium:.4f}",
        f"return_all={ret_long:.4f}",
        f"sharpe_decay={sharpe_decay:.4f}",
        f"return_decay={return_decay:.4f}",
        f"drawdown={current_dd:.4f}",
        f"max_drawdown={max_dd:.4f}",
    ])
    line = f"barra_cne5_decay{tags} {fields} {ts}"
    return write_influx([line])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Bridge V3.2 live data to dashboard measurements")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default 300)")
    args = parser.parse_args()

    if args.once:
        hd = export_holding_detail()
        daily = export_daily_stats()
        fe = export_factor_exposure()
        tr = export_trades()
        dc = export_decay()
        print(json.dumps({"holding_detail": hd, "daily": daily, "factor_exposure": fe, "trades": tr, "decay": dc}))
        return

    while True:
        try:
            hd = export_holding_detail()
            daily = export_daily_stats()
            fe = export_factor_exposure()
            tr = export_trades()
            dc = export_decay()
            if daily or fe or tr or hd or dc:
                print(f"{datetime.now():%Y-%m-%d %H:%M:%S} holding_detail={hd} daily={daily} factor_exposure={fe} trades={tr} decay={dc}", flush=True)
            else:
                print(f"{datetime.now():%Y-%m-%d %H:%M:%S} no new data", flush=True)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
