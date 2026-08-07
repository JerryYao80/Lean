"""Tests for gold2 manifest feedback: block + state_schema drawdown/pnl. Spec §2.4, §3.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_feedback_block():
    m = load_manifest(MANIFEST)
    fb = m.raw.get("feedback", {})
    assert fb, "feedback: block missing"
    assert fb["adapter_module"] == "feedback.adapters.gold2"
    assert fb["adapter_class"] == "Gold2FeedbackAdapter"
    assert fb["trigger_thresholds"]["max_layer_attribution_gap"] == 0.15
    assert fb["trigger_thresholds"]["min_narrative_trades"] == 20
    assert "extreme_risk" in fb["shaping_term_map"]
    assert "realrate_cap" in fb["shaping_term_map"]


def test_state_schema_has_drawdown_pnl():
    m = load_manifest(MANIFEST)
    names = {f.name for f in m.state_schema.fields}
    assert "drawdown" in names
    assert "pnl" in names
