# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
from NetworkCentralityAlphaModel import NetworkCentralityAlphaModel
import os


class HuaxiNetworkCentralityAlgorithm(QCAlgorithm):
    """Huaxi Securities (2021-03-14): Network Centrality Factor Strategy.

    Uses CC (combined network centrality = 0.5*SCC + 0.5*TCC) as the ranking
    signal. PMFG networks built offline from 252-day return correlations.
    Long low-CC stocks (top 20% = ~60 names), monthly rebalance, CSI300.

    Reference: 华西证券《股票网络与网络中心度因子研究》(2021-03-14)
    """

    def initialize(self):
        self.set_start_date(2015, 1, 1)
        self.set_end_date(2025, 12, 31)
        self.set_account_currency("CNY")
        self.set_cash("CNY", 1_000_000)

        self._equities = {}
        for code in self._load_constituents():
            ticker = code.split('.')[0]
            market = Market.SSE if code.endswith('.SH') else Market.SZSE
            try:
                sec = self.add_equity(ticker, Resolution.DAILY, market)
            except Exception:
                continue
            symbol = sec.symbol
            s = self.securities[symbol]
            s.set_fee_model(AShareStockFeeModel())
            s.set_fill_model(AShareStockFillModel())
            s.set_buying_power_model(AShareStockBuyingPowerModel())
            s.set_settlement_model(DelayedSettlementModel(1, timedelta(hours=9)))
            self._equities[symbol] = code

        self.set_benchmark(lambda x: 0)
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())

        feature_path = os.path.join(Globals.data_folder, 'alternative',
                                    'huaxi-network-centrality', 'factors.csv')
        self.set_universe_selection(ManualUniverseSelectionModel(list(self._equities.keys())))
        self.set_alpha(NetworkCentralityAlphaModel(feature_path, top_n=60))
        self.set_portfolio_construction(EqualWeightingPortfolioConstructionModel())
        self.set_risk_management(NullRiskManagementModel())
        self.set_execution(ImmediateExecutionModel())

        self.set_warm_up(252, Resolution.DAILY)

    def _load_constituents(self):
        """Load CSI300 constituent ts_codes from tushare_data_v2/index_weight."""
        from pathlib import Path
        import glob
        base = Path('/home/project/tushare-downloader/tushare_data_v2/index_weight')
        codes = set()
        files = sorted(glob.glob(str(base / 'trade_date=*')))
        for f in reversed(files[-200:]):
            try:
                import pandas as pd
                df = pd.read_parquet(f)
                if len(df) == 0 or 'index_code' not in df.columns:
                    continue
                sub = df[df['index_code'] == '000300.SH']
                if len(sub) > 0:
                    col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
                    codes.update(sub[col].astype(str).tolist())
                    if len(codes) > 100:
                        break
            except Exception:
                continue
        codes = sorted(codes)[:300]
        self.log(f'[huaxi-centrality] universe size: {len(codes)}')
        return codes