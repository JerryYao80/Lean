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
    public class SvmMultiFactorTimingAlgorithm : QCAlgorithm
    {
        private static readonly string[] SseTickers = { "600000", "600009", "600016", "600019", "600023", "600025", "600029", "600030", "600031", "600036", "600048", "600050", "600061", "600066", "600068" };
        private static readonly string[] SzseTickers = { "000001", "000002", "000063", "000066", "000069", "000100", "000157", "000333", "000338", "000425", "000538", "000568", "000596", "000625", "000651" };

        private Dictionary<Symbol, Momentum> _momentum;
        private Dictionary<Symbol, StandardDeviation> _stdDev;
        private Dictionary<Symbol, SimpleMovingAverage> _smaVolume;

        private int _lookback;
        private int _topN;
        private decimal _stopLossPercent;
        private DateTime _lastRebalanceDate;
        private HashSet<Symbol> _pendingSell;
        private Dictionary<Symbol, decimal> _maxPrices;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetStartDate(GetParameter("start-date") != null ? DateTime.ParseExact(GetParameter("start-date"), "yyyyMMdd", null) : new DateTime(2019, 1, 1));
            SetEndDate(GetParameter("end-date") != null ? DateTime.ParseExact(GetParameter("end-date"), "yyyyMMdd", null) : new DateTime(2023, 12, 31));
            SetCash(decimal.Parse(GetParameter("initial-capital") ?? "1000000"));
            SetBenchmark(_ => 0m);

            _lookback = GetParameter("lookback") != null ? int.Parse(GetParameter("lookback")) : 20;
            _topN = GetParameter("top-n") != null ? int.Parse(GetParameter("top-n")) : 10;
            _stopLossPercent = GetParameter("stop-loss") != null ? decimal.Parse(GetParameter("stop-loss")) : 0.10m;

            _lastRebalanceDate = DateTime.MinValue;
            _pendingSell = new HashSet<Symbol>();
            _maxPrices = new Dictionary<Symbol, decimal>();

            _momentum = new Dictionary<Symbol, Momentum>();
            _stdDev = new Dictionary<Symbol, StandardDeviation>();
            _smaVolume = new Dictionary<Symbol, SimpleMovingAverage>();

            foreach (var ticker in SseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SSE);
                ConfigureAshareSecurity(equity);
                RegisterIndicators(equity.Symbol);
            }

            foreach (var ticker in SzseTickers)
            {
                var equity = AddEquity(ticker, Resolution.Daily, Market.SZSE);
                ConfigureAshareSecurity(equity);
                RegisterIndicators(equity.Symbol);
            }

            // Warm up indicators from history
            var symbols = _momentum.Keys.ToList();
            foreach (var symbol in symbols)
            {
                var history = History<TradeBar>(symbol, _lookback * 2, Resolution.Daily);
                foreach (var bar in history)
                {
                    _momentum[symbol].Update(bar.EndTime, bar.Close);
                    _stdDev[symbol].Update(bar.EndTime, bar.Close);
                    _smaVolume[symbol].Update(bar.EndTime, bar.Volume);
                }
            }
        }

        private void RegisterIndicators(Symbol symbol)
        {
            _momentum[symbol] = MOM(symbol, _lookback, Resolution.Daily);
            _stdDev[symbol] = STD(symbol, _lookback, Resolution.Daily);
            _smaVolume[symbol] = SMA(symbol, _lookback, Resolution.Daily, selector: (TradeBar bar) => (decimal)bar.Volume);
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

            // Calculate multi-factor scores to simulate SVM classification
            var scores = new List<Tuple<Symbol, decimal>>();
            foreach (var kvp in _momentum)
            {
                var symbol = kvp.Key;
                var mom = kvp.Value;
                var std = _stdDev[symbol];
                var smaVol = _smaVolume[symbol];

                // Must be fully ready
                if (!mom.IsReady || !std.IsReady || !smaVol.IsReady) continue;
                if (!Securities[symbol].HasData) continue;

                var currentPrice = Securities[symbol].Price;
                if (currentPrice <= 0) continue;

                // Factor 1: Momentum Score (Higher is better)
                // Normalizing roughly by dividing by price to get percentage momentum
                decimal momScore = mom.Current.Value / currentPrice;

                // Factor 2: Volatility Score (Lower is better)
                // Inverse relationship
                decimal volScore = std.Current.Value == 0 ? 0 : 1m / (std.Current.Value / currentPrice);

                // Factor 3: Volume Score (Price volume confirmation)
                // Simplified as trend confirmation
                decimal volTrend = smaVol.Current.Value;
                decimal volScore = volTrend > 0 ? 1m : 0m; 

                // Linear combination simulating SVM hyperplane decision function
                // Positive weights for positive factors, we want stocks with momentum, low vol, and volume confirmation
                decimal totalScore = (momScore * 1.0m) + (volScore * 0.5m) + (volTrend > 100000 ? 0.2m : 0m);

                scores.Add(Tuple.Create(symbol, totalScore));
            }

            if (scores.Count == 0) return;

            // Select top N by score
            var selected = scores
                .OrderByDescending(x => x.Item2)
                .Take(_topN)
                .Where(x => x.Item2 > 0) // Positive prediction label simulated
                .ToList();

            // Liquidate positions not in selected
            foreach (var kvp in Portfolio)
            {
                if (kvp.Value.Invested && !selected.Any(s => s.Item1 == kvp.Key))
                {
                    Liquidate(kvp.Key);
                    _pendingSell.Add(kvp.Key);
                }
            }

            // Calculate target weight (equal weight)
            if (selected.Count == 0) return;
            var weight = 1m / selected.Count;

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