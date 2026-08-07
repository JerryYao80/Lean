from AlgorithmImports import *
import numpy as np

class SoloQuantGeneratedRLPortfolioAlgorithm(QCAlgorithm):
    def Initialize(self):
        # Parameters
        self.tickers = ["510300", "511010", "518880"] # CSI300, 10Y Bond, Gold
        self.lookback_window = 20
        self.rebalance_freq = 1 # Daily
        
        # Account Setup
        self.SetAccountCurrency("CNY")
        self.SetCash(100000)
        self.SetStartDate(2018, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # Benchmark
        self.SetBenchmark(lambda x: 0)
        
        # Universe & Security Setup
        self.symbols = []
        self.returns_history = {} # Symbol -> RollingWindow[float]
        self.previous_close = {}  # Symbol -> float
        self.high_water_mark = 0.0
        self.peak_portfolio_value = 0.0
        
        for ticker in self.tickers:
            equity = self.AddEquity(ticker, Resolution.Daily, Market.SSE)
            symbol = equity.Symbol
            self.symbols.append(symbol)
            
            # A-Share Models
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
            # Indicators / State
            self.returns_history[symbol] = RollingWindow[float](self.lookback_window)
            self.previous_close[symbol] = None
            
        self.SetWarmUp(self.lookback_window, Resolution.Daily)

    def OnData(self, slice):
        if self.IsWarmingUp: return
        
        # 1. Update State (Log Returns)
        for symbol in self.symbols:
            if slice.ContainsKey(symbol) and slice[symbol] is not None:
                price = slice[symbol].Close
                if self.previous_close[symbol] is not None and self.previous_close[symbol] > 0:
                    log_ret = Math.Log(price / self.previous_close[symbol])
                    self.returns_history[symbol].Add(log_ret)
                self.previous_close[symbol] = price
        
        # Check if all windows are ready
        if not all([w.IsReady for w in self.returns_history.values()]): return
        
        # 2. Risk Management (Pre-trade)
        current_value = self.Portfolio.TotalPortfolioValue
        
        # Max Drawdown Check (15%)
        if current_value > self.peak_portfolio_value:
            self.peak_portfolio_value = current_value
        
        if self.peak_portfolio_value > 0:
            drawdown = (self.peak_portfolio_value - current_value) / self.peak_portfolio_value
            if drawdown > 0.15:
                self.Liquidate()
                return
        
        # Trailing Stop Check (5%)
        if current_value > self.high_water_mark:
            self.high_water_mark = current_value
        
        if self.high_water_mark > 0 and current_value < self.high_water_mark * 0.95:
            self.Liquidate()
            return
            
        # 3. RL Agent Inference (Policy Forward Pass)
        # Construct State Vector: Flatten returns windows
        state = []
        for symbol in self.symbols:
            state.extend(list(self.returns_history[symbol]))
        
        # Placeholder Policy: Linear Softmax (Simulated)
        # In a real implementation, theta (weights) would be loaded here.
        # For this draft, we use a simple momentum heuristic to simulate the policy output.
        raw_scores = []
        for symbol in self.symbols:
            # Simple logic: positive avg return -> positive score
            avg_ret = sum(self.returns_history[symbol]) / self.lookback_window
            raw_scores.append(max(0.01, avg_ret + 0.001)) # Ensure positive for softmax
        
        # Softmax to get weights (Simplex Projection)
        exp_scores = [np.exp(x) for x in raw_scores]
        sum_exp = sum(exp_scores)
        weights = [x / sum_exp for x in exp_scores]
        
        # 4. Execution
        for i, symbol in enumerate(self.symbols):
            target_weight = weights[i]
            target_value = current_value * target_weight
            
            # A-Share Lot Size: 100 shares
            current_price = self.Securities[symbol].Price
            if current_price == 0: continue
            
            target_qty = int(target_value / current_price / 100) * 100
            current_qty = self.Portfolio[symbol].Quantity
            
            delta = target_qty - current_qty
            
            if delta != 0:
                self.MarketOrder(symbol, delta)