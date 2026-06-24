"""Regression test: the barra-cne5-live-paper dashboard's original panels must never change."""
import json
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "monitoring" / "grafana" / "dashboards" / "lean" / "barra-cne5-live-paper.json"


def _panel_fingerprint(p: dict) -> tuple:
    gp = p.get("gridPos", {})
    return (
        p.get("title", ""),
        (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y")),
        p.get("type", ""),
    )


def test_dashboard_exists():
    assert DASHBOARD.exists(), f"missing {DASHBOARD}"


def test_original_43_panels_unchanged():
    """The 43 panels present before this feature must keep their (title, gridPos, type).

    New panels (e.g. the control button panel) are allowed and must come AFTER index 42.
    """
    db = json.loads(DASHBOARD.read_text())
    panels = db.get("panels", [])
    assert len(panels) >= 43, f"expected >=43 panels, got {len(panels)}"
    baseline = json.loads((Path(__file__).parent / "_barra_baseline_43.json").read_text())
    current = [_panel_fingerprint(p) for p in panels[:43]]
    # baseline entries are [title, [h,w,x,y], type]; normalize gridPos to tuple for comparison
    normalized_baseline = [(b[0], tuple(b[1]), b[2]) for b in baseline]
    assert current == normalized_baseline, (
        "An original panel's (title, gridPos, type) changed — this violates the "
        "'do not modify existing dashboard' constraint."
    )


def test_algorithm_id_variable_preserved():
    db = json.loads(DASHBOARD.read_text())
    names = [v.get("name") for v in db.get("templating", {}).get("list", [])]
    assert "algorithm_id" in names


def test_no_leftover_control_button_panel():
    """The volkovlabs-button-panel was delisted from the Grafana registry (404), so we
    do NOT ship a dashboard button panel — a broken 'plugin not found' panel would violate
    the 'do not modify the dashboard' constraint. Manual control is delivered via the API
    (POST /api/strategies/{id}/stop|start) + a follow-up Business Forms panel wiring.
    This assertion guards against accidentally re-introducing a stale button panel.
    """
    db = json.loads(DASHBOARD.read_text())
    stale = [p for p in db["panels"] if p.get("type") == "volkovlabs-button-panel"]
    assert not stale, "stale volkovlabs-button-panel present — remove it (plugin delisted)"
