"""Gold2ReviewAdapter — 4-layer telescoping PnL attribution. Spec §3.2, §3.5.

Decomposition (long-only; Δp = exit_price - entry_price, signed; LEAN ProfitLoss
for Long already applies +1):
    scale  = TPV_e / p_e
    w_trend= dir_coef;  w_vol = w_after_vol
    w_ext  = extreme_triggered ? min(w_vol, extreme_cap) : w_vol
    w_real = min(w_ext, realrate_cap)
    Q_layer= w_layer * scale;   δQ_lot = Q - Q_real   (<=0)
    C_trend        = Q_trend * Δp
    C_vol_target   = (Q_vol - Q_trend) * Δp
    C_extreme_risk = (Q_ext - Q_vol)   * Δp
    C_realrate     = (Q_real - Q_ext)  * Δp + δQ_lot * Δp

Invariant: C_trend + C_vol_target + C_extreme_risk + C_realrate
         = Q_real*Δp + δQ_lot*Δp = (Q_real+δQ_lot)*Δp = Q*Δp = Trade.ProfitLoss (gross).
Every intermediate Q_layer cancels with its neighbor; δQ_lot forced into C_realrate.
"""
from decimal import Decimal

from .base import StrategyReviewAdapter, TradeRecord, TradeContext


class Gold2ReviewAdapter(StrategyReviewAdapter):
    LAYERS = ["trend", "vol_target", "extreme_risk", "realrate_cap"]

    @property
    def sum_to_property(self) -> str:
        return "profit_loss"

    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        bar = (context.entry_bar if context else None) or None
        # Residual degradation FIRST (spec §3.5): no entry_bar → can't telescope.
        # trend = all PnL (Alpha-attributed residual), others = 0. No long-only guard
        # here — short trades fall through this path without raising (guard is only
        # on the telescoping path where the long-only formula would mis-attribute).
        if not bar:
            zero = Decimal("0")
            return {
                "trend": trade.profit_loss,
                "vol_target": zero,
                "extreme_risk": zero,
                "realrate_cap": zero,
            }

        # Telescoping path (spec §3.2) — long-only guard applies only here.
        # gold2 is long-only (518880 不可做空; Gold2TrendAlphaModel.cs:12,66-67:
        # dir<=0 → Flat, never Short). A short trade here means the formula would
        # mis-attribute; raise so the caller can fall back to residual explicitly.
        if trade.side != "long":
            raise ValueError(
                f"gold2 is long-only (518880 不可做空); got side={trade.side}. "
                f"Short trade must fall back to residual (§3.5), not telescoping."
            )

        dir_coef = Decimal(str(bar.get("dir_coef", 1.0)))
        w_vol = Decimal(str(bar.get("w_after_vol", 0.0)))
        extreme_triggered = bool(bar.get("extreme_triggered", False))
        extreme_cap = Decimal(str(bar.get("extreme_cap", 0.3)))
        realrate_cap = Decimal(str(bar.get("realrate_cap", 0.6)))

        scale = trade.tpv_entry / trade.entry_price if trade.entry_price != 0 else Decimal("0")
        w_trend = dir_coef
        w_ext = min(w_vol, extreme_cap) if extreme_triggered else w_vol
        w_real = min(w_ext, realrate_cap)

        Q_trend = w_trend * scale
        Q_vol = w_vol * scale
        Q_ext = w_ext * scale
        Q_real = w_real * scale
        dp = trade.exit_price - trade.entry_price
        # Recover realized Q: prefer realized_bar.w_realized (post-fill), else PL/dp (long).
        if context and context.realized_bar and "w_realized" in (context.realized_bar or {}):
            Q = Decimal(str(context.realized_bar["w_realized"])) * scale
        elif dp != 0:
            Q = trade.profit_loss / dp   # exact: ProfitLoss = Q*dp for long
        else:
            Q = Q_real  # no price move; δQ_lot = 0
        delta_q_lot = Q - Q_real  # lot-floor + execution residual (<=0 typically)

        C_trend = Q_trend * dp
        C_vol_target = (Q_vol - Q_trend) * dp
        C_extreme_risk = (Q_ext - Q_vol) * dp
        C_realrate = (Q_real - Q_ext) * dp + delta_q_lot * dp
        return {
            "trend": C_trend,
            "vol_target": C_vol_target,
            "extreme_risk": C_extreme_risk,
            "realrate_cap": C_realrate,
        }

    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        bar = (context.entry_bar if context else None) or None
        dp = trade.exit_price - trade.entry_price
        extreme_triggered = bool(bar.get("extreme_triggered", False)) if bar else False
        return {
            "entry_signal": (context.entry_signal if context else None) or {},
            "regime_at_entry": (context.regime_at_entry if context else None) or {},
            "extreme_cap_was_protective": dp < 0 and extreme_triggered,  # long-only narrative
            "attribution_method": "telescoping" if bar else "residual",
        }
