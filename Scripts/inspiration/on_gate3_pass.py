"""GATE 3 pass → redesigned writeback hook (production path). Spec §4.6, spirit3 #1.

Called by create-strategy skill after GATE 3 (backtest_sharpe) PASS.
If new strategy manifest.provenance.source == review_inspiration:
  verify_layer_improvement(parent_review, new_review, inspired_layer)
  → if improved: provenance.write + transition→redesigned + deploy-reminder file
  → if not: provenance.write(improvement_verified=False), layer stays inspiration_pending
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provenance import write as write_provenance, verify_layer_improvement  # noqa: E402
from layer_state import load_layer_states, transition, save_layer_states  # noqa: E402


def on_pass(strategy_id: str, manifest_path: str, new_review_path: str,
            parent_state_path: str) -> dict:
    """Hook called after GATE 3 PASS. Returns {redesigned, improvement, reason}."""
    mpath = Path(manifest_path)
    if not mpath.exists():
        return {"redesigned": False, "reason": "manifest_missing"}
    manifest = json.loads(mpath.read_text())
    provenance = manifest.get("provenance", {})
    if provenance.get("source") != "review_inspiration":
        return {"redesigned": False, "reason": "not_review_inspiration"}

    parent_strategy = provenance["parent_strategy"]
    inspired_layer = provenance["inspired_layer"]
    parent_review_path = provenance.get("review_artifact", "")

    parent_review = {}
    if Path(parent_review_path).exists():
        parent_review = json.loads(Path(parent_review_path).read_text())
    new_review = {}
    if Path(new_review_path).exists():
        new_review = json.loads(Path(new_review_path).read_text())

    result = verify_layer_improvement(parent_review, new_review, inspired_layer)

    if result["improved"]:
        # provenance.write with real improvement
        write_provenance(manifest_path, parent_strategy, parent_review_path,
                         inspired_layer, provenance.get("hypothesis", ""),
                         provenance.get("inspired_at_generation", 0),
                         improvement_verified=True, layer_improvement=result["improvement"],
                         improvement_claim=provenance.get("improvement_claim", ""))
        # layer → redesigned
        states = load_layer_states(parent_state_path, parent_strategy)
        shaping_term = f"{inspired_layer}_contrib_penalty"
        try:
            transition(states, inspired_layer, "redesigned",
                       inspired_strategy_id=strategy_id,
                       retired_shaping_terms=[shaping_term],
                       candidate_status="pending_review")
            save_layer_states(parent_state_path, parent_strategy, states)
        except ValueError as ex:
            # already redesigned or illegal state — log, don't crash GATE 3
            print(f"  [on_gate3_pass] transition skipped: {ex}")
        # deploy-reminder file (human sees at deploy_gate)
        reminder_path = Path(parent_state_path).parent / f"deploy_reminder_{parent_strategy}_{inspired_layer}.txt"
        reminder_path.write_text(
            f"[inspiration 候选策略待审]\n"
            f"父策略: {parent_strategy}\n失效层: {inspired_layer}\n"
            f"新策略: {strategy_id}\n层改善: {result['improvement']:.4f}\n"
            f"manifest: {manifest_path}\n"
            f"请审/部署/拒绝后回填 layer_state.candidate_status (deployed/rejected)\n")
        return {"redesigned": True, "improvement": result["improvement"], "reason": "improved"}
    else:
        # not improved: provenance records failure, layer stays inspiration_pending (consumes timeout)
        write_provenance(manifest_path, parent_strategy, parent_review_path,
                         inspired_layer, provenance.get("hypothesis", ""),
                         provenance.get("inspired_at_generation", 0),
                         improvement_verified=False, layer_improvement=result["improvement"],
                         improvement_claim=provenance.get("improvement_claim", ""))
        print(f"  [on_gate3_pass] layer '{inspired_layer}' NOT improved ({result['improvement']:.4f}), staying inspiration_pending")
        return {"redesigned": False, "improvement": result["improvement"], "reason": "not_improved"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy-id", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--new-review", required=True)
    ap.add_argument("--parent-state", required=True)
    args = ap.parse_args()
    print(json.dumps(on_pass(args.strategy_id, args.manifest, args.new_review, args.parent_state), indent=2))
