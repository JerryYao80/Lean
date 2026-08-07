"""provenance.write + layer improvement verification. Spec §3.5, §3.7 (spirit2 #2)."""
import json
from pathlib import Path


def write(manifest_path, parent_strategy, review_artifact, inspired_layer, hypothesis,
          inspired_at_generation, improvement_verified, layer_improvement, improvement_claim=""):
    """Add provenance block to new strategy manifest. Spec §3.5."""
    p = Path(manifest_path)
    doc = json.loads(p.read_text()) if p.exists() else {}
    doc["provenance"] = {
        "source": "review_inspiration",
        "parent_strategy": parent_strategy,
        "review_artifact": review_artifact,
        "inspired_layer": inspired_layer,
        "hypothesis": hypothesis,
        "inspired_at_generation": inspired_at_generation,
        "improvement_verified": improvement_verified,
        "layer_improvement": layer_improvement,
        "improvement_claim": improvement_claim,
    }
    p.write_text(json.dumps(doc, indent=2, default=str))


def verify_layer_improvement(parent_review, new_review, inspired_layer, claim_target=None):
    """Verify inspired_layer improved in new strategy vs parent. Spec §3.7 (spirit2 #2).

    Returns {improvement, improved, claim_met}.
    improvement = new_pct - old_pct (positive = layer got less negative = improved).
    """
    old_pct = parent_review.get("layer_attribution", {}).get(inspired_layer, {}).get("pnl_pct_of_total", 0)
    new_pct = new_review.get("layer_attribution", {}).get(inspired_layer, {}).get("pnl_pct_of_total", 0)
    improvement = new_pct - old_pct
    improved = improvement > 0
    claim_met = True
    if claim_target is not None:
        claim_met = new_pct >= claim_target
    return {"improvement": improvement, "improved": improved, "claim_met": claim_met}
