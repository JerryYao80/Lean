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
    public class MultiFactorVolumePriceSvmAlgorithm : QCAlgorithm
    {
        private static readonly string[] SseTickers = { "600000", "600036", "600104", "600050", "600887", "601318", "601398", "601939", "600276", "600519" };
        private static readonly string[] SzseTickers = { "000001", "000002", "000333", "000651", "000858", "002142", "002230", "002415", "000725", "002352" };

        private Dictionary<Symbol, RollingWindow<decimal>> _closeWindows;
        private Dictionary<Symbol, RollingWindow<decimal>> _volumeWindows;
        private Dictionary<Symbol, decimal> _maxPrices;
        private int _lookbackPeriod;
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

            _lookbackPeriod = GetParameter("lookback-period") != null ? int.Parse(GetParameter("lookback-period")) : 60;
            _topN = GetParameter("top-n") != null ? int.Parse(GetParameter("top-n")) : 5;
            _maxWeight = GetParameter("max-weight") != null ? decimal.Parse(GetParameter("max-weight")) : 0.20m;
            _stopLossPercent = GetParameter("stop-loss") != null ? decimal.Parse(GetParameter("stop-loss")) : 0.10m;
            _lastRebalanceDate = DateTime.MinValue;
            _pendingSell = new HashSet<Symbol>();

            _closeWindows = new Dictionary<Symbol, RollingWindow<decimal>>();
            _volumeWindows = new Dictionary<Symbol, RollingWindow<decimal>>();
            _maxPrices = new Dictionary<Symbol, decimal>();

            foreach (var ticker in SseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
                _volumeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
            }
            foreach (var ticker in SzseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SZSE);
                ConfigureAshareSecurity(equity);
                _closeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
                _volumeWindows[equity.Symbol] = new RollingWindow<decimal>(_lookbackPeriod + 1);
            }

            foreach (var kvp in _closeWindows)
            {
                var history = History<TradeBar>(kvp.Key, _lookbackPeriod + 1, Resolution.Daily);
                foreach (var bar in history)
                {
                    kvp.Value.Add(bar.Close);
                    _volumeWindows[kvp.Key].Add(bar.Volume);
                }
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
                {
                    kvp.Value.Add(data.Bars[kvp.Key].Close);
                    _volumeWindows[kvp.Key].Add(data.Bars[kvp.Key].Volume);
                }
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

            var stage1Scores = new List<Tuple<Symbol, decimal>>();
            foreach (var kvp in _closeWindows)
            {
                if (kvp.Value.IsReady && kvp.Value.Count > _lookbackPeriod)
                {
                    var currentPrice = kvp.Value[0];
                    var pastPrice = kvp.Value[_lookbackPeriod];
                    if (pastPrice > 0)
                    {
                        var momentum = currentPrice / pastPrice - 1;
                        stage1Scores.Add(Tuple.Create(kvp.Key, momentum));
                    }
                }
            }

            var stage2Pool = stage1Scores
                .OrderByDescending(x => x.Item2)
                .Take(stage1Scores.Count / 2)
                .ToList();

            var finalScores = new List<Tuple<Symbol, decimal>>();
            foreach (var item in stage2Pool)
            {
                var symbol = item.Item1;
                var volWindow = _volumeWindows[symbol];
                if (volWindow.IsReady && volWindow.Count >= 5)
                {
                    var recentAvgVol = volWindow.Take(5).Average();
                    var pastAvgVol = volWindow.Skip(5).Take(20).Average();
                    var volumeScore = pastAvgVol > 0 ? recentAvgVol / pastAvgVol : 0;
                    
                    var compositeScore = item.Item2 + (volumeScore - 1) * 0.5m;
                    finalScores.Add(Tuple.Create(symbol, compositeScore));
                }
            }

            if (finalScores.Count == 0) return;

            var selected = finalScores
                .OrderByDescending(x => x.Item2)
                .Take(_topN)
                .ToList();

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
                {
                    MarketOrder(symbol, delta);
                }
            }

            _pendingSell.Clear();
        }
    }
}