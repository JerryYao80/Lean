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
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Interfaces;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Barra CNE5 V4 Alpha Model — LEAN native IAlphaModel implementation.
    /// FIX: Uses original regime weights as IR base (no cumulative drift),
    ///      normalizes composite scores before Insight emission,
    ///      skips re-z-scoring of already-z-scored factor data.
    /// </summary>
    public class AShareBarraCNE5V4AlphaModel : AlphaModel
    {
        private const string SourceModelName = "BarraCNE5V4";

        private readonly int _topN;
        private readonly decimal _minScoreSpread;
        private readonly int _minimumPresentFactors;
        private readonly int _minListedDays;
        private readonly int _maxMissingFactorCount;
        private readonly decimal _minTurnoverRate;
        private readonly decimal? _minTotalMv;
        private readonly TimeSpan _insightPeriod;

        // Effective factor weights (regime-adjusted + IR-adjusted, recomputed each rebalance)
        private decimal _betaWeight;
        private decimal _momentumWeight;
        private decimal _sizeWeight;
        private decimal _earningsYieldWeight;
        private decimal _residualVolatilityWeight;
        private decimal _growthWeight;
        private decimal _bookToPriceWeight;
        private decimal _leverageWeight;
        private decimal _liquidityWeight;
        private decimal _nonLinearSizeWeight;
        private decimal _moneyFlowWeight;
        private decimal _qualityWeight;
        private decimal _northboundWeight;
        private decimal _marginWeight;
        private decimal _chipCostWeight;

        // Regime switching
        private readonly bool _regimeSwitchingEnabled;
        private readonly int _regimeVolLookbackDays;
        private readonly decimal _regimeLowVolThreshold;
        private readonly decimal _regimeHighVolThreshold;
        private readonly decimal _regimeTransitionAlpha;
        private string _currentRegime = "mid_vol";

        // Regime weight sets (immutable originals)
        private readonly Dictionary<string, decimal> _lowVolWeights;
        private readonly Dictionary<string, decimal> _midVolWeights;
        private readonly Dictionary<string, decimal> _highVolWeights;

        // IC/IR tracking
        private readonly Dictionary<string, List<decimal>> _factorICHistory = new();
        private readonly int _icLookbackPeriods;
        private readonly int _icMinObservations;
        private readonly decimal _irSensitivity;
        private Dictionary<Symbol, decimal> _prevPeriodFactorScores = new();
        private Dictionary<Symbol, decimal> _prevPeriodStockReturns = new();

        // Industry stratification
        private bool _stratifiedSelectionEnabled;
        private readonly Dictionary<Symbol, string> _symbolIndustryMap = new();

        // Rebalance tracking
        private DateTime _lastRebalanceDate;
        private readonly string _rebalanceFrequency;

        // Internal state
        private readonly Dictionary<Symbol, AShareBarraCNE5V2FactorData> _latestFactors = new();
        private readonly List<decimal> _dailyReturns = new();
        private readonly List<DateTime> _dailyReturnDates = new();
        private decimal _previousEquity;
        private DateTime _lastProcessedDate;
        private Dictionary<Symbol, decimal> _latestScores = new();
        private string _currentRegimeForRuntimeStats = "mid_vol";

        public AShareBarraCNE5V4AlphaModel(
            int topN = 25,
            decimal minScoreSpread = 0.5m,
            string rebalanceFrequency = "monthly",
            int minimumPresentFactors = 8,
            int minListedDays = 250,
            int maxMissingFactorCount = 3,
            decimal minTurnoverRate = 0m,
            decimal? minTotalMv = null,
            // Factor weights
            decimal betaWeight = -0.05m,
            decimal momentumWeight = 0.25m,
            decimal sizeWeight = -0.05m,
            decimal earningsYieldWeight = 0.20m,
            decimal residualVolatilityWeight = -0.10m,
            decimal growthWeight = 0.15m,
            decimal bookToPriceWeight = 0.10m,
            decimal leverageWeight = -0.05m,
            decimal liquidityWeight = 0.05m,
            decimal nonLinearSizeWeight = 0.00m,
            decimal moneyFlowWeight = 0.10m,
            decimal qualityWeight = 0.20m,
            decimal northboundWeight = 0.08m,
            decimal marginWeight = 0.05m,
            decimal chipCostWeight = 0.07m,
            // Regime switching
            bool regimeSwitchingEnabled = true,
            int regimeVolLookbackDays = 20,
            decimal regimeLowVolThreshold = 0.15m,
            decimal regimeHighVolThreshold = 0.25m,
            decimal regimeTransitionAlpha = 0.30m,
            Dictionary<string, decimal> lowVolWeights = null,
            Dictionary<string, decimal> midVolWeights = null,
            Dictionary<string, decimal> highVolWeights = null,
            // IC/IR
            int icLookbackPeriods = 60,
            int icMinObservations = 12,
            decimal irSensitivity = 0.50m,
            // Stratified selection
            bool stratifiedSelectionEnabled = true,
            string industryClassificationPath = null)
        {
            _topN = topN;
            _minScoreSpread = minScoreSpread;
            _rebalanceFrequency = rebalanceFrequency ?? "monthly";
            _minimumPresentFactors = minimumPresentFactors;
            _minListedDays = minListedDays;
            _maxMissingFactorCount = maxMissingFactorCount;
            _minTurnoverRate = minTurnoverRate;
            _minTotalMv = minTotalMv;
            _insightPeriod = ResolveInsightPeriod(_rebalanceFrequency);

            _betaWeight = betaWeight;
            _momentumWeight = momentumWeight;
            _sizeWeight = sizeWeight;
            _earningsYieldWeight = earningsYieldWeight;
            _residualVolatilityWeight = residualVolatilityWeight;
            _growthWeight = growthWeight;
            _bookToPriceWeight = bookToPriceWeight;
            _leverageWeight = leverageWeight;
            _liquidityWeight = liquidityWeight;
            _nonLinearSizeWeight = nonLinearSizeWeight;
            _moneyFlowWeight = moneyFlowWeight;
            _qualityWeight = qualityWeight;
            _northboundWeight = northboundWeight;
            _marginWeight = marginWeight;
            _chipCostWeight = chipCostWeight;

            _regimeSwitchingEnabled = regimeSwitchingEnabled;
            _regimeVolLookbackDays = regimeVolLookbackDays;
            _regimeLowVolThreshold = regimeLowVolThreshold;
            _regimeHighVolThreshold = regimeHighVolThreshold;
            _regimeTransitionAlpha = regimeTransitionAlpha;

            _lowVolWeights = lowVolWeights ?? new Dictionary<string, decimal>
            {
                ["beta"] = -0.03m, ["momentum"] = 0.30m, ["size"] = -0.03m,
                ["earnyld"] = 0.20m, ["resvol"] = -0.08m, ["growth"] = 0.20m,
                ["btop"] = 0.08m, ["leverage"] = -0.03m, ["liquidity"] = 0.05m,
                ["nlsize"] = 0.00m, ["moneyflow"] = 0.10m, ["quality"] = 0.20m,
                ["northbound"] = 0.08m, ["margin"] = 0.05m, ["chipcost"] = 0.07m
            };
            _midVolWeights = midVolWeights ?? new Dictionary<string, decimal>
            {
                ["beta"] = -0.05m, ["momentum"] = 0.25m, ["size"] = -0.05m,
                ["earnyld"] = 0.20m, ["resvol"] = -0.10m, ["growth"] = 0.15m,
                ["btop"] = 0.10m, ["leverage"] = -0.05m, ["liquidity"] = 0.05m,
                ["nlsize"] = 0.00m, ["moneyflow"] = 0.10m, ["quality"] = 0.20m,
                ["northbound"] = 0.08m, ["margin"] = 0.05m, ["chipcost"] = 0.07m
            };
            _highVolWeights = highVolWeights ?? new Dictionary<string, decimal>
            {
                ["beta"] = -0.08m, ["momentum"] = 0.15m, ["size"] = -0.10m,
                ["earnyld"] = 0.25m, ["resvol"] = -0.15m, ["growth"] = 0.10m,
                ["btop"] = 0.15m, ["leverage"] = -0.08m, ["liquidity"] = 0.03m,
                ["nlsize"] = 0.00m, ["moneyflow"] = 0.12m, ["quality"] = 0.25m,
                ["northbound"] = 0.05m, ["margin"] = 0.08m, ["chipcost"] = 0.10m
            };

            _icLookbackPeriods = icLookbackPeriods;
            _icMinObservations = icMinObservations;
            _irSensitivity = irSensitivity;

            _stratifiedSelectionEnabled = stratifiedSelectionEnabled;

            if (!string.IsNullOrWhiteSpace(industryClassificationPath))
            {
                LoadIndustryClassification(industryClassificationPath);
            }

            Name = $"{nameof(AShareBarraCNE5V4AlphaModel)}({topN},{rebalanceFrequency})";
        }

        public void UpdateFactorData(Symbol underlying, AShareBarraCNE5V2FactorData factor)
        {
            if (underlying != null && factor != null)
            {
                _latestFactors[underlying] = factor;
            }
        }

        public void RecordDailyReturn(decimal dailyReturn, DateTime date)
        {
            _dailyReturns.Add(dailyReturn);
            _dailyReturnDates.Add(date);
        }

        public void RecordStockReturns(Dictionary<Symbol, decimal> stockReturns)
        {
            _prevPeriodStockReturns = new Dictionary<Symbol, decimal>(stockReturns);
        }

        public Dictionary<string, decimal> GetLatestExposure()
        {
            return ComputePortfolioExposure(_latestScores, _latestFactors);
        }

        public string GetCurrentRegime() => _currentRegimeForRuntimeStats;

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();
            var sessionDate = algorithm.Time.Date;

            if (_lastProcessedDate == sessionDate) return insights;
            _lastProcessedDate = sessionDate;

            if (!ShouldRebalance(sessionDate)) return insights;

            var eligibleFactors = _latestFactors
                .Where(pair => IsEligibleFactor(pair.Value))
                .ToDictionary(pair => pair.Key, pair => pair.Value);

            if (eligibleFactors.Count == 0) return insights;

            // Step 1: Detect regime and set base weights from regime presets (no accumulation)
            if (_regimeSwitchingEnabled)
            {
                var newRegime = DetectMarketRegime();
                if (newRegime != _currentRegime)
                {
                    algorithm.Log($"[V4 Alpha] Regime change: {_currentRegime} -> {newRegime}");
                    _currentRegime = newRegime;
                }
            }
            _currentRegimeForRuntimeStats = _currentRegime;

            // Step 2: Apply regime weights as the NEW base (always from regime presets, not from previous adjusted values)
            ApplyRegimeWeights(_currentRegime);

            // Step 3: Apply IR adjustment on top of regime base (not on previously IR-adjusted values)
            if (_factorICHistory.Count > 0)
            {
                ApplyIRWeightAdjustment();
            }

            // Step 4: Normalize weights so |sum| = 1 (prevents score explosion)
            NormalizeWeights();

            // Step 5: Compute factor scores (factor data is already z-scored, use directly)
            var scores = ComputeScores(eligibleFactors);
            _latestScores = new Dictionary<Symbol, decimal>(scores);

            // Track factor IC for next period
            if (_prevPeriodStockReturns.Count > 0 && _prevPeriodFactorScores.Count > 0)
            {
                var factorZScores = ComputeFactorZScores(eligibleFactors);
                var periodIC = ComputeFactorIC(factorZScores, _prevPeriodStockReturns);
                foreach (var icPair in periodIC)
                {
                    if (!_factorICHistory.ContainsKey(icPair.Key))
                        _factorICHistory[icPair.Key] = new List<decimal>();
                    _factorICHistory[icPair.Key].Add(icPair.Value);
                    if (_factorICHistory[icPair.Key].Count > _icLookbackPeriods)
                        _factorICHistory[icPair.Key].RemoveAt(0);
                }
            }
            _prevPeriodFactorScores = new Dictionary<Symbol, decimal>(scores);
            _prevPeriodStockReturns.Clear();

            // Step 6: Normalize scores to [-1, 1] range (prevents Insight.Weight explosion)
            var normalizedScores = NormalizeScores(scores);

            var spread = GetScoreSpread(normalizedScores);
            if (spread < _minScoreSpread)
            {
                algorithm.Log($"[V4 Alpha] Insufficient score spread: {spread:F4} < {_minScoreSpread:F4}");
                return insights;
            }

            var selected = _stratifiedSelectionEnabled && _symbolIndustryMap.Count > 0
                ? SelectTopNStratified(normalizedScores, eligibleFactors)
                : SelectTopN(normalizedScores);

            if (selected.Count == 0)
            {
                algorithm.Log($"[V4 Alpha] No stocks selected from {eligibleFactors.Count} eligible");
                return insights;
            }

            // Step 7: Emit Up-only Insights with Magnitude as expected return
            // For long-only strategy with BL portfolio construction:
            // - Skip negative scores (Down insights are dead weight for BL's Long bias)
            // - Scale Magnitude as expected annual return (BL interprets Magnitude as view return)
            // - Equal weight per insight (BL uses Magnitude, not Weight, for view construction)
            var longCandidates = selected.Where(p => p.Value > 0m).ToList();
            if (longCandidates.Count == 0)
            {
                algorithm.Log($"[V4 Alpha] No positive-score stocks from {selected.Count} selected");
                return insights;
            }

            var maxScore = longCandidates.Max(p => p.Value);
            var targetAnnualReturn = 0.15;   // Maps best stock to 15% expected return
            var equalWeight = 1.0 / longCandidates.Count;
            var avgConfidence = ComputeAverageConfidence();

            foreach (var pair in longCandidates)
            {
                var symbol = pair.Key;
                var score = pair.Value;
                var confidence = avgConfidence;

                if (eligibleFactors.TryGetValue(symbol, out var factor) && factor.PresentFactorCount >= _minimumPresentFactors)
                {
                    confidence = Math.Min(1.0, Math.Max(0.1, avgConfidence * (0.8 + 0.2 * factor.PresentFactorCount / 15.0)));
                }

                // Magnitude = expected annual return proportional to score rank
                var magnitude = maxScore > 0m ? (double)(score / maxScore) * targetAnnualReturn : targetAnnualReturn;

                insights.Add(Insight.Price(
                    symbol,
                    _insightPeriod,
                    InsightDirection.Up,
                    magnitude,
                    confidence,
                    SourceModelName,
                    weight: equalWeight));
            }

            _lastRebalanceDate = sessionDate;

            algorithm.Log(
                $"[V4 Alpha] Rebalanced: eligible={eligibleFactors.Count} selected={longCandidates.Count}/{selected.Count} " +
                $"spread={spread:F4} regime={_currentRegime} confidence={avgConfidence:F2}");

            return insights;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var security in changes.RemovedSecurities)
            {
                _latestFactors.Remove(security.Symbol);
                _latestScores.Remove(security.Symbol);
                _symbolIndustryMap.Remove(security.Symbol);
            }
        }

        #region Score Computation

        private Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            var eligible = factors
                .Where(pair => pair.Value != null && pair.Value.PresentFactorCount >= _minimumPresentFactors)
                .ToList();

            var scores = eligible.ToDictionary(pair => pair.Key, _ => 0m);
            if (eligible.Count == 0) return scores;

            // Re-z-score across CSI300 universe to correct for subset bias.
            // The raw factor data is z-scored across ALL A-shares; re-z-scoring
            // across the CSI300 subset ensures the distribution is centered for
            // our specific universe.
            ApplyFactor(eligible, scores, f => f.Beta, _betaWeight);
            ApplyFactor(eligible, scores, f => f.Momentum, _momentumWeight);
            ApplyFactor(eligible, scores, f => f.Size, _sizeWeight);
            ApplyFactor(eligible, scores, f => f.EarningsYield, _earningsYieldWeight);
            ApplyFactor(eligible, scores, f => f.ResidualVolatility, _residualVolatilityWeight);
            ApplyFactor(eligible, scores, f => f.Growth, _growthWeight);
            ApplyFactor(eligible, scores, f => f.BookToPrice, _bookToPriceWeight);
            ApplyFactor(eligible, scores, f => f.Leverage, _leverageWeight);
            ApplyFactor(eligible, scores, f => f.Liquidity, _liquidityWeight);
            ApplyFactor(eligible, scores, f => f.NonLinearSize, _nonLinearSizeWeight);
            ApplyFactor(eligible, scores, f => f.MoneyFlow, _moneyFlowWeight);
            ApplyFactor(eligible, scores, f => f.Quality, _qualityWeight);
            ApplyFactor(eligible, scores, f => f.Northbound, _northboundWeight);
            ApplyFactor(eligible, scores, f => f.Margin, _marginWeight);
            ApplyFactor(eligible, scores, f => f.ChipCost, _chipCostWeight);

            return scores;
        }

        /// <summary>
        /// Z-score factor values across the eligible universe, then apply weight.
        /// Re-z-scoring corrects for CSI300 subset bias in the already-z-scored data.
        /// </summary>
        private static void ApplyFactor(
            IReadOnlyList<KeyValuePair<Symbol, AShareBarraCNE5V2FactorData>> eligible,
            IDictionary<Symbol, decimal> scores,
            Func<AShareBarraCNE5V2FactorData, decimal?> selector,
            decimal weight)
        {
            if (weight == 0m) return;
            var indicesWithValues = new List<int>();
            var values = new List<decimal>();
            for (var index = 0; index < eligible.Count; index++)
            {
                var value = selector(eligible[index].Value);
                if (!value.HasValue) continue;
                indicesWithValues.Add(index);
                values.Add(value.Value);
            }
            if (values.Count <= 1) return;
            var zScores = SafeZScores(values);
            for (var index = 0; index < indicesWithValues.Count; index++)
            {
                var symbol = eligible[indicesWithValues[index]].Key;
                scores[symbol] += zScores[index] * weight;
            }
        }

        private static List<decimal> SafeZScores(IReadOnlyList<decimal> values)
        {
            if (values.Count <= 1) return values.Select(_ => 0m).ToList();
            var mean = values.Average();
            var variance = values.Select(v => Math.Pow((double)(v - mean), 2)).Average();
            var std = Math.Sqrt(variance);
            if (std <= double.Epsilon) return values.Select(_ => 0m).ToList();
            return values.Select(v => (decimal)(((double)v - (double)mean) / std)).ToList();
        }

        /// <summary>
        /// Normalize scores to [-1, 1] range using min-max scaling.
        /// </summary>
        private static Dictionary<Symbol, decimal> NormalizeScores(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            if (scores.Count == 0) return new Dictionary<Symbol, decimal>(scores);

            var maxAbs = scores.Values.Max(v => Math.Abs(v));
            if (maxAbs <= 0m) return new Dictionary<Symbol, decimal>(scores);

            return scores.ToDictionary(
                pair => pair.Key,
                pair => pair.Value / maxAbs);
        }

        #endregion

        #region Weight Normalization

        /// <summary>
        /// Normalize effective weights so that sum of absolute values = 1.
        /// This prevents composite score explosion from weight drift.
        /// </summary>
        private void NormalizeWeights()
        {
            var totalAbs = Math.Abs(_betaWeight) + Math.Abs(_momentumWeight) + Math.Abs(_sizeWeight) +
                           Math.Abs(_earningsYieldWeight) + Math.Abs(_residualVolatilityWeight) +
                           Math.Abs(_growthWeight) + Math.Abs(_bookToPriceWeight) + Math.Abs(_leverageWeight) +
                           Math.Abs(_liquidityWeight) + Math.Abs(_nonLinearSizeWeight) + Math.Abs(_moneyFlowWeight) +
                           Math.Abs(_qualityWeight) + Math.Abs(_northboundWeight) + Math.Abs(_marginWeight) +
                           Math.Abs(_chipCostWeight);

            if (totalAbs <= 0m) return;

            _betaWeight /= totalAbs;
            _momentumWeight /= totalAbs;
            _sizeWeight /= totalAbs;
            _earningsYieldWeight /= totalAbs;
            _residualVolatilityWeight /= totalAbs;
            _growthWeight /= totalAbs;
            _bookToPriceWeight /= totalAbs;
            _leverageWeight /= totalAbs;
            _liquidityWeight /= totalAbs;
            _nonLinearSizeWeight /= totalAbs;
            _moneyFlowWeight /= totalAbs;
            _qualityWeight /= totalAbs;
            _northboundWeight /= totalAbs;
            _marginWeight /= totalAbs;
            _chipCostWeight /= totalAbs;
        }

        #endregion

        #region Factor Z-Scores & IC

        private static readonly string[] FactorNames =
        {
            "beta", "momentum", "size", "earnyld", "resvol",
            "growth", "btop", "leverage", "liquidity", "nlsize",
            "moneyflow", "quality", "northbound", "margin", "chipcost"
        };

        private Dictionary<string, Dictionary<Symbol, decimal>> ComputeFactorZScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            var eligible = factors
                .Where(pair => pair.Value != null && pair.Value.PresentFactorCount >= _minimumPresentFactors)
                .ToList();

            var result = new Dictionary<string, Dictionary<Symbol, decimal>>(StringComparer.Ordinal);
            if (eligible.Count == 0) return result;

            var selectors = new (string name, Func<AShareBarraCNE5V2FactorData, decimal?> selector)[]
            {
                ("beta", f => f.Beta), ("momentum", f => f.Momentum), ("size", f => f.Size),
                ("earnyld", f => f.EarningsYield), ("resvol", f => f.ResidualVolatility),
                ("growth", f => f.Growth), ("btop", f => f.BookToPrice), ("leverage", f => f.Leverage),
                ("liquidity", f => f.Liquidity), ("nlsize", f => f.NonLinearSize),
                ("moneyflow", f => f.MoneyFlow), ("quality", f => f.Quality),
                ("northbound", f => f.Northbound), ("margin", f => f.Margin), ("chipcost", f => f.ChipCost)
            };

            foreach (var (name, selector) in selectors)
            {
                var withValues = new List<(Symbol symbol, decimal value)>();
                foreach (var pair in eligible)
                {
                    var val = selector(pair.Value);
                    if (val.HasValue) withValues.Add((pair.Key, val.Value));
                }
                if (withValues.Count <= 1) continue;
                // Re-z-score across CSI300 for IC computation (consistent with score computation)
                var zScores = SafeZScores(withValues.Select(x => x.value).ToList());
                var map = new Dictionary<Symbol, decimal>();
                for (var i = 0; i < withValues.Count; i++) map[withValues[i].symbol] = zScores[i];
                result[name] = map;
            }
            return result;
        }

        private static Dictionary<string, decimal> ComputeFactorIC(
            Dictionary<string, Dictionary<Symbol, decimal>> factorZScores,
            Dictionary<Symbol, decimal> nextPeriodReturns)
        {
            var result = new Dictionary<string, decimal>(StringComparer.Ordinal);
            if (nextPeriodReturns == null || nextPeriodReturns.Count < 2) return result;

            foreach (var factorName in FactorNames)
            {
                if (!factorZScores.TryGetValue(factorName, out var factorValues)) continue;
                var common = factorValues.Keys.Where(k => nextPeriodReturns.ContainsKey(k)).ToList();
                if (common.Count < 5) continue;
                var ranks1 = RankValues(common.Select(k => factorValues[k]).ToList());
                var ranks2 = RankValues(common.Select(k => nextPeriodReturns[k]).ToList());
                result[factorName] = SpearmanCorrelation(ranks1, ranks2);
            }
            return result;
        }

        #endregion

        #region Regime Detection

        private string DetectMarketRegime()
        {
            if (_dailyReturns.Count < _regimeVolLookbackDays) return "mid_vol";
            var lookback = _dailyReturns.TakeLast(_regimeVolLookbackDays).ToList();
            var mean = lookback.Average();
            var variance = lookback.Select(r => Math.Pow((double)(r - mean), 2)).Average();
            var std = (decimal)Math.Sqrt(variance);
            var annualizedVol = std * (decimal)Math.Sqrt(252);

            if (annualizedVol < _regimeLowVolThreshold) return "low_vol";
            if (annualizedVol > _regimeHighVolThreshold) return "high_vol";
            return "mid_vol";
        }

        /// <summary>
        /// Apply regime weights by blending from regime presets (not from previously-adjusted values).
        /// This prevents cumulative drift: each rebalance starts from the regime's canonical weights.
        /// </summary>
        private void ApplyRegimeWeights(string regime)
        {
            var regimeWeights = regime switch
            {
                "low_vol" => _lowVolWeights,
                "high_vol" => _highVolWeights,
                _ => _midVolWeights
            };

            var setters = new (string key, Action<decimal> setter)[]
            {
                ("beta", w => _betaWeight = w),
                ("momentum", w => _momentumWeight = w),
                ("size", w => _sizeWeight = w),
                ("earnyld", w => _earningsYieldWeight = w),
                ("resvol", w => _residualVolatilityWeight = w),
                ("growth", w => _growthWeight = w),
                ("btop", w => _bookToPriceWeight = w),
                ("leverage", w => _leverageWeight = w),
                ("liquidity", w => _liquidityWeight = w),
                ("nlsize", w => _nonLinearSizeWeight = w),
                ("moneyflow", w => _moneyFlowWeight = w),
                ("quality", w => _qualityWeight = w),
                ("northbound", w => _northboundWeight = w),
                ("margin", w => _marginWeight = w),
                ("chipcost", w => _chipCostWeight = w),
            };

            foreach (var (key, setter) in setters)
            {
                if (!regimeWeights.TryGetValue(key, out var targetWeight)) continue;
                // Blend from the regime target, not from current (which may be IR-adjusted)
                // This ensures regime switching always anchors to canonical weights
                var currentWeight = GetCurrentWeight(key);
                var blended = _regimeTransitionAlpha * targetWeight + (1m - _regimeTransitionAlpha) * currentWeight;
                setter(blended);
            }
        }

        private decimal GetCurrentWeight(string key) => key switch
        {
            "beta" => _betaWeight,
            "momentum" => _momentumWeight,
            "size" => _sizeWeight,
            "earnyld" => _earningsYieldWeight,
            "resvol" => _residualVolatilityWeight,
            "growth" => _growthWeight,
            "btop" => _bookToPriceWeight,
            "leverage" => _leverageWeight,
            "liquidity" => _liquidityWeight,
            "nlsize" => _nonLinearSizeWeight,
            "moneyflow" => _moneyFlowWeight,
            "quality" => _qualityWeight,
            "northbound" => _northboundWeight,
            "margin" => _marginWeight,
            "chipcost" => _chipCostWeight,
            _ => 0m
        };

        #endregion

        #region IC/IR Weight Adjustment

        /// <summary>
        /// Apply IR weight adjustment using REGIME BASE weights (not previously IR-adjusted weights).
        /// This prevents cumulative exponential drift.
        /// </summary>
        private void ApplyIRWeightAdjustment()
        {
            // Get the current regime's canonical weights as the IR adjustment base
            var regimeWeights = _currentRegime switch
            {
                "low_vol" => _lowVolWeights,
                "high_vol" => _highVolWeights,
                _ => _midVolWeights
            };

            var weightProperties = new (string factorName, Action<decimal> setter, string regimeKey)[]
            {
                ("beta", w => _betaWeight = w, "beta"),
                ("momentum", w => _momentumWeight = w, "momentum"),
                ("size", w => _sizeWeight = w, "size"),
                ("earnyld", w => _earningsYieldWeight = w, "earnyld"),
                ("resvol", w => _residualVolatilityWeight = w, "resvol"),
                ("growth", w => _growthWeight = w, "growth"),
                ("btop", w => _bookToPriceWeight = w, "btop"),
                ("leverage", w => _leverageWeight = w, "leverage"),
                ("liquidity", w => _liquidityWeight = w, "liquidity"),
                ("nlsize", w => _nonLinearSizeWeight = w, "nlsize"),
                ("moneyflow", w => _moneyFlowWeight = w, "moneyflow"),
                ("quality", w => _qualityWeight = w, "quality"),
                ("northbound", w => _northboundWeight = w, "northbound"),
                ("margin", w => _marginWeight = w, "margin"),
                ("chipcost", w => _chipCostWeight = w, "chipcost"),
            };

            foreach (var (factorName, setter, regimeKey) in weightProperties)
            {
                if (!_factorICHistory.TryGetValue(factorName, out var ics) || ics.Count < _icMinObservations)
                    continue;

                // Use the REGIME BASE weight, not the current (already-adjusted) weight
                if (!regimeWeights.TryGetValue(regimeKey, out var baseWeight)) continue;

                var meanIC = ics.Average();
                var stdIC = (decimal)Math.Sqrt(ics.Select(x => Math.Pow((double)(x - meanIC), 2)).Average());
                var ir = stdIC > 0m ? meanIC / stdIC : 0m;

                var sign = baseWeight >= 0m ? 1m : -1m;
                var magnitude = Math.Abs(baseWeight);
                // Clamp IR adjustment to [-50%, +50%] to prevent extreme drift
                var irClamp = Math.Max(-0.5m, Math.Min(0.5m, _irSensitivity * ir));
                var adjusted = sign * Math.Max(0m, magnitude * (1m + irClamp));
                setter(adjusted);
            }
        }

        private double ComputeAverageConfidence()
        {
            if (_factorICHistory.Count == 0) return 0.65;
            var absICs = new List<decimal>();
            foreach (var ics in _factorICHistory.Values)
            {
                if (ics.Count < _icMinObservations) continue;
                absICs.Add(ics.Select(x => Math.Abs(x)).Average());
            }
            if (absICs.Count == 0) return 0.65;
            var avgAbsIC = absICs.Average();
            return Math.Min(0.95, Math.Max(0.3, 0.5 + 0.3 * (double)avgAbsIC));
        }

        #endregion

        #region Selection

        private Dictionary<Symbol, decimal> SelectTopN(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            return scores
                .OrderByDescending(pair => pair.Value)
                .Take(_topN)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
        }

        private Dictionary<Symbol, decimal> SelectTopNStratified(
            IReadOnlyDictionary<Symbol, decimal> scores,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            var industryGroups = scores.Keys
                .GroupBy(s => _symbolIndustryMap.TryGetValue(s, out var ind) ? ind : "unknown")
                .ToDictionary(g => g.Key, g => g.ToList());

            if (industryGroups.Count == 0) return SelectTopN(scores);

            var totalStocks = industryGroups.Values.Sum(g => g.Count);
            var selected = new List<KeyValuePair<Symbol, decimal>>();

            foreach (var group in industryGroups)
            {
                var groupSlots = Math.Max(1, (int)Math.Round((decimal)group.Value.Count / totalStocks * _topN));
                var groupRanked = group.Value
                    .Select(s => new KeyValuePair<Symbol, decimal>(s, scores.TryGetValue(s, out var v) ? v : 0m))
                    .OrderByDescending(kvp => kvp.Value)
                    .Take(groupSlots)
                    .ToList();
                selected.AddRange(groupRanked);
            }

            if (selected.Count > _topN)
            {
                selected = selected.OrderByDescending(kvp => kvp.Value).Take(_topN).ToList();
            }

            return selected.ToDictionary(kvp => kvp.Key, kvp => kvp.Value);
        }

        #endregion

        #region Portfolio Exposure

        private static Dictionary<string, decimal> ComputePortfolioExposure(
            IReadOnlyDictionary<Symbol, decimal> scores,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            var result = new Dictionary<string, decimal>(StringComparer.Ordinal)
            {
                ["beta"] = 0m, ["momentum"] = 0m, ["size"] = 0m,
                ["earnyld"] = 0m, ["resvol"] = 0m, ["growth"] = 0m,
                ["btop"] = 0m, ["leverage"] = 0m, ["liquidity"] = 0m,
                ["nlsize"] = 0m, ["moneyflow"] = 0m, ["quality"] = 0m,
                ["northbound"] = 0m, ["margin"] = 0m, ["chipcost"] = 0m,
            };

            if (scores == null || factors == null || scores.Count == 0) return result;
            var totalWeight = scores.Values.Select(Math.Abs).Sum();
            if (totalWeight <= 0m) return result;

            foreach (var pair in scores)
            {
                if (!factors.TryGetValue(pair.Key, out var factor) || factor == null) continue;
                var nw = pair.Value / totalWeight;
                Accumulate(result, "beta", factor.Beta, nw);
                Accumulate(result, "momentum", factor.Momentum, nw);
                Accumulate(result, "size", factor.Size, nw);
                Accumulate(result, "earnyld", factor.EarningsYield, nw);
                Accumulate(result, "resvol", factor.ResidualVolatility, nw);
                Accumulate(result, "growth", factor.Growth, nw);
                Accumulate(result, "btop", factor.BookToPrice, nw);
                Accumulate(result, "leverage", factor.Leverage, nw);
                Accumulate(result, "liquidity", factor.Liquidity, nw);
                Accumulate(result, "nlsize", factor.NonLinearSize, nw);
                Accumulate(result, "moneyflow", factor.MoneyFlow, nw);
                Accumulate(result, "quality", factor.Quality, nw);
                Accumulate(result, "northbound", factor.Northbound, nw);
                Accumulate(result, "margin", factor.Margin, nw);
                Accumulate(result, "chipcost", factor.ChipCost, nw);
            }
            return result;
        }

        private static void Accumulate(IDictionary<string, decimal> result, string key, decimal? value, decimal normalizedWeight)
        {
            if (value.HasValue) result[key] += normalizedWeight * value.Value;
        }

        #endregion

        #region Rebalance Logic

        private bool ShouldRebalance(DateTime currentDate)
        {
            if (_lastRebalanceDate == default) return true;
            var token = (_rebalanceFrequency ?? "monthly").Trim().ToLowerInvariant();
            switch (token)
            {
                case "weekly": return (currentDate.Date - _lastRebalanceDate.Date).TotalDays >= 7;
                case "biweekly": return (currentDate.Date - _lastRebalanceDate.Date).TotalDays >= 14;
                case "semi-monthly":
                case "semimonthly":
                    var day = currentDate.Day;
                    if (day != 1 && day != 16) return false;
                    return (currentDate.Date - _lastRebalanceDate.Date).TotalDays >= 10;
                default: return currentDate.Year != _lastRebalanceDate.Year || currentDate.Month != _lastRebalanceDate.Month;
            }
        }

        private static TimeSpan ResolveInsightPeriod(string frequency)
        {
            return frequency?.Trim().ToLowerInvariant() switch
            {
                "weekly" => TimeSpan.FromDays(7),
                "biweekly" => TimeSpan.FromDays(14),
                _ => TimeSpan.FromDays(30)
            };
        }

        #endregion

        #region Eligibility

        private bool IsEligibleFactor(AShareBarraCNE5V2FactorData factor)
        {
            if (factor == null || factor.IsSt) return false;
            if (factor.ListedDays.HasValue && factor.ListedDays.Value < _minListedDays) return false;
            if (factor.MissingFactorCount.HasValue && factor.MissingFactorCount.Value > _maxMissingFactorCount) return false;
            if (factor.TurnoverRate.HasValue && factor.TurnoverRate.Value < _minTurnoverRate) return false;
            if (_minTotalMv.HasValue && factor.TotalMv.HasValue && factor.TotalMv.Value < _minTotalMv.Value) return false;
            return factor.PresentFactorCount >= _minimumPresentFactors;
        }

        #endregion

        #region Industry Classification

        private void LoadIndustryClassification(string path)
        {
            if (!File.Exists(path))
            {
                _stratifiedSelectionEnabled = false;
                return;
            }

            var loadedCount = 0;
            foreach (var line in File.ReadLines(path).Skip(1))
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                var parts = line.Split(',');
                if (parts.Length < 5) continue;

                var tsCode = parts[0].Trim();
                var swL1Name = parts[4].Trim();
                if (string.IsNullOrWhiteSpace(tsCode) || string.IsNullOrWhiteSpace(swL1Name)) continue;

                if (!TryParseTsCode(tsCode, out var symbol)) continue;

                _symbolIndustryMap[symbol] = swL1Name;
                loadedCount++;
            }

            if (_symbolIndustryMap.Count == 0)
            {
                _stratifiedSelectionEnabled = false;
            }
        }

        private static bool TryParseTsCode(string tsCode, out Symbol symbol)
        {
            symbol = default;
            if (string.IsNullOrWhiteSpace(tsCode)) return false;
            var parts = tsCode.Split('.', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length != 2) return false;
            var ticker = parts[0].Trim();
            var marketSuffix = parts[1].Trim().ToUpperInvariant();
            var market = marketSuffix == "SH" ? Market.SSE : Market.SZSE;
            symbol = Symbol.Create(ticker, SecurityType.Equity, market);
            return true;
        }

        #endregion

        #region Statistics Helpers

        private static decimal GetScoreSpread(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            if (scores == null || scores.Count == 0) return 0m;
            var orderedScores = scores.Values.OrderBy(v => v).ToList();
            var median = GetMedian(orderedScores);
            return scores.Values.Max() - median;
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0) return 0m;
            var midpoint = values.Count / 2;
            return values.Count % 2 == 0
                ? (values[midpoint - 1] + values[midpoint]) / 2m
                : values[midpoint];
        }

        #endregion

        #region Spearman Rank Correlation

        private static List<decimal> RankValues(IReadOnlyList<decimal> values)
        {
            var n = values.Count;
            var indices = Enumerable.Range(0, n).OrderBy(i => values[i]).ToList();
            var ranks = new decimal[n];
            var i = 0;
            while (i < n)
            {
                var j = i;
                while (j < n - 1 && values[indices[j + 1]] == values[indices[j]]) j++;
                var avgRank = (i + j) / 2m + 1m;
                for (var k = i; k <= j; k++) ranks[indices[k]] = avgRank;
                i = j + 1;
            }
            return ranks.ToList();
        }

        private static decimal SpearmanCorrelation(IReadOnlyList<decimal> ranks1, IReadOnlyList<decimal> ranks2)
        {
            var n = ranks1.Count;
            if (n < 3) return 0m;
            var mean1 = ranks1.Average();
            var mean2 = ranks2.Average();
            var cov = 0m;
            var var1 = 0m;
            var var2 = 0m;
            for (var i = 0; i < n; i++)
            {
                var d1 = ranks1[i] - mean1;
                var d2 = ranks2[i] - mean2;
                cov += d1 * d2;
                var1 += d1 * d1;
                var2 += d2 * d2;
            }
            var denom = (decimal)Math.Sqrt((double)var1 * (double)var2);
            return denom > 0m ? cov / denom : 0m;
        }

        #endregion
    }
}
