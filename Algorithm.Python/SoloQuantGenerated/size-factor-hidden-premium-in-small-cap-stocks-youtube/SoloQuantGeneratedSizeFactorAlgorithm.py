from AlgorithmImports import *

class SoloQuantGeneratedSizeFactorAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency("CNY")
        self.SetCash(1000000)
        self.SetBenchmark(lambda x: 0)
        
        self.UniverseSettings.Resolution = Resolution.Daily
        self.AddUniverse(self.CoarseSelectionFunction)
        
        # Rebalance monthly on the first trading day
        self.Schedule.On(self.DateRules.MonthStart(), self.TimeRules.AfterMarketOpen("000300", 30), self.Rebalance)
        
        self.rebalance_flag = False
        self.selected_symbols = []
        self.starting_cash = 1000000
        self.highest_portfolio_value = self.starting_cash
        
    def CoarseSelectionFunction(self, coarse):
        # Filter for A-shares: Price > 5 CNY, Volume > 100,000
        filtered = [x for x in coarse if x.Price > 5 and x.Volume > 100000]
        # Sort by DollarVolume to ensure liquidity, then select top 200 for ranking
        sorted_by_dollar = sorted(filtered, key=lambda x: x.DollarVolume, reverse=True)
        return [x.Symbol for x in sorted_by_dollar[:200]]
    
    def OnSecuritiesChanged(self, changes):
        for security in changes.AddedSecurities:
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
    
    def OnData(self, slice):
        # Risk Management: Max Drawdown Check (20%)
        if self.Portfolio.TotalPortfolioValue < self.starting_cash * 0.8:
            self.Liquidate()
            return
            
        # Update High Watermark
        if self.Portfolio.TotalPortfolioValue > self.highest_portfolio_value:
            self.highest_portfolio_value = self.Portfolio.TotalPortfolioValue
            
        # Risk Management: Trailing Stop (10% from Average Price)
        for kvp in self.Portfolio:
            if kvp.Value.Invested:
                if kvp.Value.Price < kvp.Value.AveragePrice * 0.9:
                    self.Liquidate(kvp.Value.Symbol)
                    
        if self.rebalance_flag:
            self.ExecuteRebalance()
            self.rebalance_flag = False
            
    def Rebalance(self):
        self.rebalance_flag = True
        
    def ExecuteRebalance(self):
        active_symbols = [s for s in self.ActiveSecurities.Keys]
        if not active_symbols: return
        
        # Fetch history to get Price and Volume for Market Cap Proxy
        # Note: Using Price * Volume as a proxy for Market Cap since FineFundamental is restricted
        history = self.History(active_symbols, 1, Resolution.Daily)
        
        market_caps = {}
        for symbol in active_symbols:
            if symbol in history.index:
                row = history.loc[symbol].iloc[-1]
                price = row['close']
                volume = row['volume']
                market_caps[symbol] = price * volume
        
        # Sort by Market Cap (Ascending) - Small Cap
        sorted_symbols = sorted(market_caps.keys(), key=lambda s: market_caps[s])
        
        # Select bottom 10% (minimum 5 stocks)
        count = max(5, int(len(sorted_symbols) * 0.1))
        self.selected_symbols = sorted_symbols[:count]
        
        # Liquidate existing positions not in new selection
        for kvp in self.Portfolio:
            if kvp.Value.Invested and kvp.Value.Symbol not in self.selected_symbols:
                self.Liquidate(kvp.Value.Symbol)
                
        # Execute Orders for new selection
        if not self.selected_symbols: return
        
        total_value = self.Portfolio.TotalPortfolioValue
        weight = 1.0 / len(self.selected_symbols)
        
        for symbol in self.selected_symbols:
            target_value = total_value * weight
            current_holdings = self.Securities[symbol].Holdings.AbsoluteHoldingsValue
            delta = target_value - current_holdings
            
            if abs(delta) > total_value * 0.01: # 1% threshold to trade
                price = self.Securities[symbol].Price
                if price > 0:
                    quantity = int(delta / price / 100) * 100 # Round to 100 shares
                    if quantity != 0:
                        self.MarketOrder(symbol, quantity)