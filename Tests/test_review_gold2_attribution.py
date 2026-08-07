"""Tests for Gold2ReviewAdapter 4-layer telescoping attribution. Spec §3.2, §3.5, §6.1."""
import sys
from decimal import Decimal
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from adapters.gold2 import Gold2ReviewAdapter  # noqa: E402


def _trade(profit_loss="100.0", entry="10.0", exit_p="11.0", side="long"):
    return TradeRecord(
        symbol="518880", entry_time="2020-07-01", entry_price=Decimal(entry),
        exit_time="2020-07-05", exit_price=Decimal(exit_p),
        quantity=Decimal("100"), side=side,
        profit_loss=Decimal(profit_loss), fees=Decimal("0"),
        tpv_entry=Decimal("1000.0"),
    )


def _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=False, extreme_cap=0.3,
         realrate_cap=0.6, trend_disabled=False):
    return TradeContext(entry_bar={
        "dir_coef": dir_coef, "w_after_vol": w_after_vol,
        "extreme_triggered": extreme_triggered, "extreme_cap": extreme_cap,
        "realrate_cap": realrate_cap, "trend_disabled": trend_disabled,
    })


def test_layers_constant():
    assert Gold2ReviewAdapter.LAYERS == ["trend", "vol_target", "extreme_risk", "realrate_cap"]


def test_sum_to_profit_loss_no_caps():
    """extreme/realrate not triggered: w_real = w_vol, δQ_lot = 0, sum == profit_loss."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=False, realrate_cap=0.6)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_extreme_triggered():
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=True, extreme_cap=0.3, realrate_cap=0.6)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_both_caps():
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, extreme_triggered=True, extreme_cap=0.3, realrate_cap=0.2)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_sum_to_profit_loss_trend_disabled():
    """trend-disabled: dir_coef=1.0, trend layer still carries the directional contribution."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = _ctx(dir_coef=1.0, w_after_vol=0.5, trend_disabled=True)
    layers = ad.layer_attribution(t, c)
    assert sum(layers.values()) == pytest.approx(Decimal("100.0"), abs=Decimal("1e-9"))


def test_property_test_random_sums():
    """Random sane inputs: |sum - profit_loss| < 1e-9 (Decimal). Spec §6.1 property test."""
    import random
    ad = Gold2ReviewAdapter()
    rng = random.Random(42)
    for _ in range(200):
        dp = Decimal(str(rng.uniform(-5, 5)))   # Δp
        entry = Decimal("10.0")
        exit_p = entry + dp
        qty = Decimal(str(rng.randint(10, 1000)))
        profit_loss = dp * qty  # long: ProfitLoss = (exit-entry)*qty
        t = TradeRecord("518880", "t0", entry, "t1", exit_p, qty, "long",
                        profit_loss, Decimal("0"), Decimal("10000"))
        c = _ctx(
            dir_coef=Decimal(str(rng.choice([0.2, 0.5, 1.0]))),
            w_after_vol=Decimal(str(rng.uniform(0.05, 0.8))),
            extreme_triggered=rng.random() < 0.3,
            extreme_cap=Decimal("0.3"),
            realrate_cap=Decimal(str(rng.choice([0.2, 0.4, 0.6]))),
        )
        layers = ad.layer_attribution(t, c)
        assert abs(sum(layers.values()) - profit_loss) < Decimal("1E-9"), \
            f"sum {sum(layers.values())} != {profit_loss} for ctx {c.entry_bar}"


def test_long_only_guard_raises_on_short():
    """Spec §3.2 long-only: short trade must raise, never silently apply long formula."""
    ad = Gold2ReviewAdapter()
    t = _trade(side="short")
    c = _ctx()
    with pytest.raises(ValueError, match="long-only"):
        ad.layer_attribution(t, c)


def test_residual_degradation_when_no_state_trace():
    """Spec §3.5: state_trace absent → residual decomposition. trend=all PnL, others=0,
    attribution_method='residual'."""
    ad = Gold2ReviewAdapter()
    t = _trade()
    c = TradeContext(entry_bar=None, realized_bar=None)  # no state_trace
    layers = ad.layer_attribution(t, c)
    assert layers["trend"] == Decimal("100.0")
    assert layers["vol_target"] == Decimal("0")
    assert layers["extreme_risk"] == Decimal("0")
    assert layers["realrate_cap"] == Decimal("0")
    assert sum(layers.values()) == Decimal("100.0")
    narrative = ad.trade_narrative(t, c)
    assert narrative["attribution_method"] == "residual"


def test_residual_short_trade_does_not_raise():
    """In residual mode, short trade does NOT hit the long-only guard (guard only on telescoping)."""
    ad = Gold2ReviewAdapter()
    t = _trade(side="short", profit_loss="-50.0", entry="11.0", exit_p="10.5")
    c = TradeContext(entry_bar=None, realized_bar=None)
    layers = ad.layer_attribution(t, c)  # should not raise
    assert sum(layers.values()) == Decimal("-50.0")
