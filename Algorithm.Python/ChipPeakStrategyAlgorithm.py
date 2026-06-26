# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
from TopNEqualWeightPCM import TopNEqualWeightPCM
from ChipPeakAlphaModel import ChipPeakAlphaModel
from ChipPeakRiskManagementModel import ChipPeakRiskManagementModel
from AShareUniverseSelectionModel import AShareUniverseSelectionModel


class ChipPeakStrategyAlgorithm(QCAlgorithm):
    """
    筹码峰量化策略：全A股 + ETF，混合模式。
    五层架构：Universe → Alpha → Portfolio → Risk → Execution
    数据基础: cyq_perf (筹码及胜率)。
    """

    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2025, 12, 31)
        self.set_account_currency("CNY")
        self.set_cash("CNY", 1_000_000)

        data_folder = '/home/project/tushare-downloader/tushare_data_v2'

        # 1. Universe Selection
        self.set_universe_selection(AShareUniverseSelectionModel(data_folder, include_etf=True))
        # 2. Alpha
        self.set_alpha(ChipPeakAlphaModel(data_folder, top_n=20, batch_size=500))
        # 3. Portfolio Construction
        self.set_portfolio_construction(TopNEqualWeightPCM(top_n=20, rebalance=timedelta(days=7), no_trade_band=0.02))
        # 4. Risk Management
        self.set_risk_management(ChipPeakRiskManagementModel(data_folder))
        # 5. Execution
        self.set_execution(ImmediateExecutionModel())

        self.set_benchmark(lambda x: 0)
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())
        self.set_warm_up(60, Resolution.DAILY)

    def on_end_of_algorithm(self):
        self.log(f'[ChipPeak] final portfolio value: {self.portfolio.total_portfolio_value:,.2f}')
        self.log(f'[ChipPeak] total trades: {self.transactions.orders_count}')
