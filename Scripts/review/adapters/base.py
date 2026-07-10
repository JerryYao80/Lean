"""StrategyReviewAdapter ABC + TradeRecord/TradeContext/ReviewResult. Spec §3.1.

Generic cross-strategy protocol. Each strategy implements layer_attribution()
returning one Decimal per declared LAYERS; the values MUST sum to
trade.profit_loss (LEAN Trade.ProfitLoss, gross, pre-fees). fees ride on
TradeRecord and are shown as a reconciling line, never folded into a layer.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class TradeRecord:
    """One closed LEAN Trade. quantity is unsigned; direction sign lives in profit_loss."""
    symbol: str
    entry_time: str          # ISO-8601; matches closedTrade.entryTime
    entry_price: Decimal
    exit_time: str
    exit_price: Decimal
    quantity: Decimal        # Trade.Quantity, unsigned
    side: str                # "long" | "short" (mapped from TradeDirection 0/1)
    profit_loss: Decimal     # Trade.ProfitLoss, gross, carries direction sign
    fees: Decimal            # Trade.TotalFees, always positive
    tpv_entry: Decimal       # TotalPortfolioValue frozen at entry
    order_ids: list = field(default_factory=list)  # Trade.OrderIds

    @property
    def realized_pnl(self) -> Decimal:
        """Net of fees. Derived, NOT the sum-to target (spec §3.1)."""
        return self.profit_loss - self.fees


@dataclass
class TradeContext:
    """Per-trade state the adapter needs for attribution. Populated from state_trace."""
    entry_bar: dict = None   # {dir_coef, w_after_vol, extreme_cap, trend_disabled, ...} at ts<=entry
    realized_bar: dict = None  # first row ts>entry (post-fill weight)
    entry_signal: dict = None  # matched alpha insight
    regime_at_entry: dict = None


@dataclass
class ReviewResult:
    """Output of an adapter run; serialized to review.json."""
    run_meta: dict
    layer_attribution: dict          # {layer_name: {pnl_abs, pnl_pct_of_total, ...}}
    per_trade_narrative: list
    drawdown_attribution: list
    tca: Any                         # dict or None


class StrategyReviewAdapter(ABC):
    """Generic protocol. Implementations live in adapters/<strategy>.py."""

    LAYERS: list[str]  # ordered, e.g. ["trend","vol_target","extreme_risk","realrate_cap"]

    @abstractmethod
    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        """Return one Decimal per name in LAYERS. MUST sum to trade.profit_loss (LEAN gross)."""

    @abstractmethod
    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        """Per-trade narrative: entry_signal, regime, insight realized-vs-predicted."""

    @property
    @abstractmethod
    def sum_to_property(self) -> str:
        """Which TradeRecord field the layer contributions sum to. Always 'profit_loss'."""

    def validated_layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        """Call layer_attribution + enforce sum-to-profit_loss invariant. Raises ValueError.

        Spec §6.1: BadAdapter (sum != profit_loss, or keys != LAYERS) must raise.
        The CLI (Task 8) calls this instead of layer_attribution directly so a
        misimplemented adapter can never silently emit a non-reconciling breakdown.
        """
        result = self.layer_attribution(trade, context)
        expected_keys = set(self.LAYERS)
        if set(result.keys()) != expected_keys:
            raise ValueError(
                f"layer_attribution keys {set(result.keys())} != LAYERS {expected_keys}")
        total = sum(result.values())
        target = getattr(trade, self.sum_to_property)
        if total != target:
            raise ValueError(
                f"layer_attribution sum {total} != {self.sum_to_property} {target}")
        return result
