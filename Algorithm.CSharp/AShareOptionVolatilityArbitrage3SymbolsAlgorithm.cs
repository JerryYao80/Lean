/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
*/

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// A股ETF期权波动率套利策略 (三标的混合版)
    /// 510050/510300/510500 混合标的，使用IV CSV信号驱动ETF现货交易
    ///
    /// 信号逻辑:
    /// - IV-RV spread z-score > 2 → 做空波动率 (卖出ETF)
    /// - IV-RV spread z-score < -2 → 做多波动率 (买入ETF)
    ///
    /// 数据源:
    /// - IV: Data/alternative/ashare-implied-volatility/sse/daily/{symbol}.csv
    /// - RV: 20日历史波动率 (从价格计算)
    ///
    /// 账户: USD (避免CNY账户崩溃), 手动设CNY ConversionRate=1
    /// </summary>
    public class AShareOptionVolatilityArbitrage3SymbolsAlgorithm : QCAlgorithm
    {
        private readonly string[] _underlyings = { "510050", "510300", "510500" };
        private readonly Dictionary<string, Symbol> _symbols = new();
        private readonly Dictionary<string, RollingWindow<decimal>> _ivHistory = new();
        private readonly Dictionary<string, RollingWindow<decimal>> _rvHistory = new();

        private const int RvLookbackDays = 20;
        private const decimal IvRvZScoreThreshold = 2.0m;
        private const decimal PositionWeight = 0.3m; // 每个标的30%仓位

        public override void Initialize()
        {
            SetStartDate(2024, 6, 26);
            SetEndDate(2024, 12, 25);
            SetCash(1000000); // USD账户 (避免CNY崩溃)

            // 手动添加CNY并设置ConversionRate=1 (允许下单)
            Portfolio.CashBook.Add("CNY", 0m, 1m);

            foreach (var ticker in _underlyings)
            {
                // 添加ETF (不添加期权链, 避免97%数据失败)
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                equity.SetFeeModel(new ConstantFeeModel(5m)); // A股佣金
                equity.SetFillModel(new ImmediateFillModel());
                equity.SetBuyingPowerModel(new SecurityMarginModel(1m)); // 全额保证金
                equity.SetSettlementModel(new ImmediateSettlementModel());

                _symbols[ticker] = equity.Symbol;
                _ivHistory[ticker] = new RollingWindow<decimal>(60);
                _rvHistory[ticker] = new RollingWindow<decimal>(RvLookbackDays);
            }

            SetWarmUp(RvLookbackDays, Resolution.Daily);

            Log($"[3Sym-OptionVolArb] 初始化完成: {string.Join(", ", _underlyings)}");
            Log($"[3Sym-OptionVolArb] 账户: USD, CNY ConversionRate=1");
            Log($"[3Sym-OptionVolArb] 信号: IV-RV z-score > {IvRvZScoreThreshold} 做空, < -{IvRvZScoreThreshold} 做多");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            foreach (var ticker in _underlyings)
            {
                var symbol = _symbols[ticker];
                if (!data.Bars.ContainsKey(symbol))
                {
                    Log("[{Time:yyyy-MM-dd}] {ticker}: 无价格数据");
                    continue;
                }

                var bar = data.Bars[symbol];
                var price = bar.Close;

                // 计算IV (从CSV读取)
                var iv = ReadIvFromCsv(ticker, Time);
                if (iv == null)
                {
                    Log("[{Time:yyyy-MM-dd}] {ticker}: 无IV数据");
                    continue;
                }

                // 计算RV (从价格历史)
                var rv = ComputeRealizedVol(symbol);
                if (rv == null)
                {
                    Log("[{Time:yyyy-MM-dd}] {ticker}: 无RV数据");
                    continue;
                }

                // 更新历史窗口
                _ivHistory[ticker].Add(iv.Value);
                _rvHistory[ticker].Add(rv.Value);

                // 计算信号
                var signal = GenerateSignal(ticker);
                if (signal == 0)
                {
                    Log("[{Time:yyyy-MM-dd}] {ticker}: IV={iv:F4}, RV={rv:F4}, 无信号");
                    continue;
                }

                // 执行交易
                ExecuteTrade(symbol, signal, price);
            }
        }

        private decimal? ReadIvFromCsv(string ticker, DateTime date)
        {
            var path = Path.Combine(Globals.DataFolder, "alternative", "ashare-implied-volatility", "sse", "daily", $"{ticker}.csv");
            if (!File.Exists(path)) return null;

            try
            {
                var lines = File.ReadAllLines(path);
                if (lines.Length < 2) return null;

                var header = lines[0].Split(',');
                var dateIdx = Array.IndexOf(header, "trade_date");
                var ivIdx = Array.IndexOf(header, "atm_iv");
                if (dateIdx < 0 || ivIdx < 0) return null;

                var targetDate = date.ToString("yyyyMMdd");
                decimal? fallbackIv = null;
                foreach (var line in lines.Skip(1))
                {
                    var parts = line.Split(',');
                    if (parts.Length <= Math.Max(dateIdx, ivIdx)) continue;

                    if (decimal.TryParse(parts[ivIdx], NumberStyles.Float, CultureInfo.InvariantCulture, out var iv))
                    {
                        if (parts[dateIdx] == targetDate)
                        {
                            return iv;
                        }
                        // 回退: 记录最近一个 <= targetDate 的值
                        if (parts[dateIdx].CompareTo(targetDate) <= 0)
                        {
                            fallbackIv = iv;
                        }
                    }
                }
                return fallbackIv;
            }
            catch (Exception ex)
            {
                Log($"[3Sym-OptionVolArb] 读取IV失败 {ticker}: {ex.Message}");
            }

            return null;
        }

        private decimal? ComputeRealizedVol(Symbol symbol)
        {
            var history = History<TradeBar>(symbol, RvLookbackDays, Resolution.Daily);
            var prices = history.Select(b => b.Close).ToList();
            if (prices.Count < 10) return null;

            var returns = new List<decimal>();
            for (int i = 1; i < prices.Count; i++)
            {
                if (prices[i - 1] > 0)
                {
                    returns.Add((prices[i] - prices[i - 1]) / prices[i - 1]);
                }
            }

            if (returns.Count < 5) return null;

            var mean = returns.Average();
            var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
            var annualizedVol = (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);

            return annualizedVol;
        }

        private int GenerateSignal(string ticker)
        {
            if (_ivHistory[ticker].Count < 20 || _rvHistory[ticker].Count < 20) return 0;

            var ivList = _ivHistory[ticker].Take(20).ToList();
            var rvList = _rvHistory[ticker].Take(20).ToList();
            var spreads = ivList.Zip(rvList, (iv, rv) => iv - rv).ToList();

            var mean = spreads.Average();
            var std = (decimal)Math.Sqrt((double)spreads.Select(s => (s - mean) * (s - mean)).Sum() / (spreads.Count - 1));
            if (std == 0) return 0;

            var currentSpread = ivList[0] - rvList[0];
            var zScore = (currentSpread - mean) / std;

            if (zScore > IvRvZScoreThreshold) return -1; // 做空波动率
            if (zScore < -IvRvZScoreThreshold) return 1;  // 做多波动率

            return 0;
        }

        private void ExecuteTrade(Symbol symbol, int signal, decimal price)
        {
            var holdings = Portfolio[symbol].Quantity;
            var targetHoldings = (int)(Portfolio.TotalPortfolioValue * PositionWeight * signal / price);

            if (holdings == targetHoldings) return;

            var quantity = targetHoldings - holdings;
            if (quantity == 0) return;

            MarketOrder(symbol, quantity);
            Log($"[{Time:yyyy-MM-dd}] {symbol.Value} 交易: {quantity:+#;-#;0} 股 @ {price:F3}");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[3Sym-OptionVolArb] 最终资产: {Portfolio.TotalPortfolioValue:N2}");
            Log($"[3Sym-OptionVolArb] 总交易次数: {Transactions.OrdersCount}");

            foreach (var ticker in _underlyings)
            {
                var symbol = _symbols[ticker];
                var holdings = Portfolio[symbol].Quantity;
                if (holdings != 0)
                {
                    Log($"[3Sym-OptionVolArb] {ticker} 持仓: {holdings} 股");
                }
            }
        }
    }
}
