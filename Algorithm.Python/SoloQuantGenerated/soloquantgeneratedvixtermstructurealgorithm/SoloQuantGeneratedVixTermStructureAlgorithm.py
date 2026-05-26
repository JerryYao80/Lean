from AlgorithmImports import *
from datetime import timedelta

class SoloQuantGeneratedVixTermStructureAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. 基础设置
        self.SetAccountCurrency("CNY")
        self.SetCash(100000)
        self.SetStartDate(2018, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # 2. 添加标的：沪深300ETF (510300) 作为波动率代理
        # 原策略使用VIX期货，A股无直接VIX期货，使用宽基ETF替代
        self.symbol = self.AddEquity("510300", Resolution.Daily, Market.SSE).Symbol
        security = self.Securities[self.symbol]
        
        # 3. 设置A股特有模型
        security.FeeModel = AShareStockFeeModel()
        security.FillModel = AShareStockFillModel()
        security.BuyingPowerModel = AShareStockBuyingPowerModel()
        security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
        
        # 4. 设置基准（避免数据依赖）
        self.SetBenchmark(lambda x: 0)
        
        # 5. 指标初始化
        # 使用短期波动率(5日)与长期波动率(20日)的比值模拟期限结构
        # 比值高(Backwardation代理) -> 短期恐慌/波动大
        # 比值低(Contango代理) -> 市场平稳
        self.std_short = self.STD(self.symbol, 5, Resolution.Daily)
        self.std_long = self.STD(self.symbol, 20, Resolution.Daily)
        self.rsi = self.RSI(self.symbol, 14, MovingAverageType.Simple, Resolution.Daily)
        
        self.SetWarmUp(timedelta(days=30))
        
        # 风控变量
        self.highest_price = 0
        self.peak_portfolio_value = 100000

    def OnData(self, slice):
        if not self.std_short.IsReady or not self.std_long.IsReady or not self.rsi.IsReady:
            return
        
        # 获取当前价格
        price = self.Securities[self.symbol].Price
        if price == 0: return
        
        # 1. 全局风控：最大回撤检查
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value > self.peak_portfolio_value:
            self.peak_portfolio_value = current_value
            
        drawdown = (current_value - self.peak_portfolio_value) / self.peak_portfolio_value
        if drawdown < -0.15: # 15% Max Drawdown
            if self.Portfolio[self.symbol].Invested:
                self.Liquidate(self.symbol)
            return

        # 2. 策略逻辑：期限结构状态识别
        short_vol = self.std_short.Current.Value
        long_vol = self.std_long.Current.Value
        vol_ratio = short_vol / long_vol if long_vol != 0 else 0
        
        current_rsi = self.rsi.Current.Value
        invested = self.Portfolio[self.symbol].Invested
        quantity = self.Portfolio[self.symbol].Quantity
        
        # 信号定义：
        # 状态 Backwardation (高波动): vol_ratio > 1.2 且 RSI < 70 (未超买) -> 做多
        # 状态 Contango (低波动): vol_ratio < 0.8 或 RSI > 80 -> 平仓或做空(简化为平仓)
        is_backwardation = vol_ratio > 1.2 and current_rsi < 70
        is_contango = vol_ratio < 0.8 or current_rsi > 80
        
        # 3. 执行交易
        if is_backwardation and quantity <= 0:
            # 计算目标仓位 (全仓)
            # A股最小单位100股
            target_value = self.Portfolio.Cash * 0.95 # 留一点现金
            target_quantity = int((target_value / price) // 100 * 100)
            
            if target_quantity > 0:
                self.MarketOrder(self.symbol, target_quantity)
                self.highest_price = price # 重置移动止损位
                
        elif is_contango and quantity > 0:
            self.Liquidate(self.symbol)
            self.highest_price = 0
            
        # 4. 个股风控：Trailing Stop (移动止损)
        if quantity > 0:
            if price > self.highest_price:
                self.highest_price = price
            
            if price < self.highest_price * 0.95: # 回撤5%止损
                self.Liquidate(self.symbol)
                self.highest_price = 0