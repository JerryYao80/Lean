from AlgorithmImports import *

class SoloQuantGeneratedImplementationRiskAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency("CNY")
        self.SetCash(1000000)
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2024, 12, 31)
        self.SetBenchmark(lambda x: 0)
        
        # Universe: Representative A-share stocks to simulate the paper's universe logic
        tickers = ["600519", "000858", "600036", "000001", "601318", "600276", "002594"]
        self.symbols = []
        self.sma_200 = {}
        self.entry_prices = {}
        self.rebalance_month = -1

        for ticker in tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            self.symbols.append(equity.Symbol)
            
            # A-Share Specific Models Configuration
            equity.FeeModel = AShareStockFeeModel()
            equity.FillModel = AShareStockFillModel()
            equity.BuyingPowerModel = AShareStockBuyingPowerModel()
            equity.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
            # Indicators
            self.sma_200[equity.Symbol] = self.SMA(equity.Symbol, 200, Resolution.Daily)

        self.SetWarmUp(timedelta(days=200))

    def OnData(self, slice):
        # Risk Management: Max Drawdown Check (20% threshold)
        if self.Portfolio.TotalPortfolioValue < self.Portfolio.StartingCash * 0.8:
            self.Liquidate()
            return

        # Monthly Rebalance Logic (SMA Momentum)
        if self.Time.month != self.rebalance_month:
            self.rebalance_month = self.Time.month
            
            for symbol in self.symbols:
                if not slice.ContainsKey(symbol) or slice[symbol] is None: continue
                
                price = slice[symbol].Price
                sma_val = self.sma_200[symbol].Current.Value
                
                if sma_val == 0: continue
                
                holding = self.Portfolio[symbol]
                
                # Signal: Close > SMA200
                if price > sma_val:
                    if not holding.Invested:
                        # Calculate target quantity (100 shares lot)
                        target_value = self.Portfolio.Cash / len(self.symbols)
                        qty = int(target_value / price / 100) * 100
                        if qty > 0:
                            self.MarketOrder(symbol, qty)
                            self.entry_prices[symbol] = price
                else:
                    if holding.Invested:
                        self.Liquidate(symbol)
                        if symbol in self.entry_prices:
                            del self.entry_prices[symbol]

        # Risk Management: Trailing Stop Check (Daily)
        for symbol in list(self.entry_prices.keys()):
            if not self.Portfolio[symbol].Invested:
                if symbol in self.entry_prices: del self.entry_prices[symbol]
                continue
            
            if slice.ContainsKey(symbol) and slice[symbol]:
                current_price = slice[symbol].Price
                # Trailing stop: if price drops 10% from entry
                if current_price < self.entry_prices[symbol] * 0.9:
                    self.Liquidate(symbol)
                    if symbol in self.entry_prices:
                        del self.entry_prices[symbol]