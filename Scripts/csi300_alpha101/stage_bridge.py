"""stage_bridge — write per-stage backtest results to 2 NEW InfluxDB measurements
+ append to CSV, so optimization/refactor gains are visible in Grafana.

Reuses export_backtest_results_to_influx helpers (parse_numeric_value,
InfluxPoint) WITHOUT modifying that file. Writes ONLY csi300_alpha101_*
measurements — never lean_backtest_stat / soloquant_pipeline_funnel / barra_*.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "Scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "Scripts"))

import yaml  # noqa: E402
from export_backtest_results_to_influx import parse_numeric_value, InfluxPoint  # noqa: E402

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem")

STAGE_MEASUREMENT = "csi300_alpha101_stage"
ATTR_MEASUREMENT = "csi300_alpha101_alpha_attribution"
CSV_PATH = _REPO / "Results" / "csi300_alpha101" / "stage_results.csv"

# Layer role names (alpha_001..alpha_008) → actual alpha ids for weight lookup.
LAYER_TO_ALPHA = {
    "alpha_001": "alpha001", "alpha_002": "alpha006", "alpha_003": "alpha030",
    "alpha_004": "alpha040", "alpha_005": "alpha042", "alpha_006": "alpha055",
    "alpha_007": "alpha058", "alpha_008": "alpha101",
}


def build_points(summary: dict, review: dict, stage_meta: dict,
                 params: dict, ts_ns: int) -> list[InfluxPoint]:
    """Build InfluxPoints for 1 stage row + 8 alpha attribution rows."""
    stats = summary.get("statistics") or summary.get("Statistics") or {}
    strategy_id = stage_meta["strategy_id"]
    stage_type = stage_meta["stage_type"]
    variant_id = stage_meta["variant_id"]
    generation = str(stage_meta["generation"])

    stage_fields = {
        "sharpe": parse_numeric_value(stats.get("Sharpe Ratio")) or 0.0,
        "sortino": parse_numeric_value(stats.get("Sortino Ratio")) or 0.0,
        "drawdown": parse_numeric_value(stats.get("Drawdown")) or 0.0,
        "net_profit": parse_numeric_value(stats.get("Net Profit")) or 0.0,
        "compounding_annual_return": parse_numeric_value(stats.get("Compounding Annual Return")) or 0.0,
        "total_orders": parse_numeric_value(stats.get("Total Orders")) or 0.0,
        "win_rate": parse_numeric_value(stats.get("Win Rate")) or 0.0,
    }
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        w = parse_numeric_value(params.get(f"w_{alpha_id}"))
        stage_fields[f"weight_{alpha_id}"] = w or 0.0
    if stage_type == "optimize_champion":
        stage_fields["dsr_passed"] = float(bool(stage_meta.get("dsr_passed", 0)))

    stage_tags = {"strategy_id": strategy_id, "stage_type": stage_type,
                  "variant_id": variant_id, "generation": generation}
    points = [InfluxPoint(measurement=STAGE_MEASUREMENT, tags=stage_tags,
                          fields=stage_fields, timestamp_ns=ts_ns)]

    layer_attr = review.get("layer_attribution", {})
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        agg = layer_attr.get(layer, {})
        w = parse_numeric_value(params.get(f"w_{alpha_id}")) or 0.0
        attr_fields = {
            "pnl_pct_of_total": parse_numeric_value(agg.get("pnl_pct_of_total")) or 0.0,
            "pnl_abs": parse_numeric_value(agg.get("pnl_abs")) or 0.0,
            "n_trades": parse_numeric_value(agg.get("n_trades")) or 0.0,
            "n_wins": parse_numeric_value(agg.get("n_wins")) or 0.0,
            "weight": w,
        }
        attr_tags = {"strategy_id": strategy_id, "alpha_id": layer,
                     "stage_type": stage_type, "variant_id": variant_id,
                     "generation": generation}
        points.append(InfluxPoint(measurement=ATTR_MEASUREMENT, tags=attr_tags,
                                  fields=attr_fields, timestamp_ns=ts_ns))
    return points


def append_csv(csv_path: Path, row: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists()
    cols = ["stage_type", "generation", "variant_id", "sharpe", "sortino",
            "drawdown", "net_profit", "total_orders", "win_rate", "dsr_passed",
            "alpha_001_contrib_pct", "alpha_002_contrib_pct", "alpha_003_contrib_pct",
            "alpha_004_contrib_pct", "alpha_005_contrib_pct", "alpha_006_contrib_pct",
            "alpha_007_contrib_pct", "alpha_008_contrib_pct",
            "w_alpha001", "w_alpha006", "w_alpha030", "w_alpha040",
            "w_alpha042", "w_alpha055", "w_alpha058", "w_alpha101"]
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(cols)
        w.writerow([row.get(c, "") for c in cols])


def write_influx(points: list[InfluxPoint]) -> int:
    from urllib import parse, request
    lines = []
    for p in points:
        tag_str = ",".join(f"{k}={parse.quote(str(v))}" for k, v in p.tags.items())
        field_str = ",".join(f"{k}={v}" if isinstance(v, float) else f'{k}="{v}"'
                              for k, v in p.fields.items())
        lines.append(f"{p.measurement},{tag_str} {field_str} {p.timestamp_ns}")
    body = "\n".join(lines).encode()
    req = request.Request(
        f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}",
        data=body, method="POST",
        headers={"Authorization": f"Token {INFLUX_TOKEN}",
                 "Content-Type": "text/plain; charset=utf-8"})
    try:
        request.urlopen(req, timeout=10)
        return len(lines)
    except Exception:
        return 0


def run(summary_path: Path, review_path: Path, manifest_path: Path,
        stage_meta: dict) -> int:
    """Read summary + review + manifest params, build points, write Influx + CSV."""
    import json
    from datetime import datetime, timezone
    summary = json.loads(summary_path.read_text())
    review = json.loads(review_path.read_text())
    manifest = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
    params = manifest.get("parameters", {})

    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    points = build_points(summary, review, stage_meta, params, ts_ns)
    written = write_influx(points)

    row = {"stage_type": stage_meta["stage_type"], "generation": stage_meta["generation"],
           "variant_id": stage_meta["variant_id"]}
    s = points[0].fields
    row.update({"sharpe": s.get("sharpe", ""), "sortino": s.get("sortino", ""),
                "drawdown": s.get("drawdown", ""), "net_profit": s.get("net_profit", ""),
                "total_orders": s.get("total_orders", ""), "win_rate": s.get("win_rate", ""),
                "dsr_passed": int(s.get("dsr_passed", 0))})
    for i, p in enumerate(points[1:]):
        row[f"alpha_{i+1:03d}_contrib_pct"] = p.fields.get("pnl_pct_of_total", "")
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        row[f"w_{alpha_id}"] = params.get(f"w_{alpha_id}", "")
    append_csv(CSV_PATH, row)
    return written


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--summary", required=True)
    p.add_argument("--review", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--stage-type", required=True)
    p.add_argument("--variant-id", required=True)
    p.add_argument("--generation", type=int, default=0)
    args = p.parse_args()
    n = run(Path(args.summary), Path(args.review), Path(args.manifest),
            {"strategy_id": "csi300_alpha101_composite", "stage_type": args.stage_type,
             "variant_id": args.variant_id, "generation": args.generation})
    print(f"wrote {n} points")
