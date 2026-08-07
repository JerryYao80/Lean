"""Test stage_bridge writes 2 new measurements + CSV, doesn't touch existing."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts"))


def _full_review():
    return {"layer_attribution": {
        "alpha_001": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 17},
        "alpha_002": {"pnl_pct_of_total": "-0.05", "pnl_abs": "-50", "n_trades": 30, "n_wins": 10},
        "alpha_003": {"pnl_pct_of_total": "0.15", "pnl_abs": "150", "n_trades": 30, "n_wins": 16},
        "alpha_004": {"pnl_pct_of_total": "0.10", "pnl_abs": "100", "n_trades": 30, "n_wins": 15},
        "alpha_005": {"pnl_pct_of_total": "0.12", "pnl_abs": "120", "n_trades": 30, "n_wins": 15},
        "alpha_006": {"pnl_pct_of_total": "0.08", "pnl_abs": "80", "n_trades": 30, "n_wins": 14},
        "alpha_007": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 18},
        "alpha_008": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 17}}}


def test_bridge_builds_stage_and_attribution_points():
    from csi300_alpha101.stage_bridge import build_points
    summary = {"statistics": {"Sharpe Ratio": "1.2", "Drawdown": "10%",
        "Net Profit": "15%", "Total Orders": "30", "Win Rate": "55%",
        "Sortino Ratio": "1.5", "Compounding Annual Return": "8%"}}
    stage_meta = {"strategy_id": "csi300_alpha101_composite", "stage_type": "baseline",
                  "variant_id": "baseline", "generation": 0}
    params = {f"w_alpha{n}": "0.125" for n in
              ["alpha001","alpha006","alpha030","alpha040",
               "alpha042","alpha055","alpha058","alpha101"]}
    points = build_points(summary, _full_review(), stage_meta, params, ts_ns=1234)
    stage_pts = [p for p in points if p.measurement == "csi300_alpha101_stage"]
    attr_pts = [p for p in points if p.measurement == "csi300_alpha101_alpha_attribution"]
    assert len(stage_pts) == 1
    assert stage_pts[0].fields["sharpe"] == 1.2
    assert stage_pts[0].tags["stage_type"] == "baseline"
    assert len(attr_pts) == 8
    assert attr_pts[0].tags["alpha_id"].startswith("alpha_")


def test_bridge_appends_csv_row(tmp_path):
    from csi300_alpha101.stage_bridge import append_csv
    csv_path = tmp_path / "stage_results.csv"
    row = {"stage_type": "baseline", "generation": 0, "variant_id": "baseline",
           "sharpe": 1.2, "drawdown": 10.0}
    append_csv(csv_path, row)
    append_csv(csv_path, row)
    lines = csv_path.read_text().strip().split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "stage_type" in lines[0]


def test_bridge_never_writes_existing_measurements():
    from csi300_alpha101.stage_bridge import build_points
    summary = {"statistics": {"Sharpe Ratio": "1.0", "Total Orders": "1"}}
    points = build_points(summary, _full_review(),
        {"strategy_id": "x", "stage_type": "baseline", "variant_id": "b", "generation": 0},
        {}, ts_ns=1)
    measurements = {p.measurement for p in points}
    assert measurements == {"csi300_alpha101_stage", "csi300_alpha101_alpha_attribution"}
    assert "lean_backtest_stat" not in measurements
    assert "soloquant_pipeline_funnel" not in measurements
