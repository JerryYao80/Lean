"""review_* InfluxDB line-protocol export. Spec §4.2. Reuses InfluxPoint + write_lines_to_influx
from Scripts/export_backtest_results_to_influx.py for escaping + HTTP write."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "Scripts"))
from export_backtest_results_to_influx import (  # noqa: E402
    InfluxPoint, point_to_line_protocol, write_lines_to_influx,
)


def _ts_to_ns(iso: str) -> int:
    """ISO-8601 string → nanoseconds since epoch."""
    s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
    dt = datetime.fromisoformat(s[:19])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def build_lines(review_doc: dict, algorithm_id: str, mode: str, run_id: str) -> list:
    """Build InfluxDB line-protocol lines for the 4 review_* measurements."""
    lines = []
    end_iso = review_doc["run_meta"].get("period_end") or datetime.now(timezone.utc).isoformat()
    end_ns = _ts_to_ns(end_iso)

    for layer, v in review_doc.get("layer_attribution", {}).items():
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_layer_attribution",
            tags={"algorithm_id": algorithm_id, "layer": layer, "mode": mode, "run_id": run_id},
            fields={"pnl_abs": float(v["pnl_abs"]), "pnl_pct_of_total": float(v["pnl_pct_of_total"]),
                    "n_trades": int(v["n_trades"]), "n_wins": int(v["n_wins"]),
                    "contribution_to_total_return": float(v["contribution_to_total_return"])},
            timestamp_ns=end_ns,
        )))

    for t in review_doc.get("per_trade_narrative", []):
        fields = {"pnl": float(t["pnl"]), "mae": float(t.get("mae", 0)), "mfe": float(t.get("mfe", 0)),
                  "days_held": int(t.get("days_held", 0))}
        for ln, contrib in t.get("layer_contributions", {}).items():
            fields[f"{ln}_contrib"] = float(contrib)
        regime = t.get("regime_at_entry", {}) or {}
        regime_tag = str(regime.get("regime", "unknown"))
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_trade",
            tags={"algorithm_id": algorithm_id, "symbol": t["symbol"], "regime_at_entry": regime_tag,
                  "direction": t["direction"], "run_id": run_id, "mode": mode},
            fields=fields,
            timestamp_ns=_ts_to_ns(t["exit_time"]),
        )))

    for d in review_doc.get("drawdown_attribution", []):
        if d.get("top_contributing_layers"):
            top_layer = d["top_contributing_layers"][0]["layer"]
        else:
            top_layer = d.get("top_layer", "none")
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_drawdown",
            tags={"algorithm_id": algorithm_id, "run_id": run_id, "mode": mode,
                  "regime_at_trough": str(d.get("regime_during") or "unknown")},
            fields={"depth_pct": float(d["depth_pct"]), "top_layer": top_layer,
                    "duration_days": int(d.get("duration_days", 0))},
            timestamp_ns=_ts_to_ns(d["trough_time"]),
        )))

    tca = review_doc.get("tca")
    if tca:
        lines.append(point_to_line_protocol(InfluxPoint(
            measurement="review_tca",
            tags={"algorithm_id": algorithm_id, "run_id": run_id, "mode": mode},
            fields={"avg_slippage_bps": float(tca["avg_slippage_bps"] or 0),
                    "fill_quality_score": float(tca["fill_quality_score"] or 0),
                    "n_fills": int(tca["n_fills"])},
            timestamp_ns=end_ns,
        )))
    return lines


def export(review_doc: dict, algorithm_id: str, mode: str = "backtesting",
           run_id: str = None, dry_run: bool = False) -> int:
    """Write review_* to InfluxDB. Env: INFLUXDB_URL/ORG/BUCKET/TOKEN."""
    lines = build_lines(review_doc, algorithm_id, mode, run_id or "manual")
    if dry_run:
        for l in lines[:5]:
            print(l)
        print(f"... ({len(lines)} total)")
        return len(lines)
    return write_lines_to_influx(
        lines,
        influx_url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        org=os.environ.get("INFLUXDB_ORG", "lean"),
        bucket=os.environ.get("INFLUXDB_BUCKET", "quant"),
        token=os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem"),
    )
