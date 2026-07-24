# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *


class VolatilityScalingPCM(PortfolioConstructionModel):
    """Low-vol Gated Weekly Signal x Risk portfolio construction (Fang & Slepaczuk 2026).

    - weekly rebalance
    - position cap w_max (default 0.6)
    - no-trade band b (default 0.02): suppress small weight changes
    The Insight magnitude is the pre-scaled target weight signal s_tilde.
    """

    def __init__(self, rebalance=timedelta(days=7), w_max=0.6, no_trade_band=0.02):
        self.rebalance_period = rebalance
        self.w_max = float(w_max)
        self.no_trade_band = float(no_trade_band)
        self._previous_targets = {}
        self._next_rebalance = None

    def create_targets(self, algorithm, insights):
        targets = []
        if not insights:
            return targets

        now = algorithm.utc_time
        if self._next_rebalance is None:
            self._next_rebalance = now + self.rebalance_period

        latest = {}
        for insight in insights:
            if insight.magnitude is None:
                continue
            latest[insight.symbol] = float(insight.magnitude)

        if now < self._next_rebalance and self._previous_targets:
            return targets  # not a rebalance day; hold existing

        self._next_rebalance = now + self.rebalance_period

        for symbol, signal in latest.items():
            target_w = max(-self.w_max, min(self.w_max, signal))
            prev = self._previous_targets.get(symbol, 0.0)
            if abs(target_w - prev) < self.no_trade_band:
                target_w = prev  # no-trade band
            qty = self._weight_to_quantity(algorithm, symbol, target_w)
            targets.append(PortfolioTarget(symbol, qty))
            self._previous_targets[symbol] = target_w
        return targets

    @staticmethod
    def _weight_to_quantity(algorithm, symbol, weight):
        security = algorithm.securities[symbol]
        price = security.price
        if price <= 0:
            return 0.0
        target_value = weight * algorithm.portfolio.total_portfolio_value
        raw_qty = target_value / price
        # A-share: round to nearest 100-share lot (AShareStockFillModel rejects
        # non-multiples of 100). Round half away from zero so signs are preserved.
        lot = 100.0
        rounded = math.copysign(round(abs(raw_qty) / lot) * lot, raw_qty)
        return rounded
