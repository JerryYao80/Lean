"""End-to-end integration: run_review on real gold2 → review.json → HTML → InfluxDB lines.
Spec §6.1. Verifies the full stack works together on real artifacts."""
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
RUN_REVIEW = _REPO / "Scripts" / "review" / "run_review.py"
MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def _run(args):
    return subprocess.run([sys.executable, str(RUN_REVIEW)] + args,
                          capture_output=True, text=True, cwd=str(_REPO))


def test_e2e_full_stack_on_gold2():
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 artifacts absent")
    r = _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS), "--html"])
    assert r.returncode in (0, 2), f"stderr={r.stderr[-800:]}"

    review_json = RESULTS / "review" / "review.json"
    review_html = RESULTS / "review" / "review.html"
    assert review_json.exists()
    assert review_html.exists(), "HTML tearsheet not written"

    doc = json.loads(review_json.read_text())
    total = Decimal(doc["run_meta"]["total_closed_trade_pnl"])
    layer_sum = sum(Decimal(v["pnl_abs"]) for v in doc["layer_attribution"].values())
    assert abs(layer_sum - total) < Decimal("0.01"), f"sum {layer_sum} != total {total}"

    assert set(doc["layer_attribution"].keys()) == {"trend", "vol_target", "extreme_risk", "realrate_cap"}
    assert doc["tca"] is not None
    assert doc["tca"]["n_fills"] > 0

    html = review_html.read_text()
    assert "https://" not in html
    assert "cdn" not in html.lower()


def test_e2e_influx_lines_build():
    """InfluxDB lines build without a live InfluxDB (dry-run)."""
    if not RESULTS.exists():
        import pytest
        pytest.skip("gold2 artifacts absent")
    _run(["--manifest", str(MANIFEST), "--results-dir", str(RESULTS)])
    sys.path.insert(0, str(_REPO / "Scripts" / "review"))
    sys.path.insert(0, str(_REPO / "Scripts"))
    doc = json.loads((RESULTS / "review" / "review.json").read_text())
    from influx_export import build_lines
    lines = build_lines(doc, "Gold2BetaVolTargetStrategy", "backtesting", "e2e-test")
    assert any(l.startswith("review_layer_attribution") for l in lines)
    assert any(l.startswith("review_trade") for l in lines)
    assert any(l.startswith("review_tca") for l in lines)
