using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Indicators;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class SoloQuantGeneratedAlphaMlpAlgorithm : QCAlgorithm
    {
        private int _k = 13;
        private int _n = 5;
        private decimal _stopLossLevel = 0.95m;
        private decimal _maxDrawdownLevel = 0.90m;
        private decimal _initialCapital = 1000000m;
        private Dictionary<Symbol, decimal> _compositeAlpha = new();
        private Dictionary<Symbol, DateTime> _purchaseDates = new();
        private decimal _peakPortfolioValue;
        private readonly HashSet<Symbol> _currentTargets = new();
        private int _sellsToday;

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1_000_000m);
            _k = GetIntParameter("k", 13);
            _n = GetIntParameter("n", 5);
            _stopLossLevel = GetDecimalParameter("stop-loss-level", 0.95m);
            _maxDrawdownLevel = GetDecimalParameter("max-drawdown-level", 0.90m);

            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetStartDate(GetDateParameter("start-date", new DateTime(2019, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2024, 6, 30)));
            SetBenchmark(x => 0m);

            var universe = GetParameter("universe") ?? "000001.SZ,600000.SH,300750.SZ";
            foreach (var token in universe.Split(','))
            {
                var tsCode = token.Trim();
                if (string.IsNullOrEmpty(tsCode)) continue;
                var parts = tsCode.Split('.');
                if (parts.Length != 2) continue;
                var ticker = parts[0];
                var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
                try
                {
                    var equity = AddEquity(ticker, Resolution.Daily, market);
                    equity.FeeModel = new AShareStockFeeModel();
                    equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                }
                catch { }
            }

            Schedule.On(DateRules.EveryDay(), TimeRules.AfterMarketOpen(Securities.Keys.First(), 10), DailyAlpha);
        }

        private void DailyAlpha()
        {
            _sellsToday = 0;
            _compositeAlpha.Clear();

            foreach (var kvp in Securities)
            {
                var symbol = kvp.Key;
                var security = kvp.Value;
                if (!security.HasData || !security.IsTradable) continue;

                try
                {
                    var history = History<TradeBar>(symbol, 50, Resolution.Daily).ToList();
                    if (history.Count < 20) continue;

                    var close = history[history.Count - 1].Close;
                    var close14 = history[history.Count - 15].Close;
                    var sma20 = history.Skip(history.Count - 20).Take(20).Average(b => b.Close);
                    var maxHigh20 = history.Skip(history.Count - 20).Take(20).Max(b => b.High);
                    var std10 = CalcStd(history.Skip(history.Count - 10).Take(10).Select(b => (double)b.Close).ToList());
                    var std50 = CalcStd(history.Select(b => (double)b.Close).ToList());

                    var f1 = close - close14;
                    var f4 = sma20 - close;
                    var f6 = maxHigh20 - close;
                    var f9 = std50 > 1e-10 ? (decimal)(std10 / std50) : 0m;

                    var alpha = f1 + f4 + f6 + f9;
                    _compositeAlpha[symbol] = alpha;
                }
                catch { }
            }

            ManageRisk();
            RebalancePortfolio();
        }

        private void RebalancePortfolio()
        {
            var ranked = _compositeAlpha.OrderByDescending(kv => kv.Value).ToList();
            var topK = ranked.Take(_k).Select(kv => kv.Key).ToHashSet();

            var invested = Portfolio.Where(kv => kv.Value.Invested).Select(kv => kv.Key).ToHashSet();

            foreach (var sym in invested)
            {
                if (topK.Contains(sym)) continue;
                if (_sellsToday >= _n) break;
                if (_purchaseDates.TryGetValue(sym, out var pd) && Time.Date <= pd.Date) continue;

                var holding = Portfolio[sym];
                var qty = (int)(Math.Floor(holding.Quantity / 100m) * 100m);
                if (qty > 0)
                {
                    Liquidate(sym);
                    _purchaseDates.Remove(sym);
                    _sellsToday++;
                }
            }

            if (topK.Count == 0) return;
            var weight = 1m / topK.Count;
            foreach (var sym in topK)
            {
                var targetValue = Portfolio.TotalPortfolioValue * weight;
                var price = Securities[sym].Price;
                if (price <= 0) continue;
                var targetQty = (int)(Math.Floor(targetValue / (price * 100m)) * 100m);
                var currentQty = Portfolio.ContainsKey(sym) && Portfolio[sym].Invested ? (int)(Math.Floor(Portfolio[sym].Quantity / 100m) * 100m) : 0;
                var delta = targetQty - currentQty;
                if (delta > 0)
                {
                    MarketOrder(sym, delta);
                    _purchaseDates[sym] = Time.Date;
                }
            }
        }

        private void ManageRisk()
        {
            var tpv = Portfolio.TotalPortfolioValue;
            if (tpv > _peakPortfolioValue) _peakPortfolioValue = tpv;

            if (_peakPortfolioValue > 0 && tpv / _peakPortfolioValue < _maxDrawdownLevel)
            {
                foreach (var holding in Portfolio.Values.Where(h => h.Invested))
                    Liquidate(holding.Symbol);
                return;
            }

            foreach (var holding in Portfolio.Values.Where(h => h.Invested))
            {
                if (holding.UnrealizedProfitPercent < _stopLossLevel - 1m)
                    Liquidate(holding.Symbol);
            }
        }

        private static double CalcStd(List<double> values)
        {
            if (values.Count < 2) return 0;
            var avg = values.Average();
            var sumSq = values.Sum(v => (v - avg) * (v - avg));
            return Math.Sqrt(sumSq / (values.Count - 1));
        }

        private decimal GetDecimalParameter(string name, decimal def)
        {
            var p = GetParameter(name);
            return decimal.TryParse(p, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : def;
        }

        private int GetIntParameter(string name, int def)
        {
            var p = GetParameter(name);
            return int.TryParse(p, out var v) ? v : def;
        }

        private DateTime GetDateParameter(string name, DateTime def)
        {
            var p = GetParameter(name);
            return DateTime.TryParse(p, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : def;
        }
    }
}
