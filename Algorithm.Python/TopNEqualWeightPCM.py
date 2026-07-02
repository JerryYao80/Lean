# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import math


class TopNEqualWeightPCM(PortfolioConstructionModel):
    """Equal-weight the Top-N symbols by Insight magnitude, long-only, weekly rebalance.
    A-share aware: rounds target quantity to nearest 100-share lot.

    CRITICAL: Reserves margin for fees and slippage to prevent "Insufficient buying power" errors.
    A-share fees (buy): commission 0.03% + transfer 0.001% ≈ 0.031%
    A-share fees (sell): commission 0.03% + stamp duty 0.05% + transfer 0.001% ≈ 0.081%
    Slippage estimate: 0.1% (conservative for market orders)
    Total reserve: 0.15% per position (covers fees + slippage on both buy and sell)
    """

    # Fee/slippage reserve: 3% per position for margin accounts
    # Margin accounts require additional buffer for:
    # - A-share fees (commission 0.03% + transfer 0.001% + stamp duty 0.05%) ≈ 0.081%
    # - Slippage estimate: 0.1-0.2%
    # - Initial margin requirement buffer: 1-2%
    # Total reserve: 3% per position ensures sufficient buying power
    FEE_SLIPPAGE_RESERVE = 0.03  # 3%

    def __init__(self, top_n=20, rebalance=timedelta(days=7), no_trade_band=0.02):
        self.top_n = int(top_n)
        self.rebalance_period = rebalance
        self.no_trade_band = float(no_trade_band)
        self._previous_targets = {}
        self._next_rebalance = None

    def create_targets(self, algorithm, insights):
        # DEBUG: Log what we receive
        algorithm.debug(f'[TopNPCM] create_targets called: {len(insights)} insights received')

        if not insights:
            algorithm.debug('[TopNPCM] no insights, returning empty targets')
            return []

        now = algorithm.utc_time
        if self._next_rebalance is None:
            self._next_rebalance = now + self.rebalance_period
            algorithm.debug(f'[TopNPCM] first call, setting next_rebalance={self._next_rebalance}')
        if now < self._next_rebalance and self._previous_targets:
            algorithm.debug(f'[TopNPCM] skipping: now={now} < next_rebalance={self._next_rebalance}')
            return []
        self._next_rebalance = now + self.rebalance_period
        algorithm.debug(f'[TopNPCM] rebalance triggered, next_rebalance={self._next_rebalance}')

        # rank by magnitude, keep top_n UP insights
        ups = [i for i in insights if i.direction == InsightDirection.UP
               and i.magnitude is not None]
        algorithm.debug(f'[TopNPCM] filtered to {len(ups)} UP insights with magnitude')
        ups.sort(key=lambda i: float(i.magnitude), reverse=True)
        selected = ups[:self.top_n]
        selected_symbols = {i.symbol for i in selected}
        algorithm.debug(f'[TopNPCM] selected top {len(selected)}: {[str(s.symbol.value) for s in selected]}')

        targets = []
        # liquidate anything previously held but no longer selected
        for sym, _ in list(self._previous_targets.items()):
            if sym not in selected_symbols:
                if abs(self._previous_targets[sym]) > self.no_trade_band:
                    targets.append(PortfolioTarget(sym, 0.0))
                self._previous_targets[sym] = 0.0

        n = len(selected)
        if n == 0:
            algorithm.debug('[TopNPCM] no selected insights after filtering')
            return targets

        # Calculate weight with fee/slippage reserve
        # This prevents "Insufficient buying power" when fees are deducted
        raw_weight = 1.0 / n
        weight = raw_weight * (1.0 - self.FEE_SLIPPAGE_RESERVE)

        # CRITICAL FIX: Scale down weight if insufficient margin
        # For margin accounts with leverage=1, need to ensure we have enough free margin
        total_portfolio_value = algorithm.portfolio.total_portfolio_value
        free_margin = algorithm.portfolio.margin_remaining  # Available margin (LEAN API)

        # Calculate total target value for all positions
        total_target_value = n * weight * total_portfolio_value

        # If free margin is insufficient, scale down weights proportionally
        if free_margin < total_target_value and free_margin > 0:
            scale_factor = free_margin / total_target_value
            weight *= scale_factor
            algorithm.debug(f'[TopNPCM] SCALED DOWN: free_margin={free_margin:.0f} < total_target={total_target_value:.0f}, scale={scale_factor:.2f}')

        algorithm.debug(f'[TopNPCM] weight={weight:.4f} (raw={raw_weight:.4f}, reserve={self.FEE_SLIPPAGE_RESERVE:.4f}), n={n}')

        for insight in selected:
            qty = self._weight_to_quantity(algorithm, insight.symbol, weight)
            prev = self._previous_targets.get(insight.symbol, 0.0)
            if abs(weight - prev) < self.no_trade_band and prev > 0:
                algorithm.debug(f'[TopNPCM] skip {insight.symbol.value}: no-trade band')
                continue  # no-trade band
            algorithm.debug(f'[TopNPCM] target: {insight.symbol.value} qty={qty:.0f} @ price={algorithm.securities[insight.symbol].price:.2f}')
            targets.append(PortfolioTarget(insight.symbol, qty))
            self._previous_targets[insight.symbol] = weight
        algorithm.debug(f'[TopNPCM] returning {len(targets)} targets')
        return targets

    @staticmethod
    def _weight_to_quantity(algorithm, symbol, weight):
        """
        Convert portfolio weight to trading quantity with A-share lot sizing logic.
        Supports both traditional 100-share lots and micro-cap 1-share min.
        """
        security = algorithm.securities[symbol]
        price = security.price

        # DEBUG: Log price and lot calculation
        algorithm.debug(f'[TopNPCM] _weight_to_quantity: symbol={symbol.value}, price={price:.2f}, weight={weight:.4f}')

        if price <= 0:
            algorithm.debug(f'[TopNPCM] _weight_to_quantity: price <= 0, returning 0')
            return 0.0

        target_value = weight * algorithm.portfolio.total_portfolio_value
        raw_qty = target_value / price

        # DEBUG: Log price, target_value, raw_qty before lot calculation
        algorithm.debug(f'[TopNPCM] _weight_to_quantity: target_value={target_value:.2f}, raw_qty={raw_qty:.2f}')

        # A-share lot sizing:
        # - Traditional stocks (price >= 10 CNY): 1 lot = 100 shares
        # - Micro caps (price < 10 CNY): 1 lot = 1 share
        # This ensures at least 1 share can be purchased for all prices
        lot = 100.0 if price >= 10.0 else 1.0

        # DEBUG: Log lot selection
        algorithm.debug(f'[TopNPCM] _weight_to_quantity: selected lot={lot} (price={price:.2f} CNY)')

        # Calculate rounded quantity
        qty = math.copysign(round(abs(raw_qty) / lot) * lot, raw_qty)

        # DEBUG: Log final quantity
        algorithm.debug(f'[TopNPCM] _weight_to_quantity: final qty={qty:.0f} shares')

        return qty
