from AlgorithmImports import *

class SoloQuantGeneratedMomentumVolatilityAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetAccountCurrency('CNY')
        self.SetCash(100000)
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2023, 1, 1)
        
        self.symbol = self.AddEquity("600519", Resolution.Daily, Market.SSE).Symbol
        
        # A股模型设置
        security = self.Securities[self.symbol]
        security.FeeModel = AShareStockFeeModel()
        security.FillModel = AShareStockFillModel()
        security.BuyingPowerModel = AShareStockBuyingPowerModel()
        security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
        
        self.SetBenchmark(lambda x: 0)
        
        # 指标初始化
        self.mom = self.MOM(self.symbol, 252, Resolution.Daily)
        self.std = self.STD(self.symbol, 20, Resolution.Daily)
        
        # 风控状态
        self.highest_price = None
        self.entry_price = None

    def OnData(self, slice):
        if not self.symbol in slice.Bars: return
        if not self.mom.IsReady or not self.std.IsReady: return
        
        price = slice.Bars[self.symbol].Close
        momentum = self.mom.Current.Value
        volatility = self.std.Current.Value
        
        # 波动率目标控制参数
        target_vol = 0.15
        if volatility == 0: volatility = 0.001 # 避免除以零
        
        # 交易逻辑：动量大于0买入，否则清仓
        if momentum > 0:
            # 计算基于波动率目标的目标仓位
            scale = target_vol / volatility
            target_value = scale * self.Portfolio.TotalPortfolioValue
            
            # 限制最大仓位为100% (无杠杆)
            target_value = min(target_value, self.Portfolio.TotalPortfolioValue)
            
            target_qty = int(target_value / price / 100) * 100
            current_qty = self.Portfolio[self.symbol].Quantity
            
            if target_qty != current_qty:
                self.MarketOrder(self.symbol, target_qty - current_qty)
                
            # 更新风控状态
            if self.Portfolio[self.symbol].Invested:
                if self.highest_price is None or price > self.highest_price:
                    self.highest_price = price
                if self.entry_price is None:
                    self.entry_price = price
        else:
            if self.Portfolio[self.symbol].Quantity != 0:
                self.Liquidate(self.symbol)
                self.highest_price = None
                self.entry_price = None
        
        # 风控：追踪止损 (10% 回撤)
        if self.Portfolio[self.symbol].Invested and self.highest_price is not None:
            if price < self.highest_price * 0.9:
                self.Liquidate(self.symbol)
                self.highest_price = None
                self.entry_price = None