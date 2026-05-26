from AlgorithmImports import *

class EarningsForecastData(PythonData):
    """Custom Data for Earnings Forecast Events"""
    def GetSource(self, config, date, isLiveMode):
        return SubscriptionDataSource("", SubscriptionTransportSettings.Remote)

    def Reader(self, config, line, date, isLiveMode):
        if not line.strip(): return None
        data = EarningsForecastData()
        data.Symbol = config.Symbol
        data.Time = date
        # In a real implementation, parse the line to extract forecast type
        # For this draft, we assume the data is populated correctly by the source
        try:
            parts = line.split(',')
            data.Type = parts[0] if len(parts) > 0 else ""
        except:
            data.Type = ""
        return data

class SoloQuantGeneratedEventDrivenAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. Account Settings
        self.SetAccountCurrency("CNY")
        self.SetCash(1000000)
        self.SetStartDate(2007, 1, 1)
        self.SetEndDate(2023, 12, 31)
        self.SetBenchmark(lambda x: 0) # Avoid benchmark data dependency

        # 2. Universe Selection
        self.UniverseSettings.Resolution = Resolution.Daily
        self.AddUniverse(self.CoarseSelectionFunction)

        # 3. Strategy Parameters
        self.holding_period = 30 # Trading days
        self.trailing_stop_pct = 0.05 # 5% trailing stop
        self.max_drawdown_pct = 0.15 # 15% max portfolio drawdown
        self.entry_times = {} # Track entry time for holding period
        self.highest_price = {} # Track highest price for trailing stop

        # 4. Warmup
        self.SetWarmUp(timedelta(days=30))

    def OnSecuritiesChanged(self, changes):
        """Apply A-share specific models to newly added securities"""
        for security in changes.AddedSecurities:
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9)))
            
            # Subscribe to custom earnings forecast data for this security
            self.AddData(EarningsForecastData, security.Symbol, Resolution.Daily)

    def CoarseSelectionFunction(self, coarse):
        """Select liquid A-shares, excluding ST stocks"""
        filtered = []
        for c in coarse:
            if c.Price <= 0: continue
            if c.Volume < 1000000: continue # Minimum liquidity
            if "ST" in c.Symbol.Value: continue # Exclude ST
            filtered.append(c)
        
        # Sort by Dollar Volume and select top 50 for the universe
        sorted_by_dollar_volume = sorted(filtered, key=lambda x: x.DollarVolume, reverse=True)
        return [x.Symbol for x in sorted_by_dollar_volume[:50]]

    def OnData(self, slice):
        # 1. Risk Management: Max Portfolio Drawdown
        if self.Portfolio.TotalPortfolioValue < self.StartingCash * (1 - self.max_drawdown_pct):
            self.Liquidate()
            return

        # 2. Check for new Earnings Forecast Events
        # Note: In a real backtest, this relies on the EarningsForecastData source
        for data in slice.Get(EarningsForecastData):
            if data.Type in ['略增', '扭亏', '续盈']:
                if not self.Portfolio[data.Symbol].Invested:
                    self.ExecuteEntry(data.Symbol)

        # 3. Manage Existing Holdings
        for kvp in self.Portfolio:
            symbol = kvp.Key
            security = kvp.Value
            if not security.Invested: continue

            # Update highest price for trailing stop
            if symbol not in self.highest_price:
                self.highest_price[symbol] = security.Price
            else:
                self.highest_price[symbol] = max(self.highest_price[symbol], security.Price)

            # Risk Management: Trailing Stop
            if security.Price < self.highest_price[symbol] * (1 - self.trailing_stop_pct):
                self.Liquidate(symbol)
                self.CleanupState(symbol)
                continue

            # Risk Management: Holding Period Exit
            if symbol in self.entry_times:
                if (self.Time - self.entry_times[symbol]).days >= self.holding_period:
                    self.Liquidate(symbol)
                    self.CleanupState(symbol)

    def ExecuteEntry(self, symbol):
        """Execute buy order with equal weight allocation"""
        if not self.Securities[symbol].Price: return
        
        # Calculate target quantity based on equal weight
        # Count current holdings to determine weight
        invested_count = len([s for s in self.Portfolio.Values if s.Invested])
        target_count = invested_count + 1
        
        total_value = self.Portfolio.TotalPortfolioValue
        target_value_per_stock = total_value / target_count
        
        price = self.Securities[symbol].Price
        raw_quantity = target_value_per_stock / price
        
        # Round to nearest 100 shares (A-share lot size)
        quantity = int(raw_quantity / 100) * 100
        
        if quantity > 0:
            self.MarketOrder(symbol, quantity)
            self.entry_times[symbol] = self.Time
            self.highest_price[symbol] = price

    def CleanupState(self, symbol):
        """Remove tracking data for liquidated symbols"""
        if symbol in self.entry_times:
            del self.entry_times[symbol]
        if symbol in self.highest_price:
            del self.highest_price[symbol]