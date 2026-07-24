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

namespace QuantConnect.Algorithm.CSharp
{
    public class SoloQuantGeneratedTimingExcessReturnsAlgorithm : QCAlgorithm
    {
        private static readonly string[] SseTickers = { "600000", "600016", "600019", "600028", "600029", "600030", "600036", "600048", "600050", "600104" };
        private static readonly string[] SzseTickers = { "000001", "000002", "000063", "000333", "000651", "000725", "000858", "002142", "002230", "002415" };

        private Dictionary<Symbol, RollingWindow<decimal>> _closeWindows;
        private Dictionary<Symbol, decimal> _maxPrices;
        private int _momentumPeriod;
        private int _topN;
        private decimal _maxWeight;
        private decimal _stopLossPercent;
        private DateTime _lastRebalanceDate;
        private HashSet<Symbol> _pendingSell;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetStartDate(GetParameter("start-date") != null ? DateTime.ParseExact(GetParameter("start-date"), "yyyyMMdd", null) : new DateTime(2020, 1, 1));
            SetEndDate(GetParameter("end-date") != null ? DateTime.ParseExact(GetParameter("end-date"), "yyyyMMdd", null) : new DateTime(2024, 12, 31));
            SetCash(decimal.Parse(GetParameter("initial-capital") ?? "1000000"));
            SetBenchmark(_ => 0m);

            _momentumPeriod = GetParameter("momentum-period") != null ? int.Parse(GetParameter("momentum-period")) : 252;
            _topN = GetParameter("top-n") != null ? int.Parse(GetParameter("top-n")) : 10;
            _maxWeight = GetParameter("max-weight") != null ? decimal.Parse(GetParameter("max-weight")) : 0.10m;
            _stopLossPercent = GetParameter("stop-loss") != null ? decimal.Parse(GetParameter("stop-loss")) : 0.15m;
            _lastRebalanceDate = DateTime.MinValue;
            _pendingSell = new HashSet<Symbol>();

            _closeWindows = new Dictionary<Symbol, RollingWindow<decimal>>();
            _maxPrices = new Dictionary<Symbol, decimal>();

            foreach (var ticker in SseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_momentumPeriod + 1);
            }
            foreach (var ticker in SzseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SZSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_momentumPeriod + 1);
            }

            // Warm up close windows from history
            foreach (var kvp in _closeWindows)
            {
                var history = History<TradeBar>(kvp.Key, _momentumPeriod + 1, Resolution.Daily);
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
            // Update close windows
            foreach (var kvp in _closeWindows)
            {
                if (data.Bars.ContainsKey(kvp.Key))
                    kvp.Value.Add(data.Bars[kvp.Key].Close);
            }

            // Check trailing stop for held positions
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

            // Monthly rebalance
            if (data.Time.Month == _lastRebalanceDate.Month && data.Time.Year == _lastRebalanceDate.Year)
                return;

            _lastRebalanceDate = data.Time;

            // Calculate momentum scores
            var momentumScores = new List<Tuple<Symbol, decimal>>();
            foreach (var kvp in _closeWindows)
            {
                if (kvp.Value.IsReady && kvp.Value.Count > _momentumPeriod)
                {
                    var currentPrice = kvp.Value[0];
                    var pastPrice = kvp.Value[_momentumPeriod];
                    if (pastPrice > 0)
                    {
                        var momentum = currentPrice / pastPrice - 1;
                        momentumScores.Add(Tuple.Create(kvp.Key, momentum));
                    }
                }
            }

            if (momentumScores.Count == 0) return;

            // Select top N by momentum
            var selected = momentumScores
                .OrderByDescending(x => x.Item2)
                .Take(_topN)
                .Where(x => x.Item2 > 0)
                .ToList();

            if (selected.Count == 0) return;

            // Liquidate positions not in selected
            foreach (var kvp in Portfolio)
            {
                if (kvp.Value.Invested && !selected.Any(s => s.Item1 == kvp.Key))
                {
                    Liquidate(kvp.Key);
                    _pendingSell.Add(kvp.Key);
                }
            }

            // Calculate target weight
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
