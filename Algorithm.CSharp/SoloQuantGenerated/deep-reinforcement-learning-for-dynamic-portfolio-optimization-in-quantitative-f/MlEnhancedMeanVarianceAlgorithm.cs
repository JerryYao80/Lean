using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders;
using QuantConnect.Indicators;
using QuantConnect.Securities;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Data.Fundamental;

namespace QuantConnect.Algorithm.CSharp
{
    public class MlEnhancedMeanVarianceAlgorithm : QCAlgorithm
    {
        private static readonly string[] SseTickers = { "600036", "600519", "600276", "600030", "600031", "600887", "600038", "600585", "600690", "600104" };
        private static readonly string[] SzseTickers = { "000858", "000333", "000651", "002415", "000568", "002352", "000725", "002230", "002475", "000063" };

        private Dictionary<Symbol, RollingWindow<decimal>> _closeWindows;
        private Dictionary<Symbol, decimal> _maxPrices;
        private int _lookbackPeriod;
        private int _topN;
        private decimal _maxWeight;
        private decimal _stopLossPercent;
        private decimal _maxDrawdownPercent;
        private DateTime _lastRebalanceDate;
        private HashSet<Symbol> _pendingSell;
        private decimal _peakValue;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetStartDate(GetParameter("start-date") != null ? DateTime.ParseExact(GetParameter("start-date"), "yyyyMMdd", null) : new DateTime(2020, 1, 1));
            SetEndDate(GetParameter("end-date") != null ? DateTime.ParseExact(GetParameter("end-date"), "yyyyMMdd", null) : new DateTime(2024, 12, 31));
            SetCash(decimal.Parse(GetParameter("initial-capital") ?? "1000000"));
            SetBenchmark(_ => 0m);

            _lookbackPeriod = GetParameter("lookback-period") != null ? int.Parse(GetParameter("lookback-period")) : 60;
            _topN = GetParameter("top-n") != null ? int.Parse(GetParameter("top-n")) : 5;
            _maxWeight = GetParameter("max-weight") != null ? decimal.Parse(GetParameter("max-weight")) : 0.20m;
            _stopLossPercent = GetParameter("stop-loss") != null ? decimal.Parse(GetParameter("stop-loss")) : 0.10m;
            _maxDrawdownPercent = GetParameter("max-drawdown") != null ? decimal.Parse(GetParameter("max-drawdown")) : 0.15m;
            
            _lastRebalanceDate = DateTime.MinValue;
            _pendingSell = new HashSet<Symbol>();
            _peakValue = Portfolio.TotalPortfolioValue;

            _closeWindows = new Dictionary<Symbol, RollingWindow<decimal>>();
            _maxPrices = new Dictionary<Symbol, decimal>();

            foreach (var ticker in SseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
            }
            foreach (var ticker in SzseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SZSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
            }

            foreach (var kvp in _closeWindows)
            {
                var history = History<TradeBar>(kvp.Key, _lookbackPeriod + 1, Resolution.Daily);
                foreach (var bar in history)
                    kvp.Value.Add(bar.Close);
            }
        }

        private void ConfigureAshareSecurity(Security security)
        {
            security.FeeModel = new AShareStockFeeModel();
            security.FillModel = new AShareStockFillModel();
            security.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
            security.BuyingPowerModel = new AShareStockBuyingPowerModel();
        }

        public override void OnData(Slice data)
        {
            foreach (var kvp in _closeWindows)
            {
                if (data.Bars.ContainsKey(kvp.Key))
                    kvp.Value.Add(data.Bars[kvp.Key].Close);
            }

            _peakValue = Math.Max(_peakValue, Portfolio.TotalPortfolioValue);
            if (Portfolio.TotalPortfolioValue < _peakValue * (1 - _maxDrawdownPercent))
            {
                foreach (var kvp in Portfolio)
                {
                    if (kvp.Value.Invested)
                    {
                        Liquidate(kvp.Key);
                        _pendingSell.Add(kvp.Key);
                    }
                }
                Log($"Max drawdown triggered. Liquidating all positions. Peak: {_peakValue}, Current: {Portfolio.TotalPortfolioValue}");
                return;
            }

            var toLiquidate = new List<Symbol>();
            foreach (var kvp in Portfolio)
            {
                if (!kvp.Value.Invested) continue;
                var symbol = kvp.Key;
                var price = Securities[symbol].Price;

                if (!_maxPrices.ContainsKey(symbol) || price > _maxPrices[symbol])
                    _maxPrices[symbol] = price;

                if (price <= _maxPrices[symbol] * (1 - _stopLossPercent))
                {
                    toLiquidate.Add(symbol);
                    _maxPrices.Remove(symbol);
                }
            }

            foreach (var symbol in toLiquidate)
            {
                Liquidate(symbol);
                _pendingSell.Add(symbol);
                Log($"Trailing stop triggered for {symbol}");
            }

            if (data.Time.Month == _lastRebalanceDate.Month && data.Time.Year == _lastRebalanceDate.Year)
                return;

            _lastRebalanceDate = data.Time;

            var momentumScores = new List<Tuple<Symbol, decimal>>();
            foreach (var kvp in _closeWindows)
            {
                if (kvp.Value.IsReady && kvp.Value.Count > _lookbackPeriod)
                {
                    var prices = kvp.Value.Take(_lookbackPeriod).Select(p => (double)p).ToList();
                    var returns = new List<double>();
                    for (int i = 0; i < prices.Count - 1; i++)
                    {
                        if (prices[i + 1] != 0)
                            returns.Add((prices[i] - prices[i + 1]) / prices[i + 1]);
                    }

                    if (returns.Count > 1)
                    {
                        var avgReturn = returns.Average();
                        var stdReturn = Math.Sqrt(returns.Select(x => Math.Pow(x - avgReturn, 2)).Sum() / (returns.Count - 1));
                        var score = (decimal)(avgReturn / (stdReturn + 0.0001));
                        momentumScores.Add(Tuple.Create(kvp.Key, score));
                    }
                }
            }

            if (momentumScores.Count == 0) return;

            var selected = momentumScores
                .OrderByDescending(x => x.Item2)
                .Take(_topN)
                .Where(x => x.Item2 > 0)
                .ToList();

            if (selected.Count == 0) return;

            foreach (var kvp in Portfolio)
            {
                if (kvp.Value.Invested && !selected.Any(s => s.Item1 == kvp.Key))
                {
                    Liquidate(kvp.Key);
                    _pendingSell.Add(kvp.Key);
                }
            }

            var weight = Math.Min(1m / selected.Count, _maxWeight);

            foreach (var item in selected)
            {
                var symbol = item.Item1;
                var security = Securities[symbol];
                if (!security.HasData) continue;

                var targetValue = Portfolio.TotalPortfolioValue * weight;
                var targetQuantity = (int)(targetValue / security.Price / 100) * 100;
                var currentQuantity = Portfolio[symbol].Quantity;
                var delta = targetQuantity - currentQuantity;

                if (delta > 0 && !_pendingSell.Contains(symbol))
                    MarketOrder(symbol, delta);
            }

            _pendingSell.Clear();
        }
    }
}