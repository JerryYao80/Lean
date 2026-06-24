# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
from VolatilityScalingPCM import VolatilityScalingPCM
from RegimeVolGatedAlphaModel import RegimeVolGatedAlphaModel


class VolRegimeHfChinaAlgorithm(QCAlgorithm):
    """Daily-proxy reproduction of Fang & Slepaczuk (2026):
    regime-aware volatility + XGBoost return prediction + Low-vol Gated
    Weekly Signal x Risk timing on CSI300 ETF (510300).
    """

    def initialize(self):
        self.set_start_date(2018, 1, 1)
        self.set_end_date(2025, 12, 31)
        self.set_cash(1_000_000)

        # A-share ETF: 510300 (Shanghai). Plain ticker; market inferred SSE.
        self.symbol = self.add_equity("510300", Resolution.DAILY, Market.SSE).symbol
        sec = self.securities[self.symbol]
        sec.set_fee_model(AShareStockFeeModel())
        sec.set_fill_model(AShareStockFillModel())
        sec.set_buying_power_model(AShareStockBuyingPowerModel())
        sec.set_settlement_model(DelayedSettlementModel(1, timedelta(hours=9)))

        self.set_benchmark(lambda x: 0)  # no benchmark
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())

        # 5-Step Framework
        self.set_universe_selection(ManualUniverseSelectionModel([self.symbol]))

        feature_csv = os.path.join(Globals.data_folder, 'alternative',
                                   'vol-regime', '510300_features.csv')
        self.set_alpha(RegimeVolGatedAlphaModel(feature_csv, self.symbol))

        self.set_portfolio_construction(
            VolatilityScalingPCM(rebalance=timedelta(days=7),
                                 w_max=0.6, no_trade_band=0.02))

        self.set_risk_management(MaximumDrawdownPercentPortfolio(0.15))
        self.set_execution(ImmediateExecutionModel())

        self.set_warm_up(300, Resolution.DAILY)