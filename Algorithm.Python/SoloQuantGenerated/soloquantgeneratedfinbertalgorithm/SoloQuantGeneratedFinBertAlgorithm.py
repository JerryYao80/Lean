from AlgorithmImports import *

class SoloQuantGeneratedFinBertAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. 设置账户货币 (必须在 SetCash 之前)
        self.SetAccountCurrency("CNY")
        self.SetCash(100000)
        
        # 2. 设置基准 (避免数据依赖)
        self.SetBenchmark(lambda x: 0)
        
        # 3. 解析日期参数
        start_date_str = self.GetParameter("start_date") or "20100101"
        end_date_str = self.GetParameter("end_date") or "20201231"
        
        self.SetStartDate(DateTime.ParseExact(start_date_str, "yyyyMMdd", None))
        self.SetEndDate(DateTime.ParseExact(end_date_str, "yyyyMMdd", None))
        
        # 4. 添加股票 (模拟 Universe)
        # 实际策略中应从参数读取，此处为演示使用硬编码 A 股
        tickers = ["000300", "000001", "600000"]
        
        for ticker in tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            
            # 5. 设置 A股 特定模型
            equity.FeeModel = AShareStockFeeModel()
            equity.FillModel = AShareStockFillModel()
            equity.BuyingPowerModel = AShareStockBuyingPowerModel()
            equity.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
        
        # 6. 初始化风控变量
        self.high_water_mark = self.Portfolio.TotalPortfolioValue
        self.max_drawdown_pct = 0.20 # 20% Max Drawdown
        
        # 7. 模拟情感数据存储 (实际应从数据源读取)
        self.sentiment_data = {}

    def OnData(self, slice):
        # 1. 风控：最大回撤检查
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value < self.high_water_mark * (1 - self.max_drawdown_pct):
            self.Liquidate()
            self.Debug("Max Drawdown reached. Liquidating.")
            return
        
        if current_value > self.high_water_mark:
            self.high_water_mark = current_value

        # 2. 生成信号 (模拟 FinBERT 情感分析)
        # 实际实现中，这里应读取每日新闻情感得分
        active_symbols = [symbol for symbol in self.ActiveSecurities.Keys if slice.ContainsKey(symbol) and slice[symbol] is not None]
        
        insights = []
        for symbol in active_symbols:
            # 模拟获取情感得分 (0.0 - 1.0)
            # 真实场景: score = self.GetFinBertSentiment(symbol, self.Time)
            sentiment_score = self.GetSimulatedSentiment(symbol)
            
            # 信号逻辑
            if sentiment_score > 0.6:
                insights.append((symbol, 1, sentiment_score)) # 做多
            elif sentiment_score < 0.4:
                insights.append((symbol, -1, sentiment_score)) # 做空
        
        # 3. 组合构建
        if not insights:
            # 如果没有信号，平掉所有非目标仓位
            for symbol in active_symbols:
                if self.Portfolio[symbol].Invested:
                    self.Liquidate(symbol)
            return

        # 等权重分配，单只最大 5%
        count = len(insights)
        target_weight_per_stock = min(0.05, 1.0 / count)
        
        # 4. 执行交易与风控
        for symbol, direction, score in insights:
            security = self.Securities[symbol]
            holding = security.Holdings
            
            # 个股止损检查 (当日亏损 > 10%)
            if holding.Invested:
                # 简化：使用价格偏离入场价逻辑作为止损代理
                if holding.Quantity > 0 and security.Price < holding.AveragePrice * 0.9:
                    self.MarketOrder(symbol, -holding.Quantity)
                    continue
                elif holding.Quantity < 0 and security.Price > holding.AveragePrice * 1.1:
                    self.MarketOrder(symbol, -holding.Quantity)
                    continue

            # 计算目标仓位
            target_value = self.Portfolio.TotalPortfolioValue * direction * target_weight_per_stock
            # A股最小交易单位 100股
            target_quantity = int(target_value / security.Price / 100) * 100
            
            # 执行
            delta = target_quantity - holding.Quantity
            if delta != 0:
                self.MarketOrder(symbol, delta)
        
        # 平仓不在信号列表中的股票
        invested_symbols = [x.Symbol for x in self.Portfolio.Values if x.Invested]
        signal_symbols = [x[0] for x in insights]
        for symbol in invested_symbols:
            if symbol not in signal_symbols:
                self.Liquidate(symbol)

    def GetSimulatedSentiment(self, symbol):
        """
        这是一个模拟函数，用于生成可审计的草案。
        实际策略应替换为读取 FinBERT 处理后的数据。
        使用伪随机确保回测可复现。
        """
        seed = int(self.Time.strftime("%Y%m%d")) + int(str(symbol.ID).replace(" ", ""))
        return (seed % 100) / 100.0
