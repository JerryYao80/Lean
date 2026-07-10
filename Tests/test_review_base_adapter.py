"""Tests for StrategyReviewAdapter ABC + TradeRecord + ReviewResult. Spec §3.1, §6.1."""
import sys
from decimal import Decimal
from pathlib import Path

# bootstrap Scripts/review onto sys.path so `from adapters.base import ...` resolves
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))

from adapters.base import (  # noqa: E402
    StrategyReviewAdapter, TradeRecord, TradeContext, ReviewResult,
)


def test_trade_record_carries_direction_sign_in_profit_loss():
    """quantity unsigned; direction sign lives in profit_loss (LEAN TradeBuilder sign)."""
    t = TradeRecord(
        symbol="518880", entry_time="2020-07-01", entry_price=Decimal("10.0"),
        exit_time="2020-07-05", exit_price=Decimal("11.0"),
        quantity=Decimal("100"), side="long",
        profit_loss=Decimal("100.0"), fees=Decimal("5.0"),
        tpv_entry=Decimal("1000.0"),
    )
    assert t.realized_pnl == Decimal("95.0")  # profit_loss - fees
    assert t.quantity == Decimal("100")  # unsigned


def test_abc_cannot_instantiate_directly():
    import pytest
    with pytest.raises(TypeError):
        StrategyReviewAdapter()  # abstract


class _GoodAdapter(StrategyReviewAdapter):
    LAYERS = ["a", "b"]
    def layer_attribution(self, trade, context):
        return {"a": Decimal("1"), "b": Decimal("4")}
    def trade_narrative(self, trade, context):
        return {"note": "ok"}
    @property
    def sum_to_property(self):
        return "profit_loss"


class _BadAdapter(StrategyReviewAdapter):
    LAYERS = ["a", "b"]
    def layer_attribution(self, trade, context):
        return {"a": Decimal("6")}  # sum != profit_loss; missing "b"
    def trade_narrative(self, trade, context):
        return {}
    @property
    def sum_to_property(self):
        return "profit_loss"


def test_good_adapter_sums_to_profit_loss():
    ad = _GoodAdapter()
    t = TradeRecord("518880", "t0", Decimal("1"), "t1", Decimal("1"),
                    Decimal("1"), "long", Decimal("5"), Decimal("0"), Decimal("1"))
    result = ad.layer_attribution(t, None)
    assert set(result.keys()) == {"a", "b"}
    assert sum(result.values()) == Decimal("5")  # == profit_loss
    assert ad.sum_to_property == "profit_loss"


def test_review_result_holds_blocks():
    rr = ReviewResult(
        run_meta={"strategy_name": "x", "schema_version": "1"},
        layer_attribution={},
        per_trade_narrative=[],
        drawdown_attribution=[],
        tca=None,
    )
    assert rr.run_meta["strategy_name"] == "x"
    assert rr.tca is None
