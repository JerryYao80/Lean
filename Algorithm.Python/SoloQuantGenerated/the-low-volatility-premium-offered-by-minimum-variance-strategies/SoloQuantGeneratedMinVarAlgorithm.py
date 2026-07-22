from AlgorithmImports import *
import numpy as np

class SoloQuantGeneratedMinVarAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. 设置账户货币和现金
        self.SetAccountCurrency("CNY")
        
        # 2. 读取参数
        start_date_str = self.GetParameter("start-date") or "20100101"
        end_date_str = self.GetParameter("end-date") or "20200101"
        initial_capital = float(self.GetParameter("initial-capital") or 100000)
        universe_str = self.GetParameter("universe") or "600519,000858,600036,000001,601318"
        
        # 3. 设置回测时间
        self.SetStartDate(DateTime.ParseExact(start_date_str, "yyyyMMdd", None))
        self.SetEndDate(DateTime.ParseExact(end_date_str, "yyyyMMdd", None))
        self.SetCash(initial_capital)
        
        # 4. 设置基准 (避免数据依赖)
        self.SetBenchmark(lambda x: 0)
        
        # 5. 添加标的并设置 A 股模型
        self.symbols = []
        tickers = [t.strip().split('.')[0] for t in universe_str.split(',') if t.strip()]
        
        for ticker in tickers:
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            equity.SetDataNormalizationMode(DataNormalizationMode.Raw)
            
            # 设置 A 股特定模型
            equity.FeeModel = AShareStockFeeModel()
            equity.FillModel = AShareStockFillModel()
            equity.BuyingPowerModel = AShareStockBuyingPowerModel()
            equity.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
            self.symbols.append(equity.Symbol)
            
        # 6. 策略参数
        self.rebalance_freq = timedelta(days=30) # 月度再平衡
        self.last_rebalance = self.Time - self.rebalance_freq
        self.lookback = 60 # 协方差矩阵计算窗口
        self.max_drawdown = 0.15 # 最大回撤风控
        self.trailing_stop_pct = 0.05 # 移动止损风控
        
        # 7. 状态变量
        self.highest_portfolio_value = self.Portfolio.TotalPortfolioValue
        self.entry_prices = {} # 记录入场价格用于移动止损

    def OnData(self, slice):
        # --- 风控模块 ---
        
        # 1. 最大回撤检查
        current_val = self.Portfolio.TotalPortfolioValue
        if current_val > self.highest_portfolio_value:
            self.highest_portfolio_value = current_val
            
        if current_val < self.highest_portfolio_value * (1 - self.max_drawdown):
            self.Liquidate()
            self.highest_portfolio_value = current_val # 重置以避免立即重复清仓
            return

        # 2. 移动止损检查
        for symbol in self.symbols:
            if self.Portfolio[symbol].Invested:
                # 如果是新仓位，记录入场价
                if symbol not in self.entry_prices:
                    self.entry_prices[symbol] = self.Portfolio[symbol].AveragePrice
                
                current_price = self.Securities[symbol].Price
                # 简单的移动止损：价格跌破入场价 5%
                if current_price < self.entry_prices[symbol] * (1 - self.trailing_stop_pct):
                    self.Liquidate(symbol)
                    if symbol in self.entry_prices: 
                        del self.entry_prices[symbol]

        # --- 交易逻辑模块 ---
        
        # 检查是否到达调仓日
        if self.Time - self.last_rebalance >= self.rebalance_freq:
            self.Rebalance()
            self.last_rebalance = self.Time

    def Rebalance(self):
        # 获取历史数据
        history = self.History(self.symbols, self.lookback, Resolution.Daily)
        if history.empty or len(history) < self.lookback:
            return

        # 准备数据：将 Close 价格转换为 DataFrame
        df = history['close'].unstack(level=0)
        
        # 计算日收益率
        rets = df.pct_change().dropna()
        
        # 计算协方差矩阵
        cov = rets.cov()
        
        # 最小方差优化：w = (Sigma^-1 * 1) / (1^T * Sigma^-1 * 1)
        try:
            inv_cov = np.linalg.inv(cov.values)
        except np.linalg.LinAlgError:
            inv_cov = np.linalg.pinv(cov.values) # 使用伪逆处理奇异矩阵
            
        ones = np.ones((len(self.symbols), 1))
        
        # 计算权重
        weights = np.dot(inv_cov, ones)
        weights = weights / np.sum(weights)
        
        # 执行交易
        total_value = self.Portfolio.TotalPortfolioValue
        for i, symbol in enumerate(self.symbols):
            w = weights[i][0]
            
            # 约束：不允许做空 (w_i >= 0)
            if w < 0: 
                w = 0
            
            # 计算目标股数 (按100股取整)
            target_value = total_value * w
            price = self.Securities[symbol].Price
            if price > 0:
                target_qty = int(target_value / price / 100) * 100
                current_qty = self.Portfolio[symbol].Quantity
                
                # 下单调整仓位
                if target_qty != current_qty:
                    self.MarketOrder(symbol, target_qty - current_qty)
                    
                    # 更新入场价记录
                    if target_qty > current_qty:
                        self.entry_prices[symbol] = price