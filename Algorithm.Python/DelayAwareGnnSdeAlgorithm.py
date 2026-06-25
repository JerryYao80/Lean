# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
from TopNEqualWeightPCM import TopNEqualWeightPCM
from DelayAwareSlopeAlphaModel import DelayAwareSlopeAlphaModel
import os


class DelayAwareGnnSdeAlgorithm(QCAlgorithm):
    """DGNSDE core-idea approximation on A-share (CSI300).
    Delay-aware lead-lag features + walk-forward XGBoost slope prediction.
    """

    def initialize(self):
        self.set_start_date(2020, 1, 1)
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

        feature_paths = [
            os.path.join(Globals.data_folder, 'alternative', 'delay-aware-gnn-sde',
                         'csi300_features.csv'),
            os.path.join(Globals.data_folder, 'alternative', 'delay-aware-gnn-sde',
                         'csi500_features.csv'),
        ]
        self.set_universe_selection(ManualUniverseSelectionModel(list(self._equities.keys())))
        self.set_alpha(DelayAwareSlopeAlphaModel(feature_paths, top_n=20))
        self.set_portfolio_construction(
            TopNEqualWeightPCM(top_n=20, rebalance=timedelta(days=7), no_trade_band=0.02))
        self.set_risk_management(MaximumDrawdownPercentPortfolio(0.15))
        self.set_execution(ImmediateExecutionModel())

        self.set_warm_up(60, Resolution.DAILY)

    def _load_constituents(self):
        """Load CSI300 constituent ts_codes (capped to keep backtest tractable)."""
        from pathlib import Path
        import glob
        base = Path('/home/project/tushare-downloader/tushare_data_v2/index_weight')
        codes = set()
        files = sorted(glob.glob(str(base / 'trade_date=*')))
        for f in reversed(files[-10:]):
            try:
                import pandas as pd
                df = pd.read_parquet(f)
                sub = df[df['index_code'] == '000300.SH'] if 'index_code' in df.columns else df
                if len(sub) > 0:
                    col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
                    codes.update(sub[col].astype(str).tolist())
                    if len(codes) > 100:
                        break
            except Exception:
                continue
        codes = sorted(codes)[:300]
        self.log(f'[delay-aware] universe size: {len(codes)}')
        return codes