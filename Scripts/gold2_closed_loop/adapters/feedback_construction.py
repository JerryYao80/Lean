"""Proof-local G2 feedback construction adapter (Task 11, Phase 2 STOP gate).

Consumes the FROZEN formal review bundle (produced by
:class:`FormalReviewAdapter`) ONCE and constructs G2 candidate generations
on the TRAIN partition only. It NEVER re-enters the review partition and
NEVER re-ranks candidates (spec §7 G2 line 242).

Design invariants (spec §7 G2, §8):

* ALL G2 objective/construction generations run on the TRAIN partition;
  the review partition is frozen and consumed once as the bundle.
  ``test_review_partition_never_ranks_g2`` pins this: the runner is only
  ever invoked with ``partition == "W1/train"``.
* An INVALID review bundle (``validity_status=INVALID``, e.g.
  ``MISSING_FORMAL_TRACE``) produces NO feedback — no graceful degradation
  to ``shaping={}`` (spec §8 line 299). The adapter returns
  ``feedback_action=None``, ``shaping_overrides={}``,
  ``validity_status=INVALID``, and runs NO candidate trials.
* A VALID bundle with a layer attribution gap drives non-empty shaping
  overrides sourced from the FROZEN telescoping bundle (not from P&L/dp).
* Each G2 generation consumes budget (complete attempt accounting,
  mirroring G1).
* If all G2 generations fail, G2 aliases to G1 (``aliased_to_g1=True``);
  the adapter never fabricates a feedback action and never aliases to G0.

This adapter is PROOF-ONLY. It does NOT import the production
``Scripts/feedback`` or ``Scripts/auto_optimize`` machinery; it wraps the
G1 :class:`ParameterOptimizerAdapter` for its train-partition candidate
search, driven by shaping overrides derived from the frozen bundle.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from Scripts.gold2_closed_loop.adapters.base import (
    Attempt,
    OptimizationRequest,
    Runner,
    TrialResult,
)
from Scripts.gold2_closed_loop.adapters.parameter_optimizer import (
    ParameterOptimizerAdapter,
)
from Scripts.gold2_closed_loop.candidate_registry import CandidateRegistry

_LAYER_NAMES = ("trend", "vol_target", "extreme_risk", "realrate_cap")
# Default layer->shaping-term map (mirrors the diagnostic Gold2 adapter).
_DEFAULT_TERM_MAP = {
    "extreme_risk": "extreme_risk_contrib_penalty",
    "realrate_cap": "realrate_cap_contrib_penalty",
}
# Spec §3.3 weight formula bounds (diagnostic adapter): clamp(gap/threshold,
# 0.5, 3.0).
_GAP_THRESHOLD = Decimal("0.15")
_WEIGHT_FLOOR = Decimal("0.5")
_WEIGHT_CEIL = Decimal("3.0")


@dataclass(frozen=True)
class FeedbackAction:
    """Frozen shaping feedback derived from the review bundle."""

    trigger: bool
    trigger_reason: str
    shaping_overrides: dict[str, float]
    attribution_method: str


@dataclass(frozen=True)
class FeedbackResult:
    """Result of :meth:`FeedbackConstructionAdapter.run`."""

    validity_status: str
    feedback_action: FeedbackAction | None
    shaping_overrides: dict[str, float] = field(default_factory=dict)
    attempted_trial_count: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    aliased_to_g1: bool = False


class FeedbackConstructionAdapter:
    """Construct G2 feedback from a frozen review bundle.

    Parameters
    ----------
    runner:
        Callable ``runner(request) -> TrialResult`` for G2 train-partition
        candidate trials (wraps the G1 optimizer's runner contract).
    budget:
        Number of G2 generations to run on the train partition.
    seed:
        Deterministic seed for the candidate grid shuffle.
    journal_path:
        Hash-chained candidate-event journal (mirrors G1).
    term_map:
        Optional layer->shaping-term override.
    """

    def __init__(
        self,
        runner: Runner | Callable[[OptimizationRequest], TrialResult],
        budget: int,
        seed: int,
        journal_path: Path | str,
        *,
        term_map: dict[str, str] | None = None,
    ) -> None:
        if budget < 1:
            raise ValueError(
                f"FeedbackConstructionAdapter: budget must be >= 1, got {budget}"
            )
        self._runner = runner
        self._budget = int(budget)
        self._seed = int(seed)
        self._journal_path = Path(journal_path)
        self._term_map = dict(term_map) if term_map else dict(_DEFAULT_TERM_MAP)

    def run(
        self,
        bundle: dict[str, Any] | Any,
        train_partition: str,
        review_partition: str,
    ) -> FeedbackResult:
        """Construct G2 feedback from ``bundle``.

        ``train_partition`` / ``review_partition`` are accepted explicitly so
        the adapter cannot accidentally run candidates on the review
        partition: only ``train_partition`` is ever passed to the runner.
        """
        if not train_partition:
            raise ValueError("train_partition must be non-empty")
        if not review_partition:
            raise ValueError("review_partition must be non-empty")
        validity = self._bundle_validity(bundle)
        if validity != "VALID":
            # INVALID bundle: NO feedback, NO graceful degradation, NO trials.
            return FeedbackResult(
                validity_status="INVALID",
                feedback_action=None,
                shaping_overrides={},
                attempted_trial_count=0,
                attempts=[],
                aliased_to_g1=False,
            )
        action = self._build_feedback_action(bundle)
        # Run G2 candidate generations on the TRAIN partition only. We
        # delegate to the G1 optimizer's runner contract so G2 inherits
        # complete attempt accounting (every generation consumes budget).
        shaping = action.shaping_overrides
        parameter_space = self._parameter_space_from_shaping(shaping)
        optimizer = ParameterOptimizerAdapter(
            self._runner,
            self._budget,
            self._seed,
            self._journal_path,
            stage_id="G2",
        )
        opt_result = optimizer.run(parameter_space, train_partition)
        # G2 aliases to G1 when no generation passes the gates.
        aliased_to_g1 = opt_result.selected_candidate is None
        return FeedbackResult(
            validity_status="VALID",
            feedback_action=action,
            shaping_overrides=shaping,
            attempted_trial_count=opt_result.attempted_trial_count,
            attempts=opt_result.attempts,
            aliased_to_g1=aliased_to_g1,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_validity(bundle: Any) -> str:
        """Return the bundle's validity_status ('VALID'/'INVALID')."""
        if bundle is None:
            return "INVALID"
        if isinstance(bundle, dict):
            return str(bundle.get("validity_status", "INVALID"))
        # A ReviewBundle dataclass.
        return str(getattr(bundle, "validity_status", "INVALID"))

    def _build_feedback_action(self, bundle: Any) -> FeedbackAction:
        """Derive shaping overrides from the frozen telescoping bundle."""
        layer_attr = self._layer_attribution(bundle)
        method = self._attribution_method(bundle)
        overrides: dict[str, float] = {}
        reasons: list[str] = []
        for layer, term in self._term_map.items():
            gap = self._layer_gap(layer_attr, layer)
            if gap is None:
                continue
            if abs(gap) > _GAP_THRESHOLD:
                weight = abs(gap) / _GAP_THRESHOLD
                clamped = max(_WEIGHT_FLOOR, min(weight, _WEIGHT_CEIL))
                overrides[term] = float(clamped)
                reasons.append(f"layer_gap {layer}={gap}>{_GAP_THRESHOLD}")
        trigger = bool(reasons)
        return FeedbackAction(
            trigger=trigger,
            trigger_reason="; ".join(reasons),
            shaping_overrides=overrides,
            attribution_method=method or "telescoping",
        )

    @staticmethod
    def _layer_attribution(bundle: Any) -> dict[str, dict[str, Any]]:
        if isinstance(bundle, dict):
            return dict(bundle.get("layer_attribution") or {})
        return dict(getattr(bundle, "layer_attribution", {}) or {})

    @staticmethod
    def _attribution_method(bundle: Any) -> str | None:
        if isinstance(bundle, dict):
            m = bundle.get("attribution_method")
            return str(m) if m is not None else None
        return getattr(bundle, "attribution_method", None)

    @staticmethod
    def _layer_gap(
        layer_attr: dict[str, dict[str, Any]], layer: str
    ) -> Decimal | None:
        agg = layer_attr.get(layer)
        if not isinstance(agg, dict):
            return None
        raw = agg.get("pnl_pct_of_total")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _parameter_space_from_shaping(
        shaping: dict[str, float]
    ) -> dict[str, Any]:
        """Declare a G2 parameter space from the shaping overrides.

        Each shaping term becomes a bounded range the G2 optimizer searches
        over. If no shaping overrides (no layer gap), G2 still runs its
        generations over a trivial flag so the budget is consumed and the
        alias-to-G1 path is observable.
        """
        if not shaping:
            return {"g2-baseline": {"type": "bool"}}
        space: dict[str, Any] = {}
        for term, weight in shaping.items():
            # Center the search around the diagnostic weight, bounded.
            lo = max(0.0, float(weight) - 0.5)
            hi = float(weight) + 0.5
            space[term] = {"type": "range", "min": lo, "max": hi, "step": 0.5}
        return space
