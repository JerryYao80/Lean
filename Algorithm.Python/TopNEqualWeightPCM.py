# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import math


class TopNEqualWeightPCM(PortfolioConstructionModel):
    """Equal-weight the Top-N symbols by Insight magnitude, long-only, weekly rebalance.
    A-share aware: rounds target quantity to nearest 100-share lot.
    """

    def __init__(self, top_n=20, rebalance=timedelta(days=7), no_trade_band=0.02):
        self.top_n = int(top_n)
        self.rebalance_period = rebalance
        self.no_trade_band = float(no_trade_band)
        self._previous_targets = {}
        self._next_rebalance = None

    def create_targets(self, algorithm, insights):
        if not insights:
            return []

        now = algorithm.utc_time
        if self._next_rebalance is None:
            self._next_rebalance = now + self.rebalance_period
        if now < self._next_rebalance and self._previous_targets:
            return []
        self._next_rebalance = now + self.rebalance_period

        # rank by magnitude, keep top_n UP insights
        ups = [i for i in insights if i.direction == InsightDirection.UP
               and i.magnitude is not None]
        ups.sort(key=lambda i: float(i.magnitude), reverse=True)
        selected = ups[:self.top_n]
        selected_symbols = {i.symbol for i in selected}

        targets = []
        # liquidate anything previously held but no longer selected
        for sym, _ in list(self._previous_targets.items()):
            if sym not in selected_symbols:
                if abs(self._previous_targets[sym]) > self.no_trade_band:
                    targets.append(PortfolioTarget(sym, 0.0))
                self._previous_targets[sym] = 0.0

        n = len(selected)
        if n == 0:
            return targets
        weight = 1.0 / n
        for insight in selected:
            qty = self._weight_to_quantity(algorithm, insight.symbol, weight)
            prev = self._previous_targets.get(insight.symbol, 0.0)
            if abs(weight - prev) < self.no_trade_band and prev > 0:
                continue  # no-trade band
            targets.append(PortfolioTarget(insight.symbol, qty))
            self._previous_targets[insight.symbol] = weight
        return targets

    @staticmethod
    def _weight_to_quantity(algorithm, symbol, weight):
        security = algorithm.securities[symbol]
        price = security.price
        if price <= 0:
            return 0.0
        target_value = weight * algorithm.portfolio.total_portfolio_value
        raw_qty = target_value / price
        lot = 100.0
        return math.copysign(round(abs(raw_qty) / lot) * lot, raw_qty)
