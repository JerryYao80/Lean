from AlgorithmImports import *
import numpy as np

class SoloQuantGeneratedOnlinePortfolioAlgorithm(QCAlgorithm):
    def Initialize(self):
        # 1. 设置账户货币（必须在SetCash之前）
        self.SetAccountCurrency('CNY')
        
        # 2. 读取参数
        param_start = self.GetParameter("start_date") or "20100101"
        param_end = self.GetParameter("end_date") or "20200101"
        param_capital = float(self.GetParameter("initial_capital") or 1000000)
        param_universe = self.GetParameter("universe") or "600519,000001,600036,000002,600000"
        
        # 3. 设置回测时间与资金
        self.SetStartDate(DateTime.ParseExact(param_start, "yyyyMMdd", None))
        self.SetEndDate(DateTime.ParseExact(param_end, "yyyyMMdd", None))
        self.SetCash(param_capital)
        
        # 4. 设置基准（避免数据依赖）
        self.SetBenchmark(lambda x: 0)
        
        # 5. 解析标的并添加
        self.symbols = []
        tickers = [t.strip().split('.')[0] for t in param_universe.split(',')]
        for ticker in tickers:
            if not ticker: continue
            market = Market.SSE if ticker[0] == '6' else Market.SZSE
            equity = self.AddEquity(ticker, Resolution.Daily, market)
            self.symbols.append(equity.Symbol)
            
            # 设置A股特定模型
            equity.FeeModel = AShareStockFeeModel()
            equity.FillModel = AShareStockFillModel()
            equity.BuyingPowerModel = AShareStockBuyingPowerModel()
            equity.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))
            
        # 6. 策略参数初始化
        self.lookback = 2 # 计算价格相对向量所需的历史数据长度
        self.epsilon = 1.0 # PAMR 阈值参数
        self.eta = 0.5 # 学习率
        self.weights = {s: 1.0/len(self.symbols) for s in self.symbols} # 初始等权重
        
        # 7. 风控状态初始化
        self.portfolio_peak = param_capital
        self.max_drawdown_limit = 0.20 # 最大回撤限制 20%
        self.trailing_stop_pct = 0.10 # 个股止损 10%
        self.entry_prices = {s: 0 for s in self.symbols}

    def OnData(self, slice):
        # 风控：最大回撤检查
        current_value = self.Portfolio.TotalPortfolioValue
        if current_value < self.portfolio_peak * (1 - self.max_drawdown_limit):
            self.Liquidate()
            return
        
        # 更新组合峰值
        if current_value > self.portfolio_peak:
            self.portfolio_peak = current_value
            
        # 获取历史数据计算价格相对向量 x_t = close_t / close_{t-1}
        history = self.History(self.symbols, self.lookback, Resolution.Daily)
        if history.empty or len(history) < self.lookback:
            return
            
        price_relatives = {}
        for symbol in self.symbols:
            hist = history.loc[str(symbol)]
            if len(hist) >= 2:
                close_t = hist['close'].iloc[-1]
                close_t_1 = hist['close'].iloc[-2]
                if close_t_1 > 0:
                    price_relatives[symbol] = close_t / close_t_1
                else:
                    price_relatives[symbol] = 1.0
            else:
                price_relatives[symbol] = 1.0
                
        # 计算当前组合收益 r_t = sum(b_i * x_i)
        portfolio_return = sum(self.weights[s] * price_relatives.get(s, 1.0) for s in self.symbols)
        
        # PAMR 算法更新权重
        # 损失函数 l = max(0, r_t - epsilon)
        loss = max(0, portfolio_return - self.epsilon)
        
        # 梯度下降更新: b_new = b_old - eta * loss * x_t
        new_weights = {}
        for symbol in self.symbols:
            x = price_relatives.get(symbol, 1.0)
            new_weights[symbol] = self.weights[symbol] - self.eta * loss * x
            
        # 投影到单纯形 (sum(b)=1, b>=0)
        # 1. 截断负值
        for s in new_weights:
            if new_weights[s] < 0: new_weights[s] = 0
            
        # 2. 归一化
        total_w = sum(new_weights.values())
        if total_w > 0:
            for s in new_weights:
                new_weights[s] /= total_w
        else:
            # 如果全为0，回退到等权重
            new_weights = {s: 1.0/len(self.symbols) for s in self.symbols}
            
        self.weights = new_weights
        
        # 执行交易
        for symbol in self.symbols:
            if not slice.ContainsKey(symbol) or slice[symbol] is None: continue
            
            current_price = self.Securities[symbol].Price
            target_value = current_value * self.weights[symbol]
            current_holdings = self.Portfolio[symbol].HoldingsValue
            delta = target_value - current_holdings
            
            # 个股风控：Trailing Stop
            if self.Portfolio[symbol].Invested:
                if self.entry_prices[symbol] == 0:
                    self.entry_prices[symbol] = self.Portfolio[symbol].AveragePrice
                # 简单止损：价格跌破入场价一定比例
                if current_price < self.entry_prices[symbol] * (1 - self.trailing_stop_pct):
                    self.Liquidate(symbol)
                    self.entry_prices[symbol] = 0
                    continue
            else:
                self.entry_prices[symbol] = 0
            
            # 最小交易单位 100股
            if abs(delta) > current_price * 100:
                qty = int(delta / current_price)
                # 向下取整到100的倍数
                qty = (qty // 100) * 100
                if qty != 0:
                    self.MarketOrder(symbol, qty)