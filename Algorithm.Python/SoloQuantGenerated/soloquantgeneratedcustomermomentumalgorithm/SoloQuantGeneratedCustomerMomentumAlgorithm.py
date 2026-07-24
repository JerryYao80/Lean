from AlgorithmImports import *
import numpy as np

class SoloQuantGeneratedCustomerMomentumAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. Account Setup (A-Share)
        self.SetAccountCurrency("CNY")
        self.SetCash(100000)
        self.SetBenchmark(lambda x: 0) # Avoid benchmark data dependency
        
        # 2. Parameters
        self.universe_param = self.GetParameter("universe", "600000,000001,600519,000858")
        self.start_date = self.GetParameter("start-date", "2015-01-01")
        self.end_date = self.GetParameter("end-date", "2023-12-31")
        self.initial_capital = float(self.GetParameter("initial-capital", "100000"))
        
        self.SetStartDate(2015, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # 3. Universe Selection
        self.symbols = []
        for ticker in self.universe_param.split(','):
            ticker = ticker.strip()
            if not ticker: continue
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            self.symbols.append(equity.Symbol)
            
            # Set A-Share Specific Models
            security = self.Securities[ticker]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9)))

        # 4. Schedule Rebalance (Monthly)
        self.Schedule.On(self.DateRules.MonthStart(), self.TimeRules.At(9, 30), self.Rebalance)
        
        # 5. Risk Management State
        self.max_portfolio_value = self.initial_capital
        self.entry_price = {}
        self.peak_price = {}
        self.rebalance_flag = False

    def OnData(self, slice):
        # Risk Management: Max Drawdown Check
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.max_portfolio_value:
            self.max_portfolio_value = current_value
        
        drawdown = (self.max_portfolio_value - current_value) / self.max_portfolio_value
        if drawdown > 0.20: # 20% Max Drawdown Limit
            self.Liquidate()
            self.Log("Max Drawdown exceeded. Liquidating portfolio.")
            return

        # Risk Management: Trailing Stop for individual holdings
        for symbol in self.symbols:
            if self.Securities[symbol].Invested:
                current_price = self.Securities[symbol].Price
                if symbol not in self.entry_price:
                    self.entry_price[symbol] = current_price
                    self.peak_price[symbol] = current_price
                else:
                    if current_price > self.peak_price[symbol]:
                        self.peak_price[symbol] = current_price
                    
                    # 5% Trailing Stop
                    if current_price < self.peak_price[symbol] * 0.95:
                        self.Liquidate(symbol)
                        self.Log(f"Trailing stop hit for {symbol}")
                        if symbol in self.entry_price: del self.entry_price[symbol]
                        if symbol in self.peak_price: del self.peak_price[symbol]

    def Rebalance(self):
        """Monthly rebalancing logic based on Customer Momentum signal."""
        self.Log("Rebalancing portfolio...")
        
        # 1. Calculate Signal (Customer Momentum Proxy)
        # Note: In a real implementation with external data access, we would fetch
        # supply chain links and calculate sales-weighted customer returns.
        # Here, we use the supplier's own past momentum as a proxy for the signal structure.
        insights = []
        
        for symbol in self.symbols:
            history = self.History(symbol, 22, Resolution.Daily) # Approx 1 month trading days
            if history.empty or 'close' not in history:
                continue
                
            closes = history['close']
            if len(closes) < 2: continue
            
            # Calculate 1-month return
            momentum = (closes[-1] - closes[0]) / closes[0]
            price = self.Securities[symbol].Price
            
            # Filter: Price > 5 CNY
            if price > 5.0:
                insights.append({'symbol': symbol, 'signal': momentum, 'price': price})

        if not insights: return

        # 2. Rank and Select Quintiles
        insights.sort(key=lambda x: x['signal'], reverse=True)
        n = len(insights)
        quintile_size = max(1, int(n / 5))
        
        long_candidates = insights[:quintile_size]
        short_candidates = insights[-quintile_size:]
        
        # 3. Value-Weighting (Proxy using Price)
        # In a real scenario, use Market Cap. Here we use Price as a proxy for weighting.
        long_total_weight = sum(x['price'] for x in long_candidates)
        short_total_weight = sum(x['price'] for x in short_candidates)
        
        # 4. Execute Trades
        # Liquidate existing positions not in new targets
        invested_symbols = [x.Key for x in self.Portfolio if x.Value.Invested]
        target_symbols = [x['symbol'] for x in long_candidates] + [x['symbol'] for x in short_candidates]
        
        for symbol in invested_symbols:
            if symbol not in target_symbols:
                self.Liquidate(symbol)

        # Long Top Quintile
        for item in long_candidates:
            symbol = item['symbol']
            weight = item['price'] / long_total_weight if long_total_weight > 0 else 0
            target_value = self.Portfolio.TotalPortfolioValue * 0.5 * weight # 50% allocation to Long
            
            price = self.Securities[symbol].Price
            quantity = int(target_value / price / 100) * 100 # Round to 100 shares
            
            if quantity > 0:
                self.MarketOrder(symbol, quantity)

        # Short Bottom Quintile
        for item in short_candidates:
            symbol = item['symbol']
            weight = item['price'] / short_total_weight if short_total_weight > 0 else 0
            target_value = self.Portfolio.TotalPortfolioValue * 0.5 * weight # 50% allocation to Short
            
            price = self.Securities[symbol].Price
            quantity = int(target_value / price / 100) * 100 # Round to 100 shares
            
            if quantity > 0:
                self.MarketOrder(symbol, -quantity)
