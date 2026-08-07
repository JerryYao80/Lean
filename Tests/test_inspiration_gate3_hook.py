"""Tests for GATE 3 pass → redesigned writeback hook (production path). Spec §4.6, spirit3 #1."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from on_gate3_pass import on_pass  # noqa: E402
from layer_state import load_layer_states  # noqa: E402


def _setup(tmp_path, parent_layer_status="inspiration_pending", new_layer_pct=-0.10):
    # parent strategy state: extreme_risk in inspiration_pending since gen 3
    parent_state_path = str(tmp_path / "parent_state.json")
    parent_states = {"extreme_risk": {
        "layer": "extreme_risk", "status": parent_layer_status, "pending_since_generation": 3,
        "inspired_strategy_id": "", "retired_shaping_terms": [],
        "candidate_deployed": False, "candidate_status": "none"}}
    Path(parent_state_path).write_text(json.dumps({"layer_states": {"Gold2": parent_states}}))
    # parent review.json
    parent_review_path = tmp_path / "parent_review.json"
    parent_review_path.write_text(json.dumps({"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}))
    # new strategy manifest w/ provenance
    new_manifest_path = tmp_path / "new_manifest.json"
    new_manifest_path.write_text(json.dumps({
        "strategy_id": "NewStrategy-abc",
        "provenance": {"source": "review_inspiration", "parent_strategy": "Gold2",
                       "inspired_layer": "extreme_risk", "review_artifact": str(parent_review_path),
                       "inspired_at_generation": 3}}))
    # new review.json (post-backtest)
    new_review_path = tmp_path / "new_review.json"
    new_review_path.write_text(json.dumps({"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": new_layer_pct}}}))
    return parent_state_path, str(new_manifest_path), str(new_review_path)


def test_on_pass_redesigns_when_layer_improved(tmp_path):
    parent_state_path, new_manifest, new_review = _setup(tmp_path, new_layer_pct=-0.10)
    result = on_pass(strategy_id="NewStrategy-abc", manifest_path=new_manifest,
                     new_review_path=new_review, parent_state_path=parent_state_path)
    assert result["redesigned"] is True
    assert result["improvement"] > 0
    # parent layer → redesigned
    states = load_layer_states(parent_state_path, "Gold2")
    assert states["extreme_risk"].status == "redesigned"
    assert states["extreme_risk"].candidate_status == "pending_review"
    assert states["extreme_risk"].inspired_strategy_id == "NewStrategy-abc"
    # provenance updated with real improvement
    doc = json.loads(Path(new_manifest).read_text())
    assert doc["provenance"]["improvement_verified"] is True
    assert doc["provenance"]["layer_improvement"] > 0
    # deploy-reminder file written
    reminders = list(Path(parent_state_path).parent.glob("deploy_reminder_*.txt"))
    assert len(reminders) >= 1
    assert "待审" in reminders[0].read_text() or "pending" in reminders[0].read_text()


def test_on_pass_no_redesign_when_layer_not_improved(tmp_path):
    parent_state_path, new_manifest, new_review = _setup(tmp_path, new_layer_pct=-0.40)  # worse than parent -0.32
    result = on_pass(strategy_id="NewStrategy-abc", manifest_path=new_manifest,
                     new_review_path=new_review, parent_state_path=parent_state_path)
    assert result["redesigned"] is False
    # parent layer stays inspiration_pending (not redesigned)
    states = load_layer_states(parent_state_path, "Gold2")
    assert states["extreme_risk"].status == "inspiration_pending"
    doc = json.loads(Path(new_manifest).read_text())
    assert doc["provenance"]["improvement_verified"] is False


def test_on_pass_noop_when_not_review_inspiration(tmp_path):
    # manifest without review_inspiration provenance → no-op
    new_manifest = tmp_path / "new_manifest.json"
    new_manifest.write_text(json.dumps({"strategy_id": "PaperStrategy", "provenance": {"source": "web"}}))
    result = on_pass(strategy_id="PaperStrategy", manifest_path=str(new_manifest),
                     new_review_path=str(tmp_path / "new_review.json"),
                     parent_state_path=str(tmp_path / "parent_state.json"))
    assert result["redesigned"] is False
    assert result["reason"] == "not_review_inspiration"
