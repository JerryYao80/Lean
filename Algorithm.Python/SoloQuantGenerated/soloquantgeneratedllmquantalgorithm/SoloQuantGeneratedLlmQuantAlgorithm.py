from AlgorithmImports import *

class SoloQuantGeneratedLlmQuantAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency("CNY")
        self.SetCash(1000000)
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2024, 1, 1)
        self.SetBenchmark(lambda x: 0)

        # SSE 50 Constituents (Sample)
        self.tickers = ["600519", "601318", "600036", "601166", "600030", "601328", "601398", "601939", "601988", "601288"]
        self.symbols = {}
        self.indicators = {}
        self.warmup_period = 60
        self.max_portfolio_value = 0
        self.highest_price = {}
        self.is_warmed_up = False

        for ticker in self.tickers:
            equity = self.AddEquity(ticker, Resolution.Daily, Market.SSE)
            symbol = equity.Symbol
            self.symbols[ticker] = symbol
            
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))

            self.indicators[symbol] = {
                "rsi": self.RSI(symbol, 14, Resolution.Daily),
                "sma14": self.SMA(symbol, 14, Resolution.Daily),
                "sma20": self.SMA(symbol, 20, Resolution.Daily),
                "std10": self.STD(symbol, 10, Resolution.Daily),
                "std50": self.STD(symbol, 50, Resolution.Daily),
                "bb": self.BB(symbol, 20, 2, MovingAverageType.Simple, Resolution.Daily),
                "max_high": self.MAX(symbol, 20, Resolution.Daily, Field.High)
            }
            self.highest_price[symbol] = 0

        # Weights from Table 3 of the paper (Fixed MLP output simulation)
        self.weights = {
            "price_mom_14": -0.1459,
            "rsi_mom_14": -1.0265,
            "price_vs_sma": -0.1978,
            "ma20_div": 0.0556,
            "sma20_div": -0.945,
            "dist_high20": -0.4053,
            "inv_rsi": -0.3199,
            "norm_bb_width": 3.6186,
            "vol_ratio": -0.183,
            "dollar_vol": -0.0058
        }

    def OnData(self, slice):
        # Trade once a day at 9:31 to ensure indicators are updated
        if self.Time.hour != 9 or self.Time.minute != 31: return

        # Warmup check
        if self.Time < self.StartDate + timedelta(days=self.warmup_period):
            return

        # Risk Management: Max Drawdown (20%)
        if self.Portfolio.TotalPortfolioValue > self.max_portfolio_value:
            self.max_portfolio_value = self.Portfolio.TotalPortfolioValue
        
        if self.Portfolio.TotalPortfolioValue < self.max_portfolio_value * 0.8:
            self.Liquidate()
            return

        scores = []
        for ticker, symbol in self.symbols.items():
            if not slice.ContainsKey(symbol) or slice[symbol] is None: continue
            
            # Risk Management: Trailing Stop (5%)
            if self.Portfolio[symbol].Invested:
                current_price = self.Securities[symbol].Price
                if current_price > self.highest_price[symbol]:
                    self.highest_price[symbol] = current_price
                
                if current_price < self.highest_price[symbol] * 0.95:
                    self.Liquidate(symbol)
                    continue

            # Calculate Factors
            try:
                # History for price/volume
                hist = self.History(symbol, 15, Resolution.Daily)
                if len(hist) < 15: continue
                
                close = hist['close'].iloc[-1]
                close_14 = hist['close'].iloc[-14]
                volume = hist['volume'].iloc[-1]
                
                # Indicators
                rsi = self.indicators[symbol]["rsi"].Current.Value
                sma20 = self.indicators[symbol]["sma20"].Current.Value
                std10 = self.indicators[symbol]["std10"].Current.Value
                std50 = self.indicators[symbol]["std50"].Current.Value
                bb_upper = self.indicators[symbol]["bb"].UpperBand.Current.Value
                bb_lower = self.indicators[symbol]["bb"].LowerBand.Current.Value
                max_high = self.indicators[symbol]["max_high"].Current.Value

                # Historical Indicators (for DELAY)
                rsi_hist = self.History(self.indicators[symbol]["rsi"], 14, Resolution.Daily)
                if len(rsi_hist) < 14: continue
                rsi_14 = rsi_hist['rsi'].iloc[0] 

                sma14_hist = self.History(self.indicators[symbol]["sma14"], 8, Resolution.Daily)
                if len(sma14_hist) < 8: continue
                sma14_7 = sma14_hist['sma'].iloc[0] 

                # Factors Calculation
                f1 = close - close_14 # price_mom_14
                f2 = rsi - rsi_14 # rsi_mom_14
                f3 = close - sma14_7 # price_vs_sma
                f4 = sma20 - close # ma20_div
                f5 = sma20 - close # sma20_div
                f6 = max_high - close # dist_high20
                f7 = 100 - rsi # inv_rsi
                f8 = (bb_upper - bb_lower) / sma20 if sma20 != 0 else 0 # norm_bb_width
                f9 = std10 / std50 if std50 != 0 else 0 # vol_ratio
                f10 = volume * close # dollar_vol

                # Composite Score
                score = (f1 * self.weights["price_mom_14"] +
                         f2 * self.weights["rsi_mom_14"] +
                         f3 * self.weights["price_vs_sma"] +
                         f4 * self.weights["ma20_div"] +
                         f5 * self.weights["sma20_div"] +
                         f6 * self.weights["dist_high20"] +
                         f7 * self.weights["inv_rsi"] +
                         f8 * self.weights["norm_bb_width"] +
                         f9 * self.weights["vol_ratio"] +
                         f10 * self.weights["dollar_vol"])
                
                scores.append((symbol, score))
            except Exception as e:
                continue

        # Portfolio Construction
        if not scores: return
        
        # Sort by score (Ascending because IC is negative -> Buy Low Score, Sell High Score)
        scores.sort(key=lambda x: x[1])
        
        long_count = int(len(scores) * 0.2)
        short_count = int(len(scores) * 0.2)
        
        long_symbols = [x[0] for x in scores[:long_count]]
        short_symbols = [x[0] for x in scores[-short_count:]]

        # Execution
        for symbol in self.symbols.values():
            if symbol in long_symbols:
                target_value = self.Portfolio.TotalPortfolioValue * 0.9 / long_count
                target_qty = int(target_value / self.Securities[symbol].Price / 100) * 100
                self.MarketOrder(symbol, target_qty - self.Portfolio[symbol].Quantity)
            elif symbol in short_symbols:
                target_value = self.Portfolio.TotalPortfolioValue * 0.9 / short_count
                target_qty = int(target_value / self.Securities[symbol].Price / 100) * 100
                self.MarketOrder(symbol, -target_qty - self.Portfolio[symbol].Quantity)
            else:
                self.Liquidate(symbol)
