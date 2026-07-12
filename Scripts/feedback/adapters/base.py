"""StrategyFeedbackAdapter ABC + FeedbackAction. Spec §3.1.

Generic cross-strategy protocol. feedback_signal() returns a FeedbackAction
driving the 3-layer closed loop: (1) evolution_scheduler review_drift trigger,
(2) reward shaping term layer_contrib_penalty, (3) CQL observation_fields.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FeedbackAction:
    """Output of adapter.feedback_signal; drives the 3-layer closed loop."""
    trigger: bool
    trigger_reason: str
    shaping_overrides: dict
    observation_fields: list
    attribution_method: str
    per_bar_layer_contrib: list = field(default_factory=list)


class StrategyFeedbackAdapter(ABC):
    LAYERS: list

    @abstractmethod
    def feedback_signal(self, manifest, review_doc: dict, state_trace: list) -> FeedbackAction:
        """Read review.json + state_trace → FeedbackAction. Pure function."""
