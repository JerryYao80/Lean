from AlgorithmImports import *
import datetime

class SoloQuantGeneratedPairsTradingAlgorithm(QCAlgorithm):
    def Initialize(self):
        # Parameters
        self.start_date_str = "20100101"
        self.end_date_str = "20200615"
        self.initial_capital = 100000
        self.symbol1_ticker = "601398"  # ICBC
        self.symbol2_ticker = "601939"  # CCB
        self.lookback_period = 30
        self.entry_threshold = 1.0
        self.exit_threshold = 0.5
        self.max_drawdown_pct = 0.20
        self.trailing_stop_pct = 0.05

        # Account Settings
        self.SetAccountCurrency("CNY")
        self.SetCash(self.initial_capital)
        
        # Date Parsing
        start_date = datetime.datetime.strptime(self.start_date_str, "%Y%m%d")
        end_date = datetime.datetime.strptime(self.end_date_str, "%Y%m%d")
        self.SetStartDate(start_date.year, start_date.month, start_date.day)
        self.SetEndDate(end_date.year, end_date.month, end_date.day)

        # Benchmark
        self.SetBenchmark(lambda x: 0)

        # Add Equities
        self.symbol1 = self.AddEquity(self.symbol1_ticker, Resolution.Daily, Market.SSE).Symbol
        self.symbol2 = self.AddEquity(self.symbol2_ticker, Resolution.Daily, Market.SSE).Symbol

        # Set A-Share Models
        for symbol in [self.symbol1, self.symbol2]:
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))

        # Indicators and State
        self.ratio_window = RollingWindow[float](self.lookback_period)
        self.entry_price1 = 0
        self.entry_price2 = 0
        self.max_portfolio_value = self.initial_capital
        self.invested = False

    def OnData(self, slice):
        if not (slice.ContainsKey(self.symbol1) and slice.ContainsKey(self.symbol2)): return
        if slice[self.symbol1].Price == 0 or slice[self.symbol2].Price == 0: return

        price1 = slice[self.symbol1].Price
        price2 = slice[self.symbol2].Price
        
        # Update Rolling Window
        ratio = price1 / price2
        self.ratio_window.Add(ratio)
        
        if not self.ratio_window.IsReady: return

        # Calculate Z-Score
        ratios = [self.ratio_window[i] for i in range(self.lookback_period)]
        mean = sum(ratios) / len(ratios)
        variance = sum((x - mean) ** 2 for x in ratios) / len(ratios)
        std = variance ** 0.5
        
        if std == 0: return
        z_score = (ratio - mean) / std

        # Risk Management: Max Drawdown
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.max_portfolio_value:
            self.max_portfolio_value = current_value
        
        if current_value < self.max_portfolio_value * (1 - self.max_drawdown_pct):
            self.Liquidate()
            self.invested = False
            return

        # Risk Management: Trailing Stop
        if self.invested:
            holdings1 = self.Portfolio[self.symbol1]
            
            if holdings1.IsLong:
                if price1 < self.entry_price1 * (1 - self.trailing_stop_pct):
                    self.Liquidate()
                    self.invested = False
                    return
            elif holdings1.IsShort:
                if price1 > self.entry_price1 * (1 + self.trailing_stop_pct):
                    self.Liquidate()
                    self.invested = False
                    return

        # Trading Logic
        holdings1 = self.Portfolio[self.symbol1]

        # Entry Signals
        if not self.invested:
            if z_score > self.entry_threshold:
                # Short 1, Long 2
                self.ExecutePairTrade(-1, price1, price2)
            elif z_score < -self.entry_threshold:
                # Long 1, Short 2
                self.ExecutePairTrade(1, price1, price2)
        
        # Exit Signal
        elif self.invested and abs(z_score) < self.exit_threshold:
            self.Liquidate()
            self.invested = False

    def ExecutePairTrade(self, direction, price1, price2):
        # direction: 1 (Long 1, Short 2), -1 (Short 1, Long 2)
        # Calculate quantity based on 50% capital allocation per leg, rounded to 100 shares
        capital_per_leg = self.Portfolio.Cash * 0.5
        qty1 = int(capital_per_leg / price1) // 100 * 100
        qty2 = int(capital_per_leg / price2) // 100 * 100

        if qty1 == 0 or qty2 == 0: return

        self.MarketOrder(self.symbol1, direction * qty1)
        self.MarketOrder(self.symbol2, -direction * qty2)
        
        self.entry_price1 = price1
        self.entry_price2 = price2
        self.invested = True