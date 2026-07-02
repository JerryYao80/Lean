# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.
#
# ChipPeakStrategyAlgorithm (因子动物园版)
# 使用 C# ChipPeakFactorZooAlphaModel 替代纯 Python AlphaModel,
# 通过 FactorRegistry 实现 alpha 信号的三层管线 (Factor → Alpha → Strategy).

from AlgorithmImports import *
from TopNEqualWeightPCM import TopNEqualWeightPCM
from ChipPeakRiskManagementModel import ChipPeakRiskManagementModel
from AShareUniverseSelectionModel import AShareUniverseSelectionModel
from QuantConnect.Algorithm.CSharp.Models import ChipPeakFactorZooAlphaModel


class ChipPeakStrategyAlgorithm(QCAlgorithm):
    """
    筹码峰量化策略 (因子动物园版): 全A股 + ETF, 混合模式.
    五层架构: Universe → Alpha(因子动物园) → Portfolio → Risk → Execution
    数据基础: cyq_perf (筹码及胜率).
    """

    def initialize(self):
        # 快速回测配置: 3个月 + Top 5
        self.set_start_date(2025, 10, 1)
        self.set_end_date(2025, 12, 31)
        self.set_account_currency("CNY")
        self.set_cash("CNY", 1_000_000)

        data_folder = '/home/project/tushare-downloader/tushare_data_v2'

        # 1. Universe Selection
        self.set_universe_selection(AShareUniverseSelectionModel(data_folder, include_etf=True))
        # 2. Alpha - 因子动物园版: C# AlphaModel 通过 FactorRegistry 注入 composite_score
        self.set_alpha(ChipPeakFactorZooAlphaModel(data_folder, top_n=5, batch_size=500, max_workers=2))
        # 3. Portfolio Construction
        self.set_portfolio_construction(TopNEqualWeightPCM(top_n=5, rebalance=timedelta(days=7), no_trade_band=0.02))
        # 4. Risk Management
        self.set_risk_management(ChipPeakRiskManagementModel(data_folder))
        # 5. Execution
        self.set_execution(ImmediateExecutionModel())

        self.set_benchmark(lambda x: 0)
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())
        self.set_warm_up(30, Resolution.DAILY)

    def on_end_of_algorithm(self):
        self.log(f'[ChipPeak-FactorZoo] final portfolio value: {self.portfolio.total_portfolio_value:,.2f}')
        self.log(f'[ChipPeak-FactorZoo] total trades: {self.transactions.orders_count}')
