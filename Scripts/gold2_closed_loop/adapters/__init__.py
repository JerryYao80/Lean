"""Proof-local adapters for the Gold2 closed-loop economic proof
(Task 10, Phase 2 STOP gate).

This package contains the G1 parameter optimizer adapter
(``parameter_optimizer``) and the typed request/result dataclasses
(``base``). It is PROOF-ONLY: it does NOT import or reuse the production
``Scripts/auto_optimize/evolution_scheduler`` /
``Scripts/inspiration`` machinery. The optimizer is fully independent.

Task 11 adds the ``formal_review`` and ``feedback_construction`` adapters
under this same package.
"""

from __future__ import annotations

from Scripts.gold2_closed_loop.adapters.base import (
    Adapter,
    Attempt,
    OptimizationRequest,
    OptimizationResult,
    Runner,
    TrialResult,
)
from Scripts.gold2_closed_loop.adapters.feedback_construction import (
    FeedbackAction,
    FeedbackConstructionAdapter,
    FeedbackResult,
)
from Scripts.gold2_closed_loop.adapters.formal_review import (
    FormalReviewAdapter,
    ReviewBundle,
    ReviewResult,
)
from Scripts.gold2_closed_loop.adapters.parameter_optimizer import (
    InfrastructureError,
    ParameterOptimizerAdapter,
)

__all__ = [
    "Adapter",
    "Attempt",
    "FeedbackAction",
    "FeedbackConstructionAdapter",
    "FeedbackResult",
    "FormalReviewAdapter",
    "InfrastructureError",
    "OptimizationRequest",
    "OptimizationResult",
    "ParameterOptimizerAdapter",
    "ReviewBundle",
    "ReviewResult",
    "Runner",
    "TrialResult",
]
