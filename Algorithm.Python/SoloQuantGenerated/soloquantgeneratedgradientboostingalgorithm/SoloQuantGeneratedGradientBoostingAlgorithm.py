from AlgorithmImports import *

class SoloQuantGeneratedGradientBoostingAlgorithm(QCAlgorithm):
    def Initialize(self):
        # A股账户初始化：SetAccountCurrency必须在SetCash之前
        self.SetAccountCurrency("CNY")
        self.SetCash(100000)
        
        # 设置基准避免数据依赖
        self.SetBenchmark(lambda x: 0)
        
        # 设置时间参数
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2023, 12, 31)
        
        # 定义股票池 (示例：沪深300成分股)
        self.tickers = ["600519", "000858", "600036", "000001", "601318"]
        self.symbols = []
        
        # 初始化指标字典
        self.indicators = {}
        self.stop_loss_percent = 0.05 # 5% 止损
        self.max_drawdown_percent = 0.10 # 10% 最大回撤风控
        self.high_water_mark = 1.0
        
        for ticker in self.tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            symbol = equity.Symbol
            self.symbols.append(symbol)
            
            # A股模型设置
            security = self.Securities[symbol]
            security.FeeModel = AShareStockFeeModel()
            security.FillModel = AShareStockFillModel()
            security.BuyingPowerModel = AShareStockBuyingPowerModel()
            security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
            # 初始化技术指标作为特征
            self.indicators[symbol] = {
                "rsi": RelativeStrengthIndex(symbol, 14, Resolution.Daily),
                "sma_short": SimpleMovingAverage(symbol, 10, Resolution.Daily),
                "sma_long": SimpleMovingAverage(symbol, 30, Resolution.Daily),
                "momentum": Momentum(symbol, 10, Resolution.Daily),
                "volatility": StandardDeviation(symbol, 20, Resolution.Daily)
            }
            
        # 设置预热
        self.SetWarmUp(timedelta(days=40))
        
        # 每日调仓
        self.Schedule.On(self.DateRules.EveryDay(self.symbols[0]), self.TimeRules.At(9, 30), self.Rebalance)

    def OnData(self, slice):
        # 风控：最大回撤检查
        if self.Portfolio.TotalPortfolioValue > 0:
            current_drawdown = (self.high_water_mark - self.Portfolio.TotalPortfolioValue) / self.high_water_mark
            if current_drawdown > self.max_drawdown_percent:
                self.Liquidate()
                self.Log("Max Drawdown Exceeded. Liquidating all positions.")
                return
            
            if self.Portfolio.TotalPortfolioValue > self.high_water_mark:
                self.high_water_mark = self.Portfolio.TotalPortfolioValue
        
        # 风控：Trailing Stop (移动止损)
        for symbol in self.symbols:
            if not self.Portfolio[symbol].Invested: continue
            
            holding = self.Portfolio[symbol]
            # 简单的固定止损逻辑，实际可扩展为Trailing Stop
            if holding.IsLong:
                if (holding.AveragePrice - self.Securities[symbol].Price) / holding.AveragePrice > self.stop_loss_percent:
                    self.Liquidate(symbol)
                    self.Log(f"Stop Loss triggered for {symbol}")
            elif holding.IsShort:
                if (self.Securities[symbol].Price - holding.AveragePrice) / holding.AveragePrice > self.stop_loss_percent:
                    self.Liquidate(symbol)
                    self.Log(f"Stop Loss triggered for {symbol}")

    def Rebalance(self):
        for symbol in self.symbols:
            if not self.Securities[symbol].Price: continue
            
            # 1. 特征工程 (模拟)
            rsi = self.indicators[symbol]["rsi"].Current.Value
            mom = self.indicators[symbol]["momentum"].Current.Value
            sma_short = self.indicators[symbol]["sma_short"].Current.Value
            sma_long = self.indicators[symbol]["sma_long"].Current.Value
            vol = self.indicators[symbol]["volatility"].Current.Value
            
            # 2. 模拟梯度提升树模型预测
            # 在实际生产环境中，这里会加载训练好的模型并传入特征
            prediction_score = self.MockGradientBoostingPredict(rsi, mom, sma_short, sma_long, vol)
            
            # 3. 组合构建
            current_qty = self.Portfolio[symbol].Quantity
            target_qty = 0
            
            # 信号阈值
            if prediction_score > 0.6:
                # 做多信号
                target_qty = 100 # 最小交易单位
            elif prediction_score < -0.6:
                # 做空信号
                target_qty = -100
            else:
                target_qty = 0
                
            # 4. 执行交易
            if target_qty != current_qty:
                self.MarketOrder(symbol, target_qty - current_qty)

    def MockGradientBoostingPredict(self, rsi, mom, sma_short, sma_long, vol):
        """
        模拟梯度提升树模型的预测逻辑。
        这是一个基于规则的代理，用于在没有外部模型文件的情况下演示策略结构。
        """
        score = 0
        
        # 规则1：超卖且动量向上 -> 做多
        if rsi < 30 and mom > 0:
            score += 0.8
        # 规则2：短期均线上穿长期均线 -> 做多
        elif sma_short > sma_long and mom > 0:
            score += 0.6
        # 规则3：超买且动量向下 -> 做空
        elif rsi > 70 and mom < 0:
            score -= 0.8
        # 规则4：短期均线下穿长期均线 -> 做空
        elif sma_short < sma_long and mom < 0:
            score -= 0.6
            
        # 波动率调整 (低波动环境下信号更强)
        if vol < 0.02:
            score *= 1.2
            
        return score