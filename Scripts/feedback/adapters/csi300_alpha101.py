"""Csi300Alpha101 feedback adapter — detects low-contribution alpha and
triggers hypothesize.run to propose a replacement alpha (Stage 6 refactor).

Mirrors the gold2 feedback trigger pattern but does NOT modify gold2.py
(never-modify-existing-features). hypothesize.run is strategy-agnostic.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "Scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "Scripts"))

from inspiration.hypothesize import run as hypothesize_run  # noqa: E402


@dataclass
class FeedbackAction:
    trigger: str
    inspired_layer: str
    hypothesis_path: str
    trigger_reason: str


def check_triggers(review: dict, gen_history: list, manifest_raw: dict,
                   hypothesis_dir: str = "Results/soloquant/local-strategies"
                   ) -> Optional[FeedbackAction]:
    """Fire hypothesize for any alpha layer with persistent negative contribution
    AND non-convergent shaping weight (gap > threshold for >= min_generations)."""
    inspiration = manifest_raw.get("inspiration", {})
    persistence = inspiration.get("persistence", {})
    gap_threshold = persistence.get("gap_threshold", 0.20)
    min_generations = persistence.get("min_generations", 3)

    layer_attr = review.get("layer_attribution", {})

    for layer, agg in layer_attr.items():
        pnl_pct = float(agg.get("pnl_pct_of_total", 0))
        if pnl_pct >= 0:
            continue

        gaps = [g.get("layer_gaps", {}).get(layer, {}).get("gap", 0)
                for g in gen_history]
        weights = [g.get("shaping_overrides", {}).get(f"{layer}_contrib_penalty", 0)
                   for g in gen_history]
        if len(gaps) < min_generations:
            continue
        recent_gaps = gaps[-min_generations:]
        if not all(g >= gap_threshold for g in recent_gaps):
            continue
        if weights and not (weights[-1] >= 3.0 or _trending_up(weights[-min_generations:])):
            continue

        strategy_name = manifest_raw.get("strategy_name", "Csi300Alpha101")
        llm_cfg = inspiration.get("llm", {})
        path = hypothesize_run(
            strategy_name=strategy_name,
            inspired_layer=layer,
            review_doc=review,
            gen_history=gen_history,
            manifest_raw=manifest_raw,
            llm_cfg=llm_cfg,
            hypothesis_dir=hypothesis_dir,
        )
        return FeedbackAction(
            trigger="review_drift",
            inspired_layer=layer,
            hypothesis_path=path,
            trigger_reason=f"{layer} pnl_pct={pnl_pct:.3f} for {min_generations} gens, gap>{gap_threshold}",
        )
    return None


def _trending_up(seq: list) -> bool:
    return len(seq) >= 2 and seq[-1] > seq[0]
