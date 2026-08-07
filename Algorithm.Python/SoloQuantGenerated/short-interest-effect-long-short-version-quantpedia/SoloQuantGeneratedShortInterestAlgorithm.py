from AlgorithmImports import *

class SoloQuantGeneratedShortInterestAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. A股账户设置
        self.SetAccountCurrency("CNY") # 必须在SetCash之前
        self.SetCash(1000000)
        
        # 2. 基准设置 (避免基准数据依赖)
        self.SetBenchmark(lambda x: 0)
        
        # 3. 策略参数与Universe
        # 注意：实际应用中应通过参数读取或Universe Selection获取
        self.tickers = [
            "600519", "600036", "601318", "600276", "600887", # SSE Sample
            "000858", "000001", "000333", "002594", "300750"  # SZSE Sample
        ]
        
        # 4. 添加标的并设置A-Share模型
        for ticker in self.tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            security = self.AddEquity(ticker, Resolution.Daily, market)
            
            # A股特定模型设置 (必须)
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))

        # 5. 调度每月 rebalance
        self.Schedule.On(self.DateRules.MonthStart(self.tickers[0]), self.TimeRules.AfterMarketOpen(self.tickers[0], 30), self.Rebalance)
        
        # 6. 风控状态变量
        self.trailing_stop_pct = 0.05 # 5% Trailing Stop
        self.highest_price = {} 
        self.lowest_price = {} # 用于空头Trailing Stop
        self.max_drawdown_pct = 0.20 # 20% Max Drawdown
        self.portfolio_peak = self.Portfolio.TotalPortfolioValue

    def OnData(self, slice):
        # 每日风控检查
        self.CheckRiskManagement(slice)

    def CheckRiskManagement(self, slice):
        # 更新组合最高水位线
        current_portfolio_value = self.Portfolio.TotalPortfolioValue
        if current_portfolio_value > self.portfolio_peak:
            self.portfolio_peak = current_portfolio_value
        
        # 组合层面 Max Drawdown 检查
        if self.portfolio_peak > 0:
            drawdown = (self.portfolio_peak - current_portfolio_value) / self.portfolio_peak
            if drawdown > self.max_drawdown_pct:
                self.Liquidate()
                self.Log(f"Max Drawdown Exceeded: {drawdown:.2%}. Liquidating all positions.")
                return

        # 持仓层面 Trailing Stop 检查
        for kvp in self.Portfolio:
            if not kvp.Value.Invested: continue
            
            symbol = kvp.Key
            security = self.Securities[symbol]
            if not security.Price: continue
            
            current_price = security.Price
            
            if kvp.Value.IsLong:
                # 多头 Trailing Stop
                if symbol not in self.highest_price:
                    self.highest_price[symbol] = current_price
                elif current_price > self.highest_price[symbol]:
                    self.highest_price[symbol] = current_price
                
                if current_price < self.highest_price[symbol] * (1 - self.trailing_stop_pct):
                    self.Liquidate(symbol)
                    self.Log(f"Trailing Stop Long Liquidated: {symbol} at {current_price}")
                    
            elif kvp.Value.IsShort:
                # 空头 Trailing Stop (价格从低点回升时止损)
                if symbol not in self.lowest_price:
                    self.lowest_price[symbol] = current_price
                elif current_price < self.lowest_price[symbol]:
                    self.lowest_price[symbol] = current_price
                
                if current_price > self.lowest_price[symbol] * (1 + self.trailing_stop_pct):
                    self.Liquidate(symbol)
                    self.Log(f"Trailing Stop Short Liquidated: {symbol} at {current_price}")

    def Rebalance(self):
        self.Liquidate() # 清空旧仓位
        
        # 模拟信号计算 (Short Interest Ratio)
        # 注意：实际A股Short Interest数据需通过自定义数据源获取，此处使用20日波动率作为代理模拟排序逻辑
        # 逻辑假设：高波动率代理高Short Interest (做空)，低波动率代理低Short Interest (做多)
        scores = []
        for ticker in self.tickers:
            symbol = self.Securities[ticker].Symbol
            history = self.History(symbol, 20, Resolution.Daily)
            if history.empty: continue
            
            # 使用收益率标准差作为代理信号
            returns = history['close'].pct_change().dropna()
            volatility = returns.std() if len(returns) > 0 else 0
            
            scores.append((symbol, volatility))
        
        if not scores: return
        
        # 排序：Low (Long) vs High (Short)
        scores.sort(key=lambda x: x[1])
        
        n = len(scores)
        # 取前1/4做多，后1/4做空
        long_leg_count = max(1, n // 4)
        short_leg_count = max(1, n // 4)
        
        long_symbols = [x[0] for x in scores[:long_leg_count]]
        short_symbols = [x[0] for x in scores[-short_leg_count:]]
        
        # 执行交易
        self.ExecuteTrades(long_symbols, short_symbols)

    def ExecuteTrades(self, longs, shorts):
        total_value = self.Portfolio.TotalPortfolioValue
        if total_value <= 0: return
        
        # 等权重分配，预留部分现金
        total_positions = len(longs) + len(shorts)
        if total_positions == 0: return
        
        target_weight_per_stock = 0.95 / total_positions # 95%仓位
        
        for symbol in longs:
            self.PlaceOrder(symbol, target_weight_per_stock)
            
        for symbol in shorts:
            self.PlaceOrder(symbol, -target_weight_per_stock)
            
    def PlaceOrder(self, symbol, weight):
        price = self.Securities[symbol].Price
        if price <= 0: return
        
        total_value = self.Portfolio.TotalPortfolioValue
        target_value = total_value * weight
        
        # A股最小交易单位100股
        quantity = int(target_value / price / 100) * 100
        
        if quantity != 0:
            self.MarketOrder(symbol, quantity)