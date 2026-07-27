"""Test Csi300Alpha101ReviewAdapter proportional-z attribution sums to profit_loss."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from adapters.csi300_alpha101 import Csi300Alpha101ReviewAdapter  # noqa: E402


def _trade(profit_loss: str) -> TradeRecord:
    # Field names match base.py's TradeRecord dataclass exactly.
    return TradeRecord(
        symbol="600519.SH", entry_time="2024-01-02T09:30:00",
        entry_price=Decimal("100"), exit_time="2024-02-02T09:30:00",
        exit_price=Decimal("110"), quantity=Decimal("100"), side="long",
        profit_loss=Decimal(profit_loss), fees=Decimal("5"),
        tpv_entry=Decimal("1000000"), order_ids=[],
    )


def test_proportional_z_sums_to_profit_loss():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("1000")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {"alpha_001": 0.5, "alpha_002": -0.3,
            "alpha_003": 0.2, "alpha_004": -0.1, "alpha_005": 0.4,
            "alpha_006": -0.2, "alpha_007": 0.1, "alpha_008": 0.3},
            "alpha_weights": {a: 0.125 for a in [
                "alpha_001","alpha_002","alpha_003","alpha_004",
                "alpha_005","alpha_006","alpha_007","alpha_008"]}},
        realized_bar={}, entry_signal={}, regime_at_entry={},
    )
    out = ad.layer_attribution(trade, ctx)
    assert set(out.keys()) == set(ad.LAYERS)
    total = sum(out.values())
    assert total == trade.profit_loss, f"sum {total} != {trade.profit_loss}"


def test_residual_equal_split_when_no_state_trace():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("800")
    ctx = TradeContext(entry_bar={}, realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert all(v == Decimal("100") for v in out.values())  # 800/8 = 100
    assert sum(out.values()) == trade.profit_loss


def test_zero_blend_denominator_falls_back_to_equal():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("800")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {a: 0 for a in ad.LAYERS},
                   "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert sum(out.values()) == trade.profit_loss


def test_negative_alpha_gets_negative_share():
    """alpha_002 votes opposite to a POSITIVE blend → negative ρ → negative share.
    Note: the assertion requires the blend Σ(w·z) > 0. With alpha_002=-2.0 dominating
    7 alphas at +0.1, the blend would be -0.1625 (negative), making alpha_002 the
    dominant driver of the blend (positive share), NOT opposite to it. So we use a
    small-magnitude negative alpha_002 against larger positive peers → blend > 0."""
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("1000")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {"alpha_001": 0.5, "alpha_002": -0.3,
            "alpha_003": 0.5, "alpha_004": 0.5, "alpha_005": 0.5,
            "alpha_006": 0.5, "alpha_007": 0.5, "alpha_008": 0.5},
            "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert out["alpha_002"] < 0, "alpha_002 voted opposite blend -> negative share"
    assert sum(out.values()) == trade.profit_loss


def test_drift_absorbed_to_largest_alpha():
    """Decimal rounding drift absorbed so validated_layer_attribution never raises."""
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("333")  # 333/8 not exact -> drift.
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {a: 0.5 for a in ad.LAYERS},
                   "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.validated_layer_attribution(trade, ctx)  # should NOT raise.
    assert sum(out.values()) == trade.profit_loss
