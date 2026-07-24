"""Regression test: strategy-review dashboard's 6 panels must never change. Spec §4.3."""
import json
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "monitoring" / "grafana" / "dashboards" / "lean" / "strategy-review.json"
BASELINE = Path(__file__).resolve().parent / "_strategy_review_baseline_6.json"
ALLOWED_TYPES = {"stat", "barchart", "scatter", "table", "timeseries", "bargauge", "row", "gauge"}


def _panel_fingerprint(p: dict) -> tuple:
    gp = p.get("gridPos", {})
    return (p.get("title", ""), (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y")), p.get("type", ""))


def test_dashboard_exists():
    assert DASHBOARD.exists(), f"missing {DASHBOARD}"


def test_original_6_panels_unchanged():
    db = json.loads(DASHBOARD.read_text())
    panels = [p for p in db.get("panels", []) if p.get("type") != "row"]
    current = [_panel_fingerprint(p) for p in panels]
    baseline = json.loads(BASELINE.read_text())
    normalized_baseline = [(b[0], tuple(b[1]), b[2]) for b in baseline]
    assert current == normalized_baseline, (
        f"panel layout changed!\ncurrent={current}\nbaseline={normalized_baseline}"
    )


def test_algorithm_id_variable_preserved():
    db = json.loads(DASHBOARD.read_text())
    vars_ = {v.get("name"): v.get("query", "") for v in db.get("templating", {}).get("list", [])}
    assert "algorithm_id" in vars_, "algorithm_id template var missing"
    assert "review_layer_attribution" in vars_["algorithm_id"]


def test_no_stale_plugins():
    db = json.loads(DASHBOARD.read_text())
    for p in db.get("panels", []):
        assert p.get("type") in ALLOWED_TYPES, f"stale plugin type: {p.get('type')}"
