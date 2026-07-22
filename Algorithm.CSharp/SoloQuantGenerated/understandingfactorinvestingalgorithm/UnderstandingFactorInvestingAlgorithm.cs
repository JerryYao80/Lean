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
    public class UnderstandingFactorInvestingAlgorithm : QCAlgorithm
    {
        private static readonly string[] SseTickers = { "600000", "600016", "600019", "600028", "600029", "600030", "600036", "600048", "600050", "600104" };
        private static readonly string[] SzseTickers = { "000001", "000002", "000063", "000333", "000651", "000725", "000858", "002142", "002230", "002415" };

        private Dictionary<Symbol, FactorSymbolData> _symbolData;
        private int _momentumPeriod;
        private int _bollingerPeriod;
        private int _topK;
        private int _dropN;
        private decimal _stopLossPercent;
        private decimal _maxWeight;
        private DateTime _lastRebalanceDate;
        private HashSet<Symbol> _pendingSell;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetStartDate(GetParameter("start-date") != null ? DateTime.ParseExact(GetParameter("start-date"), "yyyyMMdd", null) : new DateTime(2019, 1, 1));
            SetEndDate(GetParameter("end-date") != null ? DateTime.ParseExact(GetParameter("end-date"), "yyyyMMdd", null) : new DateTime(2024, 6, 30));
            SetCash(decimal.Parse(GetParameter("initial-capital") ?? "1000000"));
            SetBenchmark(_ => 0m);

            _momentumPeriod = GetParameter("momentum-period") != null ? int.Parse(GetParameter("momentum-period")) : 14;
            _bollingerPeriod = GetParameter("bollinger-period") != null ? int.Parse(GetParameter("bollinger-period")) : 20;
            _topK = GetParameter("top-k") != null ? int.Parse(GetParameter("top-k")) : 13;
            _dropN = GetParameter("drop-n") != null ? int.Parse(GetParameter("drop-n")) : 5;
            _stopLossPercent = GetParameter("stop-loss") != null ? decimal.Parse(GetParameter("stop-loss")) : 0.15m;
            _maxWeight = GetParameter("max-weight") != null ? decimal.Parse(GetParameter("max-weight")) : 0.10m;
            _lastRebalanceDate = DateTime.MinValue;
            _pendingSell = new HashSet<Symbol>();

            _symbolData = new Dictionary<Symbol, FactorSymbolData>();

            foreach (var ticker in SseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                ConfigureAshareSecurity(equity);
                _symbolData[equity.Symbol] = new FactorSymbolData(_momentumPeriod, _bollingerPeriod);
            }
            foreach (var ticker in SzseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SZSE);
                ConfigureAshareSecurity(equity);
                _symbolData[equity.Symbol] = new FactorSymbolData(_momentumPeriod, _bollingerPeriod);
            }

            foreach (var kvp in _symbolData)
            {
                var history = History<TradeBar>(kvp.Key, _bollingerPeriod + 1, Resolution.Daily);
                foreach (var bar in history)
                {
                    kvp.Value.Update(bar.Close, bar.Volume);
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
            foreach (var kvp in _symbolData)
            {
                if (data.Bars.ContainsKey(kvp.Key))
                {
                    var bar = data.Bars[kvp.Key];
                    kvp.Value.Update(bar.Close, bar.Volume);
                }
            }

            var toLiquidate = new List<Symbol>();
            foreach (var kvp in Portfolio)
            {
                if (!kvp.Value.Invested) continue;
                var symbol = kvp.Key;
                var price = Securities[symbol].Price;

                if (!_symbolData[symbol].MaxPrice.HasValue || price > _symbolData[symbol].MaxPrice.Value)
                    _symbolData[symbol].MaxPrice = price;

                if (price <= _symbolData[symbol].MaxPrice.Value * (1 - _stopLossPercent))
                {
                    toLiquidate.Add(symbol);
                    _symbolData[symbol].MaxPrice = null;
                }
            }

            foreach (var symbol in toLiquidate)
            {
                Liquidate(symbol);
                _pendingSell.Add(symbol);
                Log($"Trailing stop triggered for {symbol}");
            }

            if (data.Time.Day == _lastRebalanceDate.Day && data.Time.Month == _lastRebalanceDate.Month && data.Time.Year == _lastRebalanceDate.Year)
                return;

            _lastRebalanceDate = data.Time;

            var compositeScores = new List<Tuple<Symbol, decimal>>();
            foreach (var kvp in _symbolData)
            {
                if (kvp.Value.IsReady)
                {
                    var momentum = kvp.Value.GetMomentum();
                    var bollingerWidth = kvp.Value.GetBollingerBandWidth();
                    var volumeRatio = kvp.Value.GetVolumeRatio();

                    var compositeAlpha = 0.6m * momentum + 0.3m * bollingerWidth + 0.1m * volumeRatio;
                    compositeScores.Add(Tuple.Create(kvp.Key, compositeAlpha));
                }
            }

            if (compositeScores.Count == 0) return;

            var ranked = compositeScores.OrderByDescending(x => x.Item2).ToList();
            var selected = ranked.Take(_topK).ToList();

            var currentHoldings = Portfolio.Where(p => p.Value.Invested).Select(p => p.Key).ToList();
            var currentRanks = currentHoldings.Select(h => new { Symbol = h, Rank = ranked.FindIndex(r => r.Item1 == h) }).ToList();
            var bottomCurrent = currentRanks.OrderBy(x => x.Rank).Take(_dropN).ToList();

            foreach (var bottom in bottomCurrent)
            {
                if (!selected.Any(s => s.Item1 == bottom.Symbol))
                {
                    Liquidate(bottom.Symbol);
                    _pendingSell.Add(bottom.Symbol);
                }
            }

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

    public class FactorSymbolData
    {
        private readonly RollingWindow<decimal> _closeWindow;
        private readonly RollingWindow<decimal> _volumeWindow;
        private readonly int _momentumPeriod;
        private readonly int _bollingerPeriod;
        public decimal? MaxPrice { get; set; }

        public FactorSymbolData(int momentumPeriod, int bollingerPeriod)
        {
            _momentumPeriod = momentumPeriod;
            _bollingerPeriod = bollingerPeriod;
            _closeWindow = new RollingWindow<decimal>(bollingerPeriod + 1);
            _volumeWindow = new RollingWindow<decimal>(bollingerPeriod + 1);
        }

        public bool IsReady => _closeWindow.IsReady && _closeWindow.Count > _momentumPeriod;

        public void Update(decimal close, decimal volume)
        {
            _closeWindow.Add(close);
            _volumeWindow.Add(volume);
        }

        public decimal GetMomentum()
        {
            if (_closeWindow.Count <= _momentumPeriod) return 0m;
            var currentPrice = _closeWindow[0];
            var pastPrice = _closeWindow[_momentumPeriod];
            return pastPrice != 0 ? currentPrice / pastPrice - 1m : 0m;
        }

        public decimal GetBollingerBandWidth()
        {
            if (_closeWindow.Count < _bollingerPeriod) return 0m;
            var sma = 0m;
            for (int i = 0; i < _bollingerPeriod; i++)
                sma += _closeWindow[i];
            sma /= _bollingerPeriod;

            var variance = 0m;
            for (int i = 0; i < _bollingerPeriod; i++)
                variance += (_closeWindow[i] - sma) * (_closeWindow[i] - sma);
            variance /= _bollingerPeriod;

            var std = (decimal)Math.Sqrt((double)variance);
            var bollingerUp = sma + 2 * std;
            var bollingerDown = sma - 2 * std;

            return sma != 0 ? (bollingerUp - bollingerDown) / sma : 0m;
        }

        public decimal GetVolumeRatio()
        {
            if (_volumeWindow.Count < _bollingerPeriod || _volumeWindow[0] == 0) return 0m;
            var avgVolume = 0m;
            for (int i = 1; i < _bollingerPeriod; i++)
                avgVolume += _volumeWindow[i];
            avgVolume /= (_bollingerPeriod - 1);
            return avgVolume != 0 ? _volumeWindow[0] / avgVolume : 0m;
        }
    }
}