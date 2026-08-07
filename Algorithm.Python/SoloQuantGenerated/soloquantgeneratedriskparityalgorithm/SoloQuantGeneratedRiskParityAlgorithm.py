from AlgorithmImports import *
import numpy as np

class SoloQuantGeneratedRiskParityAlgorithm(QCAlgorithm):
    def Initialize(self):
        # Set Start and End dates from summary
        self.SetStartDate(2007, 3, 21)
        self.SetEndDate(2021, 3, 19)
        self.SetCash(100000)
        
        # Set Warmup for volatility calculation (126 days)
        self.SetWarmUp(126)
        
        # Universe from summary: SPY, EFA, GLD, IEF
        # Using Market.USA as per summary universe (US ETFs)
        self.tickers = ["SPY", "EFA", "GLD", "IEF"]
        self.symbols = [self.AddEquity(ticker, Resolution.Daily).Symbol for ticker in self.tickers]
        
        # Benchmark: Set to 0 to avoid data dependency as per constraints
        self.SetBenchmark(lambda x: 0)
        
        # Schedule Rebalance: Weekly (Friday calculation, Monday execution)
        # Using Friday for calculation, execution happens next trading day (Monday or similar)
        self.Schedule.On(self.DateRules.Every(DayOfWeek.Friday), self.TimeRules.AfterMarketOpen("SPY", 30), self.Rebalance)
        
        self.rebalance_flag = False
        
        # Risk Management Parameters
        self.max_drawdown_pct = 0.20 # 20% Max Drawdown
        self.trailing_stop_pct = 0.05 # 5% Trailing Stop
        self.peak_equity = self.Portfolio.TotalPortfolioValue
        self.highest_price = {symbol: 0 for symbol in self.symbols}

    def OnData(self, slice):
        # Update Peak Equity
        if self.Portfolio.TotalPortfolioValue > self.peak_equity:
            self.peak_equity = self.Portfolio.TotalPortfolioValue
            
        # Max Drawdown Check
        current_drawdown = (self.peak_equity - self.Portfolio.TotalPortfolioValue) / self.peak_equity
        if current_drawdown > self.max_drawdown_pct:
            self.Liquidate()
            return
            
        # Trailing Stop Check
        for symbol in self.symbols:
            if self.Portfolio[symbol].Invested and slice.ContainsKey(symbol):
                current_price = slice[symbol].Price
                if current_price > self.highest_price[symbol]:
                    self.highest_price[symbol] = current_price
                
                if self.highest_price[symbol] > 0:
                    drawdown_from_high = (self.highest_price[symbol] - current_price) / self.highest_price[symbol]
                    if drawdown_from_high > self.trailing_stop_pct:
                        self.Liquidate(symbol)
                        self.highest_price[symbol] = 0

        # Execute Rebalance if flag is set
        if self.rebalance_flag:
            self.ExecuteRebalance()
            self.rebalance_flag = False

    def Rebalance(self):
        # Set flag to rebalance in OnData (skip-day logic handled by scheduling on Friday, executing on next open)
        self.rebalance_flag = True

    def ExecuteRebalance(self):
        # Fetch 126 days of history
        history = self.History(self.symbols, 126, Resolution.Daily)
        if history.empty: return

        # Calculate daily returns
        df = history['close'].unstack(level=0)
        returns = df.pct_change().dropna()
        
        if len(returns) < 60: return # Ensure enough data

        # Calculate Volatility (Standard Deviation)
        vol = returns.std()
        
        # Naive Risk Parity: Weight proportional to 1/Volatility
        inv_vol = 1 / vol
        weights = inv_vol / inv_vol.sum()
        
        # Execute Trades
        for symbol, weight in zip(self.symbols, weights):
            self.SetHoldings(symbol, weight)
