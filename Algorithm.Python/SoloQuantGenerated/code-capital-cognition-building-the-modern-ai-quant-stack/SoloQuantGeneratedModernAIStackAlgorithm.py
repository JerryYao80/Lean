from AlgorithmImports import *

class SoloQuantGeneratedModernAIStackAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. 设置账户货币（必须在 SetCash 之前）
        self.SetAccountCurrency("CNY")
        
        # 2. 设置现金和日期
        self.SetCash(100000)
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # 3. 设置基准（避免基准数据依赖）
        self.SetBenchmark(lambda x: 0)
        
        # 4. 添加标的（A股示例：贵州茅台）
        self.ticker = "600519"
        self.symbol = self.AddEquity(self.ticker, Resolution.Daily, Market.SSE).Symbol
        
        # 5. 配置 A股特定模型
        security = self.Securities[self.symbol]
        security.FeeModel = AShareStockFeeModel()
        security.FillModel = AShareStockFillModel()
        security.BuyingPowerModel = AShareStockBuyingPowerModel()
        security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
        
        # 6. 指标初始化（用于演示逻辑）
        self.sma = self.SMA(self.symbol, 20, Resolution.Daily)
        self.warm_up_indicator(self.sma)
        
        # 7. 风控参数
        self.max_drawdown_pct = 0.15 # 15% Max Drawdown
        self.trailing_stop_pct = 0.05 # 5% Trailing Stop
        self.highest_portfolio_value = self.Portfolio.TotalPortfolioValue

    def OnData(self, slice):
        # 如果数据不可用则返回
        if not self.Securities[self.symbol].HasData:
            return
            
        # --- 风控模块 ---
        current_value = self.Portfolio.TotalPortfolioValue
        
        # 更新最高净值
        if current_value > self.highest_portfolio_value:
            self.highest_portfolio_value = current_value
            
        # Max Drawdown 检查
        drawdown = (self.highest_portfolio_value - current_value) / self.highest_portfolio_value
        if drawdown > self.max_drawdown_pct:
            if self.Portfolio[self.symbol].Invested:
                self.Liquidate(self.symbol, "Max Drawdown Exceeded")
                return
                
        # Trailing Stop 检查
        if self.Portfolio[self.symbol].Invested:
            holdings = self.Portfolio[self.symbol]
            if holdings.AveragePrice > 0:
                # 简单的基于成本的 Trailing Stop
                if self.Securities[self.symbol].Price < holdings.AveragePrice * (1 - self.trailing_stop_pct):
                    self.Liquidate(self.symbol, "Trailing Stop Hit")
                    return
        
        # --- 交易逻辑模块 ---
        # 由于原文未提供具体策略，此处使用简单的均值回归作为占位符
        if self.sma.IsReady:
            price = self.Securities[self.symbol].Price
            holding = self.Portfolio[self.symbol]
            
            # 买入信号：价格低于 SMA
            if price < self.sma.Current.Value and not holding.Invested:
                # 计算数量（100股取整）
                cash = self.Portfolio.Cash
                raw_qty = (cash * 0.9) / price
                quantity = int(raw_qty / 100) * 100
                if quantity > 0:
                    self.MarketOrder(self.symbol, quantity)
                    
            # 卖出信号：价格高于 SMA
            elif price > self.sma.Current.Value and holding.Invested:
                self.Liquidate(self.symbol)