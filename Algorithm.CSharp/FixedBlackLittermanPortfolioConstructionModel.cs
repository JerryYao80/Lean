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
using System.Linq;
using Accord.Statistics;
using Accord.Math;
using QuantConnect;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Scheduling;
using NodaTime;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Black-Litterman portfolio construction model with defensive fixes for A-share market:
    /// 1. Retries History() on first rebalance if OnSecuritiesChanged got no data (A-share data starts at algorithm start date)
    /// 2. Filters symbols without data before optimization to prevent IndexOutOfRangeException
    /// 3. Validates optimizer output dimensions
    /// 4. Guards NaN/Infinity from optimizer
    /// Core BL logic (insight magnitude injection, equilibrium returns, master formula) is identical to LEAN native.
    /// </summary>
    public class FixedBlackLittermanPortfolioConstructionModel : PortfolioConstructionModel
    {
        private readonly IPortfolioOptimizer _optimizer;
        private readonly PortfolioBias _portfolioBias;
        private readonly Resolution _resolution;
        private readonly double _riskFreeRate;
        private readonly double _delta;
        private readonly int _lookback;
        private readonly double _tau;
        private readonly int _period;

        private readonly Dictionary<Symbol, ReturnsSymbolData> _symbolDataDict;
        private readonly Dictionary<Symbol, DateTimeZone> _symbolTimeZones;
        private bool _historyInitialized;

        public FixedBlackLittermanPortfolioConstructionModel(TimeSpan timeSpan,
            PortfolioBias portfolioBias = PortfolioBias.LongShort,
            int lookback = 1,
            int period = 63,
            Resolution resolution = Resolution.Daily,
            double riskFreeRate = 0.0,
            double delta = 2.5,
            double tau = 0.05,
            IPortfolioOptimizer optimizer = null)
            : this(dt => dt.Add(timeSpan), portfolioBias, lookback, period, resolution, riskFreeRate, delta, tau, optimizer)
        {
        }

        public FixedBlackLittermanPortfolioConstructionModel(Func<DateTime, DateTime?> rebalancingFunc,
            PortfolioBias portfolioBias = PortfolioBias.LongShort,
            int lookback = 1,
            int period = 63,
            Resolution resolution = Resolution.Daily,
            double riskFreeRate = 0.0,
            double delta = 2.5,
            double tau = 0.05,
            IPortfolioOptimizer optimizer = null)
            : base(rebalancingFunc)
        {
            _lookback = lookback;
            _period = period;
            _resolution = resolution;
            _riskFreeRate = riskFreeRate;
            _delta = delta;
            _tau = tau;

            var lower = portfolioBias == PortfolioBias.Long ? 0 : -1;
            var upper = portfolioBias == PortfolioBias.Short ? 0 : 1;
            _optimizer = optimizer ?? new MaximumSharpeRatioPortfolioOptimizer(lower, upper, riskFreeRate);
            _portfolioBias = portfolioBias;
            _symbolDataDict = new Dictionary<Symbol, ReturnsSymbolData>();
            _symbolTimeZones = new Dictionary<Symbol, DateTimeZone>();
        }

        protected override bool ShouldCreateTargetForInsight(Insight insight)
        {
            return FilterInvalidInsightMagnitude(Algorithm, new[] { insight }).Length != 0;
        }

        protected override Dictionary<Insight, double> DetermineTargetPercent(List<Insight> activeInsights)
        {
            var targets = new Dictionary<Insight, double>();

            if (!TryGetViews(activeInsights, out var P, out var Q))
            {
                return targets;
            }

            // If OnSecuritiesChanged got no history (A-share data starts at algorithm start date),
            // populate from history once we have enough data available.
            if (!_historyInitialized)
            {
                InitializeHistoryFromFeed(activeInsights);
                if (!_historyInitialized)
                {
                    // Not enough data yet — return zero weights
                    foreach (var insight in activeInsights)
                    {
                        targets[insight] = 0;
                    }
                    return targets;
                }
            }

            // Update ReturnsSymbolData with insight magnitudes (identical to LEAN native BL)
            foreach (var insight in activeInsights)
            {
                if (_symbolDataDict.TryGetValue(insight.Symbol, out var symbolData))
                {
                    if (insight.Magnitude == null)
                    {
                        Algorithm.SetRunTimeError(new ArgumentNullException(
                            "FixedBlackLittermanPortfolioConstructionModel does not accept 'null' as Insight.Magnitude."));
                        return targets;
                    }
                    symbolData.Add(insight.GeneratedTimeUtc, insight.Magnitude.Value.SafeDecimalCast());
                }
            }

            // Get symbols' returns
            var symbols = activeInsights.Select(x => x.Symbol).Distinct().ToList();

            // FIX: Only include symbols that have sufficient return data
            var validSymbols = symbols.Where(s =>
                _symbolDataDict.ContainsKey(s) && _symbolDataDict[s].Returns.Count >= _lookback).ToList();

            if (validSymbols.Count == 0)
            {
                foreach (var insight in activeInsights)
                {
                    targets[insight] = 0;
                }
                return targets;
            }

            // Filter insights and P matrix to valid symbols only
            var validInsights = activeInsights.Where(i => validSymbols.Contains(i.Symbol)).ToList();
            var validP = FilterMatrixColumns(P, symbols, validSymbols);

            var returns = _symbolDataDict.FormReturnsMatrix(validSymbols);

            // Guard: empty returns matrix
            if (returns.GetLength(0) == 0 || returns.GetLength(1) == 0)
            {
                foreach (var insight in activeInsights)
                {
                    targets[insight] = 0;
                }
                return targets;
            }

            // Calculate posterior estimate of the mean and uncertainty in the mean
            var Π = GetEquilibriumReturns(returns, out var Σ);
            ApplyBlackLittermanMasterFormula(ref Π, ref Σ, validP, Q);

            // Create portfolio targets from the specified insights
            var W = _optimizer.Optimize(returns, Π, Σ);

            // FIX: Validate optimizer output length matches validSymbols count
            if (W.Length != validSymbols.Count)
            {
                var equalW = 1.0 / validSymbols.Count;
                W = validSymbols.Select(_ => equalW).ToArray();
            }

            // Map weights back: valid symbols get optimizer weights, others get zero
            var weightMap = new Dictionary<Symbol, double>();
            for (var i = 0; i < validSymbols.Count; i++)
            {
                var weight = W[i];
                if (double.IsNaN(weight) || double.IsInfinity(weight))
                {
                    weight = 0;
                }
                else if (_portfolioBias != PortfolioBias.LongShort
                    && Math.Sign(weight) != (int)_portfolioBias)
                {
                    weight = 0;
                }
                weightMap[validSymbols[i]] = weight;
            }

            // Assign weights to insights (first insight per symbol wins)
            foreach (var insight in activeInsights)
            {
                targets[insight] = weightMap.TryGetValue(insight.Symbol, out var w) ? w : 0;
            }

            return targets;
        }

        protected override List<Insight> GetTargetInsights()
        {
            var activeInsights = Algorithm.Insights.GetActiveInsights(Algorithm.UtcTime)
                .Where(ShouldCreateTargetForInsight);

            return (from insight in activeInsights
                    group insight by new { insight.Symbol, insight.SourceModel } into g
                    select g.OrderBy(x => x.GeneratedTimeUtc).Last())
                    .OrderBy(x => x.Symbol).ToList();
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            base.OnSecuritiesChanged(algorithm, changes);

            foreach (var symbol in changes.RemovedSecurities.Select(x => x.Symbol))
            {
                if (_symbolDataDict.ContainsKey(symbol))
                {
                    _symbolDataDict[symbol].Reset();
                    _symbolDataDict.Remove(symbol);
                }
                _symbolTimeZones.Remove(symbol);
            }

            // initialize data for added securities (identical to LEAN native BL)
            var addedSymbols = changes.AddedSecurities.ToDictionary(x => x.Symbol, x => x.Exchange.TimeZone);
            foreach (var kvp in addedSymbols)
            {
                _symbolTimeZones[kvp.Key] = kvp.Value;
            }

            algorithm.History(addedSymbols.Keys, _lookback * _period, _resolution)
                .PushThrough(bar =>
                {
                    ReturnsSymbolData symbolData;
                    if (!_symbolDataDict.TryGetValue(bar.Symbol, out symbolData))
                    {
                        symbolData = new ReturnsSymbolData(bar.Symbol, _lookback, _period);
                        _symbolDataDict.Add(bar.Symbol, symbolData);
                    }
                    var tz = addedSymbols[bar.Symbol];
                    var utcTime = bar.EndTime.ConvertToUtc(tz);
                    symbolData.Update(utcTime, bar.Value);
                });

            // Check if we actually got data
            if (_symbolDataDict.Count > 0)
            {
                _historyInitialized = true;
            }
        }

        /// <summary>
        /// One-time initialization: populate _symbolDataDict from history for active insight symbols.
        /// Needed for A-share where data starts at algorithm start date, so OnSecuritiesChanged
        /// gets no pre-start history. Called only once; after success, _historyInitialized = true.
        /// </summary>
        private void InitializeHistoryFromFeed(List<Insight> activeInsights)
        {
            var algo = Algorithm as QCAlgorithm;
            if (algo == null) return;

            var symbols = activeInsights.Select(x => x.Symbol).Distinct().ToList();
            if (symbols.Count == 0) return;

            try
            {
                algo.History(symbols, _lookback * _period, _resolution)
                    .PushThrough(bar =>
                    {
                        ReturnsSymbolData symbolData;
                        if (!_symbolDataDict.TryGetValue(bar.Symbol, out symbolData))
                        {
                            symbolData = new ReturnsSymbolData(bar.Symbol, _lookback, _period);
                            _symbolDataDict.Add(bar.Symbol, symbolData);
                        }
                        DateTimeZone tz;
                        if (_symbolTimeZones.TryGetValue(bar.Symbol, out tz))
                        {
                            var utcTime = bar.EndTime.ConvertToUtc(tz);
                            symbolData.Update(utcTime, bar.Value);
                        }
                    });

                if (_symbolDataDict.Count > 0)
                {
                    _historyInitialized = true;
                }
            }
            catch
            {
                // Will retry on next rebalance
            }
        }

        /// <summary>
        /// Filter P matrix columns to only include valid symbols, preserving row order.
        /// </summary>
        private static double[,] FilterMatrixColumns(double[,] P, List<Symbol> allSymbols, List<Symbol> validSymbols)
        {
            if (P == null || P.GetLength(0) == 0 || P.GetLength(1) == 0) return P;
            if (allSymbols.Count == validSymbols.Count) return P;

            var validIndices = validSymbols.Select(s => allSymbols.IndexOf(s)).Where(i => i >= 0).ToList();
            if (validIndices.Count == 0) return P;

            var result = new double[P.GetLength(0), validIndices.Count];
            for (var r = 0; r < P.GetLength(0); r++)
            {
                for (var c = 0; c < validIndices.Count; c++)
                {
                    result[r, c] = P[r, validIndices[c]];
                }
            }
            return result;
        }

        private double[] GetEquilibriumReturns(double[,] returns, out double[,] Σ)
        {
            var W = Vector.Create(returns.GetLength(1), 1.0 / returns.GetLength(1));
            Σ = returns.Covariance().Multiply(252);
            var annualReturn = W.Dot(Elementwise.Add(returns.Mean(0), 1.0).Pow(252.0).Subtract(1.0));
            var annualVariance = W.Dot(Σ.Dot(W));
            var riskAversion = (annualReturn - _riskFreeRate) / annualVariance;
            return Σ.Dot(W).Multiply(riskAversion);
        }

        private void ApplyBlackLittermanMasterFormula(ref double[] Π, ref double[,] Σ, double[,] P, double[] Q)
        {
            var eye = Matrix.Diagonal(Q.GetLength(0), 1);
            var Ω = Elementwise.Multiply(P.Dot(Σ).DotWithTransposed(P).Multiply(_tau), eye);
            if (Ω.Determinant() != 0)
            {
                var Στ = Σ.Multiply(_tau);
                var A = Στ.DotWithTransposed(P).Dot(P.Dot(Στ).DotWithTransposed(P).Add(Ω).Inverse());
                Π = Π.Add(A.Dot(Q.Subtract(P.Dot(Π))));
                var M = Στ.Subtract(A.Dot(P).Dot(Στ));
                Σ = Σ.Add(M).Multiply(_delta);
            }
        }

        private bool TryGetViews(ICollection<Insight> insights, out double[,] P, out double[] Q)
        {
            try
            {
                var symbols = insights.Select(insight => insight.Symbol).ToHashSet();

                var tmpQ = insights.GroupBy(insight => insight.SourceModel)
                    .Select(values =>
                    {
                        var upInsightsSum = values.Where(i => i.Direction == InsightDirection.Up).Sum(i => Math.Abs(i.Magnitude.Value));
                        var dnInsightsSum = values.Where(i => i.Direction == InsightDirection.Down).Sum(i => Math.Abs(i.Magnitude.Value));
                        return new { View = values.Key, Q = upInsightsSum > dnInsightsSum ? upInsightsSum : dnInsightsSum };
                    })
                    .Where(x => x.Q != 0)
                    .ToDictionary(k => k.View, v => v.Q);

                var tmpP = insights.GroupBy(insight => insight.SourceModel)
                    .Select(values =>
                    {
                        var q = tmpQ[values.Key];
                        var results = values.ToDictionary(x => x.Symbol, insight =>
                        {
                            var value = (int)insight.Direction * Math.Abs(insight.Magnitude.Value);
                            return value / q;
                        });
                        foreach (var symbol in symbols)
                        {
                            if (!results.ContainsKey(symbol))
                            {
                                results.Add(symbol, 0d);
                            }
                        }
                        return new { View = values.Key, Results = results };
                    })
                    .Where(r => !r.Results.Select(v => Math.Abs(v.Value)).Sum().IsNaNOrZero())
                    .ToDictionary(k => k.View, v => v.Results);

                P = Matrix.Create(tmpP.Select(d => d.Value.Values.ToArray()).ToArray());
                Q = tmpQ.Values.ToArray();
            }
            catch
            {
                P = null;
                Q = null;
                return false;
            }
            return true;
        }
    }
}
