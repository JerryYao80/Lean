from AlgorithmImports import *

class SoloQuantGeneratedDeepLearningStraddleAlgorithm(QCAlgorithm):
    def Initialize(self):
        # A-share Account Setup
        self.SetAccountCurrency('CNY')
        self.SetCash(100000)
        
        # Time Settings (from summary)
        self.SetStartDate(2018, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # Benchmark to avoid data dependency
        self.SetBenchmark(lambda x: 0)
        
        # Universe Selection: Using CSI 300 ETF as proxy for index options underlying
        self.symbol = self.AddEquity("510300", Resolution.Daily, Market.SSE).Symbol
        
        # A-share Specific Models Configuration
        security = self.Securities[self.symbol]
        security.FeeModel = AShareStockFeeModel()
        security.FillModel = AShareStockFillModel()
        security.BuyingPowerModel = AShareStockBuyingPowerModel()
        security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
        
        # Indicators for Signals
        self.roc = self.ROC(self.symbol, 20, Resolution.Daily) # 20-day return for TSMOM
        self.macd = self.MACD(self.symbol, 12, 26, 9, MovingAverageType.Exponential, Resolution.Daily)
        self.std = self.STD(self.symbol, 20, Resolution.Daily) # 20-day EWMA proxy
        
        # Risk Management State
        self.highest_price = 0
        self.peak_portfolio_value = self.Portfolio.TotalPortfolioValue
        self.max_drawdown_limit = 0.20 # 20% Max Drawdown
        self.trailing_stop_pct = 0.05 # 5% Trailing Stop
        
        self.SetWarmUp(30)

    def OnData(self, slice):
        if self.IsWarmingUp or not self.symbol in slice:
            return
        
        if not self.roc.IsReady or not self.macd.IsReady or not self.std.IsReady:
            return
            
        price = self.Securities[self.symbol].Price
        
        # 1. Risk Controls: Max Drawdown Check
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.peak_portfolio_value:
            self.peak_portfolio_value = current_value
            
        drawdown = (self.peak_portfolio_value - current_value) / self.peak_portfolio_value
        if drawdown > self.max_drawdown_limit:
            self.Liquidate(self.symbol)
            return
            
        # 2. Risk Controls: Trailing Stop
        if price > self.highest_price:
            self.highest_price = price
            
        if price < self.highest_price * (1 - self.trailing_stop_pct):
            self.Liquidate(self.symbol)
            self.highest_price = 0 # Reset after liquidation
            return

        # 3. Signal Generation (TSMOM + MACD)
        # TSMOM: sign(r_{t-20, t})
        roc_val = self.roc.Current.Value
        ts_mom_signal = 1 if roc_val > 0 else -1
        
        # MACD Momentum: sign(MACD)
        macd_val = self.macd.Current.Value
        macd_signal = 1 if macd_val > 0 else -1
        
        # Combined Signal (Simple average for demonstration)
        combined_signal = (ts_mom_signal + macd_signal) / 2.0
        
        # 4. Volatility Targeting
        # Target 15% annualized vol
        target_vol = 0.15
        # Current annualized vol (approx)
        current_vol = self.std.Current.Value * (252**0.5)
        if current_vol < 0.0001: current_vol = 0.0001
        
        vol_scale = target_vol / current_vol
        # Clamp scale to reasonable limits (e.g., max 3x leverage)
        vol_scale = min(max(vol_scale, 0.1), 3.0)
        
        # 5. Position Sizing
        target_value = combined_signal * vol_scale * self.Portfolio.TotalPortfolioValue
        target_shares = int(target_value / price)
        
        # A-share Lot Size (100 shares)
        target_shares = round(target_shares / 100) * 100
        
        # 6. Execution
        current_shares = self.Portfolio[self.symbol].Quantity
        delta = target_shares - current_shares
        
        if delta != 0:
            self.MarketOrder(self.symbol, delta)