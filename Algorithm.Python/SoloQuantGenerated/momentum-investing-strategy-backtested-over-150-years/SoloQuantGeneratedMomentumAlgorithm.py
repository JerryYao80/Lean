from AlgorithmImports import *

class SoloQuantGeneratedMomentumAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency('CNY')
        self.SetCash(1000000)
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2023, 12, 31)
        self.SetBenchmark(lambda x: 0)
        
        # A-share Universe (Hardcoded representative stocks for demo)
        tickers = ['600519', '000858', '600036', '000001', '601318', '000002', '600276', '300750']
        self.symbols = [self.AddEquity(ticker, Resolution.Daily, Market.SSE if ticker[0] == '6' else Market.SZSE).Symbol for ticker in tickers]
        
        # Set A-share specific models
        for symbol in self.symbols:
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
        # Schedule monthly rebalance
        self.Schedule.On(self.DateRules.MonthStart(self.symbols[0]), self.TimeRules.AfterMarketOpen(self.symbols[0], 30), self.Rebalance)
        
        # Risk Management Parameters
        self.trailing_stop_pct = 0.10 # 10% trailing stop
        self.max_drawdown_pct = 0.20 # 20% max drawdown
        self.highest_price = {}
        self.peak_equity = self.Portfolio.TotalPortfolioValue
        
        # Momentum Parameters
        self.lookback_days = 252 # 12 months
        self.skip_days = 21 # 1 month
        self.warmup = self.lookback_days + 10
        self.SetWarmUp(self.warmup)

    def OnData(self, slice):
        if self.IsWarmingUp: return
        
        # Update Peak Equity
        current_equity = self.Portfolio.TotalPortfolioValue
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            
        # Max Drawdown Check
        if current_equity < self.peak_equity * (1 - self.max_drawdown_pct):
            self.Liquidate()
            self.Debug("Max Drawdown reached. Liquidating all positions.")
            return
            
        # Trailing Stop Logic
        for symbol in self.Portfolio.Keys:
            if self.Portfolio[symbol].Invested and slice.ContainsKey(symbol):
                current_price = slice[symbol].Price
                if symbol not in self.highest_price:
                    self.highest_price[symbol] = current_price
                elif current_price > self.highest_price[symbol]:
                    self.highest_price[symbol] = current_price
                    
                if current_price < self.highest_price[symbol] * (1 - self.trailing_stop_pct):
                    self.Liquidate(symbol)
                    self.Debug(f"Trailing Stop triggered for {symbol}")

    def Rebalance(self):
        # Calculate Momentum 12-1
        momentum_scores = {}
        history = self.History(self.symbols, self.lookback_days, Resolution.Daily)
        
        if history.empty or 'close' not in history: return
        
        for symbol in self.symbols:
            if str(symbol) not in history.index.get_level_values(0).unique(): continue
            df = history.loc[str(symbol)]
            if len(df) < self.lookback_days: continue
            
            # Price at t-1 (approx 21 days ago) and t-12 (approx 252 days ago)
            price_t_minus_1 = df['close'].iloc[-self.skip_days]
            price_t_minus_12 = df['close'].iloc[-self.lookback_days]
            
            if price_t_minus_12 != 0:
                momentum = (price_t_minus_1 / price_t_minus_12) - 1
                momentum_scores[symbol] = momentum
            
        # Sort by momentum
        sorted_symbols = sorted(momentum_scores.keys(), key=lambda x: momentum_scores[x], reverse=True)
        
        # Select Top Quintile (Top 20%)
        quintile_size = max(1, int(len(sorted_symbols) * 0.2))
        long_symbols = sorted_symbols[:quintile_size]
        
        # Execute Trades
        self.Liquidate()
        
        if not long_symbols: return
        
        # Equal Weight Allocation
        total_value = self.Portfolio.TotalPortfolioValue
        weight_per_stock = 0.95 / len(long_symbols) # Use 95% of capital
        
        for symbol in long_symbols:
            if not self.Securities[symbol].Price: continue
            price = self.Securities[symbol].Price
            target_value = total_value * weight_per_stock
            quantity = int(target_value / price / 100) * 100 # Round to 100 shares
            
            if quantity > 0:
                self.MarketOrder(symbol, quantity)
                self.highest_price[symbol] = price # Reset trailing stop