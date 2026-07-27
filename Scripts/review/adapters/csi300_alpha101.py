"""Csi300Alpha101ReviewAdapter — per-alpha PnL attribution for the 8-alpha
composite strategy. Spec §5.4 (proportional-z), §3.5 (residual).

Attribution method (proportional-z):
  At rebalance t_e for symbol s, composite z = Σ_i (w_i * z_i,t,s).
  P = trade.profit_loss. We attribute:
      ρ_i = (w_i * z_i) / Σ_j (w_j * z_j)   (signed denominator)
      C_i = ρ_i * P
  Σ_i C_i = (Σ_i ρ_i) * P = 1 * P = P  (exact by construction).
  An alpha voting opposite the blend gets negative ρ_i.

Residual paths (sum still = P):
  - no entry_bar           → equal split C_i = P/8
  - zero-blend denominator → equal split
  - partial z (some missing)→ redistribute among present

State_trace contract: entry_bar (ts <= entry_time, no-lookahead) carries
  alpha_zscores {alpha_id: float} + alpha_weights {alpha_id: float} (default 1/8).

NOTE: LAYERS are LAYER ROLE names (alpha_001..alpha_008), NOT literal alpha ids.
This is so a refactor replacement alpha#Y can be attributed under the SAME
inspired_layer key (spec §5.5 critical contract for verify_layer_improvement).
"""
from decimal import Decimal

from .base import StrategyReviewAdapter, TradeRecord, TradeContext


class Csi300Alpha101ReviewAdapter(StrategyReviewAdapter):
    """Per-alpha attribution for the CSI300 8-alpha composite strategy."""

    # MUST match manifest review.layer_names exactly.
    LAYERS = [
        "alpha_001", "alpha_002", "alpha_003", "alpha_004",
        "alpha_005", "alpha_006", "alpha_007", "alpha_008",
    ]
    N_LAYERS = len(LAYERS)
    _EQUAL_WEIGHT = Decimal(1) / Decimal(N_LAYERS)

    @property
    def sum_to_property(self) -> str:
        return "profit_loss"

    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict:
        P = trade.profit_loss
        bar = (context.entry_bar if context else None) or {}

        # Residual 1: no state_trace → equal split (max-entropy prior).
        if not bar:
            equal = P / self.N_LAYERS
            return {ln: equal for ln in self.LAYERS}

        z_scores = bar.get("alpha_zscores") or {}
        weights = bar.get("alpha_weights") or {}
        default_w = self._EQUAL_WEIGHT

        numerators = {}
        for ln in self.LAYERS:
            z = z_scores.get(ln)
            w = weights.get(ln, default_w)
            if z is None:
                continue  # partial → skip, redistribute
            numerators[ln] = Decimal(str(w)) * Decimal(str(z))

        denom = sum(numerators.values())
        if not numerators or denom == 0:
            equal = P / self.N_LAYERS
            return {ln: equal for ln in self.LAYERS}

        out = {}
        for ln in self.LAYERS:
            num = numerators.get(ln)
            out[ln] = (num * P) / denom if num is not None else Decimal("0")

        # Absorb sub-ulp Decimal rounding drift into largest-magnitude alpha.
        drift = P - sum(out.values())
        if drift != 0:
            anchor = max(out, key=lambda k: abs(out[k]))
            out[anchor] += drift
        return out

    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        bar = (context.entry_bar if context else None) or {}
        z_scores = bar.get("alpha_zscores") or {}
        weights = bar.get("alpha_weights") or {}
        composite_z = bar.get("composite_z")
        denom = sum(
            Decimal(str(weights.get(ln, self._EQUAL_WEIGHT))) * Decimal(str(z_scores.get(ln, 0)))
            for ln in self.LAYERS)
        if not bar or denom == 0:
            method = "residual-equal" if not bar else "proportional-z-zero-blend"
        elif not z_scores:
            method = "residual-equal"
        elif len(z_scores) < self.N_LAYERS:
            method = "proportional-z-partial"
        else:
            method = "proportional-z"
        return {
            "entry_signal": {"alpha_zscores": z_scores, "alpha_weights": weights,
                             "composite_z": composite_z},
            "regime_at_entry": (context.regime_at_entry if context else None) or {},
            "attribution_method": method,
            "insight_realized_vs_predicted": {"method": method},
        }
