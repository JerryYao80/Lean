from AlgorithmImports import *

class SoloQuantGeneratedSellingVolatilityAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency("USD")
        
        # Parameters
        start_date_str = self.GetParameter("start-date", "20100101")
        end_date_str = self.GetParameter("end-date", "20181231")
        initial_capital = float(self.GetParameter("initial-capital", "100000"))
        
        self.SetStartDate(DateTime.ParseExact(start_date_str, "yyyyMMdd", None))
        self.SetEndDate(DateTime.ParseExact(end_date_str, "yyyyMMdd", None))
        self.SetCash(initial_capital)
        
        # Benchmark: Avoid data dependency
        self.SetBenchmark(lambda x: 0)
        
        # Add SPX Index Options
        # Using Minute resolution for accurate option chain data
        option = self.AddIndexOption("SPX", Resolution.Minute)
        self.spx_option_symbol = option.Symbol
        
        # Set Filter: ~1 month to expiry (20 to 40 days)
        # Strikes: -2 to +2 around ATM
        option.SetFilter(-2, 2, timedelta(20), timedelta(40))
        
        # Warmup
        self.SetWarmUp(TimeSpan.FromDays(5))
        
        # Risk Management Variables
        self.max_drawdown_pct = 0.20 # 20% Max Drawdown
        self.max_portfolio_value = initial_capital
        
    def OnData(self, slice):
        # Update Max Portfolio Value for Drawdown Check
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.max_portfolio_value:
            self.max_portfolio_value = current_value
            
        # Risk Control: Max Drawdown Check
        if current_value < self.max_portfolio_value * (1 - self.max_drawdown_pct):
            self.Liquidate()
            self.Debug("Max Drawdown reached. Liquidating.")
            return

        # Check for Option Chain
        chain = slice.OptionChains.get(self.spx_option_symbol)
        if not chain: return

        # Check if we already have a position
        # We only want to hold one short put at a time
        has_short_put = False
        for kvp in self.Portfolio:
            if kvp.Value.Invested and kvp.Value.Type == SecurityType.Option:
                security = self.Securities[kvp.Key]
                # Check if it's a put and we are short
                if security.Symbol.ID.OptionRight == OptionRight.Put and self.Portfolio[kvp.Key].IsShort:
                    has_short_put = True
                    # If it's expiring today, let it expire (LEAN handles settlement)
                    # We don't need to manually liquidate unless we want to roll early
                    break
        
        if not has_short_put:
            # Select ATM Put
            underlying_price = chain.Underlying.Price
            if underlying_price == 0: return

            # Filter for Puts
            puts = [x for x in chain if x.Right == OptionRight.Put]
            if not puts: return

            # Filter for expiry closest to 30 days (within our filter range)
            # We pick the expiry date closest to 30 days from now
            target_expiry = min(puts, key=lambda x: abs((x.Expiry - self.Time).days - 30)).Expiry
            expiry_puts = [x for x in puts if x.Expiry == target_expiry]
            
            if not expiry_puts: return

            # Find ATM Strike
            atm_put = min(expiry_puts, key=lambda x: abs(x.Strike - underlying_price))
            
            # Calculate Quantity (Fully Collateralized)
            # Notional = Strike * 100 * Qty
            # We want Notional <= TotalPortfolioValue
            # Qty = floor(TotalPortfolioValue / (Strike * 100))
            qty = int(self.Portfolio.TotalPortfolioValue / (atm_put.Strike * 100))
            
            if qty > 0:
                self.MarketOrder(atm_put.Symbol, -qty)
                self.Debug(f"Selling {qty} ATM Puts. Strike: {atm_put.Strike}, Expiry: {atm_put.Expiry}")