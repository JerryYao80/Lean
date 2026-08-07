"""Tests for provenance.write + layer improvement verification. Spec §3.5, §3.7 (spirit2 #2)."""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from provenance import write, verify_layer_improvement  # noqa: E402


def test_write_adds_provenance_block(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"strategy_id": "NewStrategy-abc", "class_name": "NewStrategy"}))
    write(str(manifest_path), parent_strategy="Gold2",
          review_artifact="Results/gold2-betavol/review/review.json",
          inspired_layer="extreme_risk", hypothesis="path/to/hypothesis.md",
          inspired_at_generation=3, improvement_verified=True, layer_improvement=0.22,
          improvement_claim="该层贡献从 -32% → -10% 以内")
    doc = json.loads(manifest_path.read_text())
    assert doc["provenance"]["source"] == "review_inspiration"
    assert doc["provenance"]["parent_strategy"] == "Gold2"
    assert doc["provenance"]["improvement_verified"] is True
    assert doc["provenance"]["layer_improvement"] == 0.22


def test_verify_layer_improvement_pass():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
    assert result["improvement"] > 0
    assert result["improved"] is True


def test_verify_layer_improvement_fail():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
    assert result["improvement"] < 0
    assert result["improved"] is False


def test_verify_layer_improvement_against_claim():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.15}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk", claim_target=-0.10)
    assert result["improved"] is True
    assert result["claim_met"] is False
