// file: Algorithm.CSharp/Models/Alpha/VarAlphaModel.cs
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Risk;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// VaR-based Alpha model (Layer 2) with direct volatility-regime + trend filter.
    ///
    /// Optimization (vs broken regime-percentile approach):
    /// - Compute volatility regime DIRECTLY: compare current 20-day HV to its 252-day
    ///   empirical percentile. This avoids the broken RegimeScore trailing-window issue
    ///   (which returns 0.00 for all days when trailingWindow ~= dataLength).
    /// - Trend guard: only go long when price >= MA50 AND MA50 rising.
    /// - Insight period 21 days (monthly rebalance) to cut turnover.
    /// </summary>
    public class VarAlphaModel : IAlphaModel
    {
        private readonly VaRFactor _varFactor;
        private readonly int _lookbackDays;
        private readonly decimal _regimeHighThreshold;
        private readonly decimal _regimeLowThreshold;
        private readonly TimeSpan _insightPeriod;

        public string Name => "VarAlphaModel";

        public VarAlphaModel(
            VaRFactor varFactor,
            int lookbackDays = 252,
            decimal regimeHighThreshold = 0.80m,
            decimal regimeLowThreshold = 0.20m,
            int insightPeriodDays = 21)
        {
            _varFactor = varFactor ?? throw new ArgumentNullException(nameof(varFactor));
            _lookbackDays = lookbackDays;
            _regimeHighThreshold = regimeHighThreshold;
            _regimeLowThreshold = regimeLowThreshold;
            _insightPeriod = TimeSpan.FromDays(insightPeriodDays);
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();
            if (algorithm.IsWarmingUp) return insights;

            foreach (var kvp in data.Bars)
            {
                var symbol = kvp.Key;
                var bar = kvp.Value;
                if (bar.Close <= 0) continue;
                if (!algorithm.Securities.ContainsKey(symbol)) continue;

                var history = algorithm.History<TradeBar>(symbol, _lookbackDays + 10, Resolution.Daily);
                var result = _varFactor.Compute(symbol, algorithm.Time, history);
                if (result.Quality != FactorDataQuality.Valid) continue;

                var varValue = result.RawValue;

                // === Direct volatility regime (bypass broken RegimeScore) ===
                // Compute rolling 20-day HV series, then rank current HV vs trailing 252-day HVs
                var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).ToList();
                var hvSeries = new List<double>();
                for (int i = 20; i < bars.Count; i++)
                {
                    var window = bars.Skip(i - 20).Take(20).Select(b => (double)b.Close).ToList();
                    var rets = new List<double>();
                    for (int j = 1; j < window.Count; j++)
                        rets.Add(window[j] / window[j - 1] - 1);
                    if (rets.Count > 1)
                    {
                        var mean = rets.Average();
                        var variance = rets.Sum(r => (r - mean) * (r - mean)) / (rets.Count - 1);
                        hvSeries.Add(Math.Sqrt(variance) * Math.Sqrt(252));
                    }
                }
                decimal regimePercentile = 0m;
                if (hvSeries.Count >= 60)
                {
                    var currentHv = hvSeries.Last();
                    var trailing = hvSeries.Skip(Math.Max(0, hvSeries.Count - 252)).ToList();
                    var rank = trailing.Count(h => h <= currentHv);
                    regimePercentile = (decimal)rank / trailing.Count;
                }

                // === Trend guard: MA50 rising ===
                var closes = bars.Select(b => b.Close).TakeLast(50).ToList();
                bool uptrend = false;
                if (closes.Count >= 50)
                {
                    var ma50 = closes.Average();
                    var firstHalf = closes.Take(25).Average();
                    var secondHalf = closes.Skip(25).Take(25).Average();
                    uptrend = bar.Close >= ma50 && secondHalf >= firstHalf;
                }

                InsightDirection direction;
                double? magnitude = null;

                if (regimePercentile >= _regimeHighThreshold)
                {
                    // High-vol regime: reduce
                    direction = InsightDirection.Down;
                    magnitude = (double)((regimePercentile - _regimeHighThreshold) / (1.0m - _regimeHighThreshold));
                }
                else if (regimePercentile <= _regimeLowThreshold && uptrend)
                {
                    // Low-vol + uptrend: buy
                    direction = InsightDirection.Up;
                    magnitude = (double)((_regimeLowThreshold - regimePercentile) / _regimeLowThreshold);
                }
                else
                {
                    direction = InsightDirection.Flat;
                    magnitude = 0;
                }

                insights.Add(Insight.Price(symbol, _insightPeriod, direction,
                    magnitude: magnitude, confidence: (double)varValue, sourceModel: Name));
                algorithm.Debug($"[VarAlpha] {symbol} regime={regimePercentile:F2} VaR={varValue:F4} uptrend={uptrend} dir={direction}");
            }
            return insights;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}

