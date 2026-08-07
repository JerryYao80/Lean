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
    """volkovlabs-button-panel was delisted from the Grafana registry (404), so the
    dashboard uses volkovlabs-form-panel (Business Forms) for the control buttons instead.
    This assertion guards against accidentally re-introducing the stale delisted plugin.
    """
    db = json.loads(DASHBOARD.read_text())
    stale = [p for p in db["panels"] if p.get("type") == "volkovlabs-button-panel"]
    assert not stale, "stale volkovlabs-button-panel present — remove it (plugin delisted)"


def test_manual_control_form_panels_present():
    """The Manual Control row + Start/Stop Business Forms panels are appended after the
    original 43. They POST via the authenticated Infinity datasource (no token in JSON).
    """
    db = json.loads(DASHBOARD.read_text())
    types_titles = [(p.get("type"), p.get("title")) for p in db["panels"]]
    assert ("row", "Manual Control") in types_titles
    start = [p for p in db["panels"] if p.get("title") == "Start Strategy"]
    stop = [p for p in db["panels"] if p.get("title") == "Stop Strategy"]
    assert len(start) == 1 and start[0]["type"] == "volkovlabs-form-panel"
    assert len(stop) == 1 and stop[0]["type"] == "volkovlabs-form-panel"
    # Start/Stop POST via the Infinity datasource (auth injected server-side)
    for p in start + stop:
        upd = p.get("options", {}).get("update", {})
        assert upd.get("method") == "POST"
        assert "/api/strategies/by-algorithm/" in upd.get("url", "")
        assert upd.get("datasource", {}).get("uid") == "strategy-control-api"
