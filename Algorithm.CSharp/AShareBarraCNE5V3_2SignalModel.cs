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

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Cross-sectional multi-factor scoring model for Barra CNE5 V3.2
    /// V3.2 extends V2.1 with: regime-dependent weights, IC/IR weight calibration,
    /// stratified industry selection, turnover constraint, and vol targeting.
    /// </summary>
    public static class AShareBarraCNE5V3_2SignalModel
    {
        private static readonly string[] FactorNames =
        {
            "beta", "momentum", "size", "earnyld", "resvol",
            "growth", "btop", "leverage", "liquidity", "nlsize",
            "moneyflow", "quality", "northbound", "margin", "chipcost"
        };

        public static Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            AShareBarraCNE5V3_2SignalSettings settings)
        {
            settings ??= new AShareBarraCNE5V3_2SignalSettings();
            var eligible = factors
                .Where(pair => pair.Value != null && pair.Value.PresentFactorCount >= settings.MinimumPresentFactors)
                .ToList();

            var scores = eligible.ToDictionary(pair => pair.Key, _ => 0m);
            if (eligible.Count == 0)
            {
                return scores;
            }

            ApplyFactor(eligible, scores, factor => factor.Beta, settings.BetaWeight);
            ApplyFactor(eligible, scores, factor => factor.Momentum, settings.MomentumWeight);
            ApplyFactor(eligible, scores, factor => factor.Size, settings.SizeWeight);
            ApplyFactor(eligible, scores, factor => factor.EarningsYield, settings.EarningsYieldWeight);
            ApplyFactor(eligible, scores, factor => factor.ResidualVolatility, settings.ResidualVolatilityWeight);
            ApplyFactor(eligible, scores, factor => factor.Growth, settings.GrowthWeight);
            ApplyFactor(eligible, scores, factor => factor.BookToPrice, settings.BookToPriceWeight);
            ApplyFactor(eligible, scores, factor => factor.Leverage, settings.LeverageWeight);
            ApplyFactor(eligible, scores, factor => factor.Liquidity, settings.LiquidityWeight);
            ApplyFactor(eligible, scores, factor => factor.NonLinearSize, settings.NonLinearSizeWeight);
            ApplyFactor(eligible, scores, factor => factor.MoneyFlow, settings.MoneyFlowWeight);
            ApplyFactor(eligible, scores, factor => factor.Quality, settings.QualityWeight);
            ApplyFactor(eligible, scores, factor => factor.Northbound, settings.NorthboundWeight);
            ApplyFactor(eligible, scores, factor => factor.Margin, settings.MarginWeight);
            ApplyFactor(eligible, scores, factor => factor.ChipCost, settings.ChipCostWeight);
            return scores;
        }

        /// <summary>
        /// Compute per-factor z-scores for IC calculation (returns factor name -> symbol -> z-score)
        /// </summary>
        public static Dictionary<string, Dictionary<Symbol, decimal>> ComputeFactorZScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            AShareBarraCNE5V3_2SignalSettings settings)
        {
            var eligible = factors
                .Where(pair => pair.Value != null && pair.Value.PresentFactorCount >= settings.MinimumPresentFactors)
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
                    if (val.HasValue)
                    {
                        withValues.Add((pair.Key, val.Value));
                    }
                }

                if (withValues.Count <= 1) continue;
                var zScores = SafeZScores(withValues.Select(x => x.value).ToList());
                var map = new Dictionary<Symbol, decimal>();
                for (var i = 0; i < withValues.Count; i++)
                {
                    map[withValues[i].symbol] = zScores[i];
                }
                result[name] = map;
            }

            return result;
        }

        /// <summary>
        /// Compute Spearman rank IC for each factor against next-period returns
        /// </summary>
        public static Dictionary<string, decimal> ComputeFactorIC(
            Dictionary<string, Dictionary<Symbol, decimal>> factorZScores,
            Dictionary<Symbol, decimal> nextPeriodReturns)
        {
            var result = new Dictionary<string, decimal>(StringComparer.Ordinal);
            if (nextPeriodReturns == null || nextPeriodReturns.Count < 2) return result;

            foreach (var factorName in FactorNames)
            {
                if (!factorZScores.TryGetValue(factorName, out var factorValues)) continue;

                var common = factorValues.Keys
                    .Where(k => nextPeriodReturns.ContainsKey(k))
                    .ToList();

                if (common.Count < 5) continue;

                var ranks1 = RankValues(common.Select(k => factorValues[k]).ToList());
                var ranks2 = RankValues(common.Select(k => nextPeriodReturns[k]).ToList());

                var ic = SpearmanCorrelation(ranks1, ranks2);
                result[factorName] = ic;
            }

            return result;
        }

        /// <summary>
        /// Adjust factor weights based on IC/IR history
        /// </summary>
        public static void ApplyIRWeightAdjustment(
            AShareBarraCNE5V3_2SignalSettings settings,
            Dictionary<string, List<decimal>> icHistory,
            int minObservations,
            decimal irSensitivity)
        {
            var weightProperties = new (string factorName, System.Action<decimal> setter, decimal baseWeight)[]
            {
                ("beta", w => settings.BetaWeight = w, settings.BetaWeight),
                ("momentum", w => settings.MomentumWeight = w, settings.MomentumWeight),
                ("size", w => settings.SizeWeight = w, settings.SizeWeight),
                ("earnyld", w => settings.EarningsYieldWeight = w, settings.EarningsYieldWeight),
                ("resvol", w => settings.ResidualVolatilityWeight = w, settings.ResidualVolatilityWeight),
                ("growth", w => settings.GrowthWeight = w, settings.GrowthWeight),
                ("btop", w => settings.BookToPriceWeight = w, settings.BookToPriceWeight),
                ("leverage", w => settings.LeverageWeight = w, settings.LeverageWeight),
                ("liquidity", w => settings.LiquidityWeight = w, settings.LiquidityWeight),
                ("nlsize", w => settings.NonLinearSizeWeight = w, settings.NonLinearSizeWeight),
                ("moneyflow", w => settings.MoneyFlowWeight = w, settings.MoneyFlowWeight),
                ("quality", w => settings.QualityWeight = w, settings.QualityWeight),
                ("northbound", w => settings.NorthboundWeight = w, settings.NorthboundWeight),
                ("margin", w => settings.MarginWeight = w, settings.MarginWeight),
                ("chipcost", w => settings.ChipCostWeight = w, settings.ChipCostWeight),
            };

            foreach (var (factorName, setter, baseWeight) in weightProperties)
            {
                if (!icHistory.TryGetValue(factorName, out var ics) || ics.Count < minObservations)
                {
                    continue;
                }

                var meanIC = ics.Average();
                var stdIC = (decimal)Math.Sqrt(ics.Select(x => Math.Pow((double)(x - meanIC), 2)).Average());
                var ir = stdIC > 0m ? meanIC / stdIC : 0m;

                // Dynamic weight: sign(base) * max(0, |base| * (1 + irSensitivity * ir))
                var sign = baseWeight >= 0m ? 1m : -1m;
                var magnitude = Math.Abs(baseWeight);
                var adjusted = sign * Math.Max(0m, magnitude * (1m + irSensitivity * ir));
                setter(adjusted);
            }
        }

        /// <summary>
        /// Select portfolio using industry-stratified selection
        /// </summary>
        public static List<AShareBarraCNE5V3_2Target> SelectPortfolioStratified(
            IReadOnlyDictionary<Symbol, decimal> scores,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            Dictionary<Symbol, string> industryMap,
            int topN,
            decimal minScoreSpread,
            decimal targetExposure,
            string weightingMode,
            AShareBarraCNE5V3_2SignalSettings settings = null)
        {
            settings ??= new AShareBarraCNE5V3_2SignalSettings { WeightingMode = weightingMode };
            if (scores == null || scores.Count == 0 || topN <= 0 || targetExposure <= 0m)
            {
                return new List<AShareBarraCNE5V3_2Target>();
            }

            var orderedScores = scores.Values.OrderBy(v => v).ToList();
            var median = GetMedian(orderedScores);
            var spread = scores.Values.Max() - median;
            if (spread < minScoreSpread)
            {
                return new List<AShareBarraCNE5V3_2Target>();
            }

            // Group by industry
            var industryGroups = scores.Keys
                .GroupBy(s => industryMap != null && industryMap.TryGetValue(s, out var ind) ? ind : "unknown")
                .ToDictionary(g => g.Key, g => g.ToList());

            if (industryGroups.Count == 0)
            {
                return new List<AShareBarraCNE5V3_2Target>();
            }

            // Allocate slots proportionally per industry
            var totalStocks = industryGroups.Values.Sum(g => g.Count);
            var selected = new List<KeyValuePair<Symbol, decimal>>();

            foreach (var group in industryGroups)
            {
                var groupSlots = Math.Max(1, (int)Math.Round((decimal)group.Value.Count / totalStocks * topN));
                var groupRanked = group.Value
                    .Select(s => new KeyValuePair<Symbol, decimal>(s, scores.TryGetValue(s, out var v) ? v : 0m))
                    .OrderByDescending(kvp => kvp.Value)
                    .Take(groupSlots)
                    .ToList();
                selected.AddRange(groupRanked);
            }

            // If we selected more than topN, keep only topN by score
            if (selected.Count > topN)
            {
                selected = selected.OrderByDescending(kvp => kvp.Value).Take(topN).ToList();
            }

            var mode = (weightingMode ?? settings.WeightingMode ?? "equal").Trim().ToLowerInvariant();
            Dictionary<Symbol, decimal> rawWeights;
            switch (mode)
            {
                case "market-cap":
                case "marketcap":
                    rawWeights = BuildPriorWeights(selected, factors, useMarketCap: true);
                    break;
                case "black-litterman":
                case "blacklitterman":
                case "bl":
                    rawWeights = BuildBlackLittermanKellyWeights(selected, factors, settings);
                    break;
                default:
                    rawWeights = BuildPriorWeights(selected, factors, useMarketCap: false);
                    break;
            }

            var normalizedWeights = NormalizeAndCapWeights(rawWeights, targetExposure, settings.MaxSingleWeight);
            return selected
                .Where(item => normalizedWeights.TryGetValue(item.Key, out var weight) && weight > 0m)
                .Select(item => new AShareBarraCNE5V3_2Target
                {
                    Symbol = item.Key,
                    Weight = normalizedWeights[item.Key],
                    Score = item.Value
                })
                .ToList();
        }

        public static List<AShareBarraCNE5V3_2Target> SelectPortfolio(
            IReadOnlyDictionary<Symbol, decimal> scores,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            int topN,
            decimal minScoreSpread,
            decimal targetExposure,
            string weightingMode,
            AShareBarraCNE5V3_2SignalSettings settings = null)
        {
            settings ??= new AShareBarraCNE5V3_2SignalSettings { WeightingMode = weightingMode };
            if (scores == null || scores.Count == 0 || topN <= 0 || targetExposure <= 0m)
            {
                return new List<AShareBarraCNE5V3_2Target>();
            }

            var ranked = scores
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                .ToList();

            var orderedScores = ranked.Select(pair => pair.Value).OrderBy(value => value).ToList();
            var median = GetMedian(orderedScores);
            var spread = ranked[0].Value - median;
            if (spread < minScoreSpread)
            {
                return new List<AShareBarraCNE5V3_2Target>();
            }

            var selected = ranked.Take(topN).ToList();
            var mode = (weightingMode ?? settings.WeightingMode ?? "equal").Trim().ToLowerInvariant();
            Dictionary<Symbol, decimal> rawWeights;
            switch (mode)
            {
                case "market-cap":
                case "marketcap":
                    rawWeights = BuildPriorWeights(selected, factors, useMarketCap: true);
                    break;
                case "black-litterman":
                case "blacklitterman":
                case "bl":
                    rawWeights = BuildBlackLittermanKellyWeights(selected, factors, settings);
                    break;
                default:
                    rawWeights = BuildPriorWeights(selected, factors, useMarketCap: false);
                    break;
            }

            var normalizedWeights = NormalizeAndCapWeights(rawWeights, targetExposure, settings.MaxSingleWeight);
            return selected
                .Where(item => normalizedWeights.TryGetValue(item.Key, out var weight) && weight > 0m)
                .Select(item => new AShareBarraCNE5V3_2Target
                {
                    Symbol = item.Key,
                    Weight = normalizedWeights[item.Key],
                    Score = item.Value
                })
                .ToList();
        }

        public static Dictionary<string, decimal> ComputePortfolioExposure(
            IReadOnlyDictionary<Symbol, decimal> weights,
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

            if (weights == null || factors == null || weights.Count == 0) return result;
            var totalWeight = weights.Values.Select(Math.Abs).Sum();
            if (totalWeight <= 0m) return result;

            foreach (var pair in weights)
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
            var cov = 0m; var var1 = 0m; var var2 = 0m;
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

        #region Private Helpers (same as V2.1)

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
            var variance = values.Select(value => Math.Pow((double)(value - mean), 2)).Average();
            var standardDeviation = Math.Sqrt(variance);
            if (standardDeviation <= double.Epsilon) return values.Select(_ => 0m).ToList();
            return values.Select(value => (decimal)(((double)value - (double)mean) / standardDeviation)).ToList();
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0) return 0m;
            var midpoint = values.Count / 2;
            return values.Count % 2 == 0 ? (values[midpoint - 1] + values[midpoint]) / 2m : values[midpoint];
        }

        private static void Accumulate(IDictionary<string, decimal> result, string key, decimal? value, decimal normalizedWeight)
        {
            if (value.HasValue) result[key] += normalizedWeight * value.Value;
        }

        private static Dictionary<Symbol, decimal> BuildPriorWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            bool useMarketCap)
        {
            var weights = new Dictionary<Symbol, decimal>();
            if (selected == null || selected.Count == 0) return weights;
            if (!useMarketCap)
            {
                foreach (var item in selected) weights[item.Key] = 1m;
                return weights;
            }
            var totalMarketValue = selected
                .Select(item => factors.TryGetValue(item.Key, out var factor) ? factor.TotalMv.GetValueOrDefault() : 0m)
                .Where(value => value > 0m).Sum();
            if (totalMarketValue <= 0m)
            {
                foreach (var item in selected) weights[item.Key] = 1m;
                return weights;
            }
            foreach (var item in selected)
            {
                var marketValue = factors.TryGetValue(item.Key, out var factor) ? factor.TotalMv.GetValueOrDefault() : 0m;
                weights[item.Key] = marketValue > 0m ? marketValue / totalMarketValue : 0m;
            }
            return weights;
        }

        private static Dictionary<Symbol, decimal> BuildBlackLittermanKellyWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors,
            AShareBarraCNE5V3_2SignalSettings settings)
        {
            // V3.2 fix: Use equal-weight prior instead of market-cap prior
            // to avoid Size signal contradiction (market-cap tilts large, Size weight is negative)
            var priorWeights = BuildPriorWeights(selected, factors, useMarketCap: settings.UseMarketCapPrior);
            var viewScores = SafeZScores(selected.Select(item => item.Value).ToList());
            var tau = Clamp(settings.BlackLittermanTau, 0.01m, 1.0m);
            var confidence = Clamp(settings.BlackLittermanViewConfidence, 0.05m, 0.95m);
            var omega = Math.Max(0.05m, 1m - confidence);
            var priorBlend = Clamp(settings.BlackLittermanPriorBlend, 0m, 1m);
            var kellyFraction = Clamp(settings.KellyWeightFraction, 0m, 1m);

            var rawKellyWeights = new Dictionary<Symbol, decimal>();
            for (var index = 0; index < selected.Count; index++)
            {
                var item = selected[index];
                var symbol = item.Key;
                var priorWeight = priorWeights.TryGetValue(symbol, out var weight) ? weight : 0m;
                var priorReturn = settings.BlackLittermanRiskAversion * priorWeight;
                var viewReturn = settings.BlackLittermanViewScale * viewScores[index];
                var posteriorReturn = (priorReturn / tau + viewReturn / omega) / (1m / tau + 1m / omega);
                var factor = factors.TryGetValue(symbol, out var resolvedFactor) ? resolvedFactor : null;
                var riskProxy = ComputeKellyRiskProxy(factor, settings);
                var kellyWeight = posteriorReturn > 0m && riskProxy > 0m
                    ? (posteriorReturn / riskProxy) * kellyFraction : 0m;
                rawKellyWeights[symbol] = Math.Max(0m, kellyWeight);
            }

            var normalizedKellyWeights = NormalizePositiveWeights(rawKellyWeights);
            if (normalizedKellyWeights.Count == 0) return priorWeights;

            var blendedWeights = new Dictionary<Symbol, decimal>();
            foreach (var item in selected)
            {
                var symbol = item.Key;
                var priorWeight = priorWeights.TryGetValue(symbol, out var prior) ? prior : 0m;
                var kellyWeight = normalizedKellyWeights.TryGetValue(symbol, out var kelly) ? kelly : 0m;
                blendedWeights[symbol] = priorBlend * priorWeight + (1m - priorBlend) * kellyWeight;
            }
            return blendedWeights;
        }

        private static Dictionary<Symbol, decimal> NormalizeAndCapWeights(
            IReadOnlyDictionary<Symbol, decimal> rawWeights, decimal targetExposure, decimal maxSingleWeight)
        {
            var exposure = Math.Max(0m, targetExposure);
            if (rawWeights == null || rawWeights.Count == 0 || exposure <= 0m) return new Dictionary<Symbol, decimal>();
            var normalized = NormalizePositiveWeights(rawWeights);
            if (normalized.Count == 0) return new Dictionary<Symbol, decimal>();
            var cap = maxSingleWeight > 0m ? Math.Min(maxSingleWeight, exposure) : exposure;
            var result = normalized.ToDictionary(pair => pair.Key, _ => 0m);
            var remaining = normalized.ToDictionary(pair => pair.Key, pair => pair.Value);
            var remainingExposure = exposure;
            while (remaining.Count > 0 && remainingExposure > 0m)
            {
                var totalRemaining = remaining.Values.Sum();
                if (totalRemaining <= 0m) break;
                var cappedAny = false;
                foreach (var pair in remaining.ToList())
                {
                    var proposed = remainingExposure * pair.Value / totalRemaining;
                    if (proposed <= cap) continue;
                    result[pair.Key] = cap;
                    remainingExposure -= cap;
                    remaining.Remove(pair.Key);
                    cappedAny = true;
                }
                if (cappedAny) continue;
                foreach (var pair in remaining) result[pair.Key] = remainingExposure * pair.Value / totalRemaining;
                break;
            }
            return result.Where(pair => pair.Value > 0m).ToDictionary(pair => pair.Key, pair => pair.Value);
        }

        private static Dictionary<Symbol, decimal> NormalizePositiveWeights(IReadOnlyDictionary<Symbol, decimal> rawWeights)
        {
            var filtered = rawWeights.Where(pair => pair.Value > 0m).ToList();
            if (filtered.Count == 0) return new Dictionary<Symbol, decimal>();
            var total = filtered.Sum(pair => pair.Value);
            if (total <= 0m) return new Dictionary<Symbol, decimal>();
            return filtered.ToDictionary(pair => pair.Key, pair => pair.Value / total);
        }

        private static decimal ComputeKellyRiskProxy(AShareBarraCNE5V2FactorData factor, AShareBarraCNE5V3_2SignalSettings settings)
        {
            var riskProxy = settings.KellyVarianceFloor;
            if (factor?.ResidualVolatility != null) riskProxy += Math.Abs(factor.ResidualVolatility.Value) * settings.KellyResidualVolatilityScale;
            if (factor?.Beta != null) riskProxy += Math.Abs(factor.Beta.Value) * settings.KellyBetaPenaltyScale;
            return Math.Max(settings.KellyVarianceFloor, riskProxy);
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue) return minValue;
            return value > maxValue ? maxValue : value;
        }

        #endregion
    }

    /// <summary>
    /// V3.2 signal settings with regime weight sets, IC/IR parameters, and all V2.1 parameters.
    /// </summary>
    public sealed class AShareBarraCNE5V3_2SignalSettings
    {
        // Base factor weights (mid_vol regime defaults, same as V2.1)
        public decimal BetaWeight { get; set; } = -0.05m;
        public decimal MomentumWeight { get; set; } = 0.25m;
        public decimal SizeWeight { get; set; } = -0.05m;
        public decimal EarningsYieldWeight { get; set; } = 0.20m;
        public decimal ResidualVolatilityWeight { get; set; } = -0.10m;
        public decimal GrowthWeight { get; set; } = 0.15m;
        public decimal BookToPriceWeight { get; set; } = 0.10m;
        public decimal LeverageWeight { get; set; } = -0.05m;
        public decimal LiquidityWeight { get; set; } = 0.05m;
        public decimal NonLinearSizeWeight { get; set; } = 0.00m;
        public decimal MoneyFlowWeight { get; set; } = 0.10m;
        public decimal QualityWeight { get; set; } = 0.20m;
        public decimal NorthboundWeight { get; set; } = 0.08m;
        public decimal MarginWeight { get; set; } = 0.05m;
        public decimal ChipCostWeight { get; set; } = 0.07m;

        // Regime weight sets
        public Dictionary<string, decimal> LowVolWeights { get; set; } = new()
        {
            ["beta"] = -0.03m, ["momentum"] = 0.30m, ["size"] = -0.03m,
            ["earnyld"] = 0.20m, ["resvol"] = -0.08m, ["growth"] = 0.20m,
            ["btop"] = 0.08m, ["leverage"] = -0.03m, ["liquidity"] = 0.05m,
            ["nlsize"] = 0.00m, ["moneyflow"] = 0.10m, ["quality"] = 0.20m,
            ["northbound"] = 0.08m, ["margin"] = 0.05m, ["chipcost"] = 0.07m
        };
        public Dictionary<string, decimal> MidVolWeights { get; set; } = new()
        {
            ["beta"] = -0.05m, ["momentum"] = 0.25m, ["size"] = -0.05m,
            ["earnyld"] = 0.20m, ["resvol"] = -0.10m, ["growth"] = 0.15m,
            ["btop"] = 0.10m, ["leverage"] = -0.05m, ["liquidity"] = 0.05m,
            ["nlsize"] = 0.00m, ["moneyflow"] = 0.10m, ["quality"] = 0.20m,
            ["northbound"] = 0.08m, ["margin"] = 0.05m, ["chipcost"] = 0.07m
        };
        public Dictionary<string, decimal> HighVolWeights { get; set; } = new()
        {
            ["beta"] = -0.08m, ["momentum"] = 0.15m, ["size"] = -0.10m,
            ["earnyld"] = 0.25m, ["resvol"] = -0.15m, ["growth"] = 0.10m,
            ["btop"] = 0.15m, ["leverage"] = -0.08m, ["liquidity"] = 0.03m,
            ["nlsize"] = 0.00m, ["moneyflow"] = 0.12m, ["quality"] = 0.25m,
            ["northbound"] = 0.05m, ["margin"] = 0.08m, ["chipcost"] = 0.10m
        };

        // IC/IR parameters
        public decimal IRSensitivity { get; set; } = 0.50m;
        public int ICLookbackPeriods { get; set; } = 60;
        public int ICMinObservations { get; set; } = 12;

        // General
        public int MinimumPresentFactors { get; set; } = 8;
        public string WeightingMode { get; set; } = "black-litterman";

        // V3.2: Use equal-weight prior to avoid Size signal contradiction
        public bool UseMarketCapPrior { get; set; } = false;

        // Black-Litterman / Kelly
        public decimal BlackLittermanTau { get; set; } = 0.05m;
        public decimal BlackLittermanRiskAversion { get; set; } = 2.20m;
        public decimal BlackLittermanViewScale { get; set; } = 0.08m;
        public decimal BlackLittermanViewConfidence { get; set; } = 0.65m;
        public decimal BlackLittermanPriorBlend { get; set; } = 0.30m;
        public decimal KellyWeightFraction { get; set; } = 0.50m;
        public decimal KellyVarianceFloor { get; set; } = 0.35m;
        public decimal KellyResidualVolatilityScale { get; set; } = 0.40m;
        public decimal KellyBetaPenaltyScale { get; set; } = 0.10m;
        public decimal MaxSingleWeight { get; set; } = 0.10m;
    }

    public sealed class AShareBarraCNE5V3_2Target
    {
        public Symbol Symbol { get; init; }
        public decimal Weight { get; init; }
        public decimal Score { get; init; }
    }
}
