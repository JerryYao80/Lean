from AlgorithmImports import *

class SoloQuantGeneratedShortInterestAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 设置账户货币为CNY，必须在SetCash之前
        self.SetAccountCurrency("CNY")
        self.SetCash(1000000)
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # 避免基准数据依赖
        self.SetBenchmark(lambda x: 0)
        
        # 定义A股标的池（示例）
        self.tickers = ["600519", "000858", "600036", "000001", "600276"]
        self.symbols = []
        
        for ticker in self.tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            symbol = equity.Symbol
            self.symbols.append(symbol)
            
            # 设置A股特定模型
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9)))
        
        # 初始化指标（作为Short Interest的代理）
        self.rsi = {}
        for symbol in self.symbols:
            self.rsi[symbol] = self.RelativeStrengthIndex(symbol, 14, Resolution.Daily)
            
        self.SetWarmUp(timedelta(days=30))
        self.last_rebalance_month = -1
        self.max_portfolio_value = self.Portfolio.TotalPortfolioValue
        
    def OnData(self, slice):
        # 风控：最大回撤检查 (10% 止损)
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.max_portfolio_value:
            self.max_portfolio_value = current_value
            
        if current_value < self.max_portfolio_value * 0.90:
            self.Liquidate()
            return
            
        # 风控：单笔持仓 Trailing Stop (5% 止损)
        for kvp in self.Portfolio:
            if kvp.Value.Invested:
                if kvp.Value.UnrealizedProfitPercent < -0.05:
                    self.Liquidate(kvp.Key)
        
        # 调仓逻辑：每月第一个交易日
        if self.Time.month == self.last_rebalance_month:
            return
            
        # 确保指标数据已就绪
        if not all(self.rsi[s].IsReady for s in self.symbols):
            return
            
        # 信号生成：按RSI排序（低值做多，高值做空，模拟Short Interest逻辑）
        sorted_by_rsi = sorted(self.symbols, key=lambda x: self.rsi[x].Current.Value)
        long_symbol = sorted_by_rsi[0]
        short_symbol = sorted_by_rsi[-1]
        
        # 组合构建：多空对冲，各占40%资金
        total_value = self.Portfolio.TotalPortfolioValue
        target_value_per_side = total_value * 0.4
        
        # 执行做多
        long_price = self.Securities[long_symbol].Price
        if long_price > 0:
            long_qty = int((target_value_per_side / long_price) / 100) * 100
            self.MarketOrder(long_symbol, long_qty)
            
        # 执行做空
        short_price = self.Securities[short_symbol].Price
        if short_price > 0:
            short_qty = int((target_value_per_side / short_price) / 100) * 100
            self.MarketOrder(short_symbol, -short_qty)
            
        # 平仓非目标持仓
        for symbol in self.symbols:
            if symbol != long_symbol and symbol != short_symbol:
                self.Liquidate(symbol)
                
        self.last_rebalance_month = self.Time.month