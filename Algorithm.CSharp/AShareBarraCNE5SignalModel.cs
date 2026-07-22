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
    public sealed class AShareBarraCNE5SignalSettings
    {
        public decimal BetaWeight { get; set; } = -0.05m;
        public decimal MomentumWeight { get; set; } = 0.20m;
        public decimal SizeWeight { get; set; } = -0.10m;
        public decimal EarningsYieldWeight { get; set; } = 0.20m;
        public decimal ResidualVolatilityWeight { get; set; } = -0.10m;
        public decimal GrowthWeight { get; set; } = 0.15m;
        public decimal BookToPriceWeight { get; set; } = 0.10m;
        public decimal LeverageWeight { get; set; } = -0.05m;
        public decimal LiquidityWeight { get; set; } = 0.05m;
        public decimal NonLinearSizeWeight { get; set; } = 0.00m;
        public int MinimumPresentFactors { get; set; } = 6;
        public string WeightingMode { get; set; } = "black-litterman";
        public decimal BlackLittermanTau { get; set; } = 0.05m;
        public decimal BlackLittermanRiskAversion { get; set; } = 2.20m;
        public decimal BlackLittermanViewScale { get; set; } = 0.08m;
        public decimal BlackLittermanViewConfidence { get; set; } = 0.65m;
        public decimal BlackLittermanPriorBlend { get; set; } = 0.30m;
        public decimal KellyWeightFraction { get; set; } = 0.50m;
        public decimal KellyVarianceFloor { get; set; } = 0.35m;
        public decimal KellyResidualVolatilityScale { get; set; } = 0.40m;
        public decimal KellyBetaPenaltyScale { get; set; } = 0.10m;
        public decimal MaxSingleWeight { get; set; } = 0.12m;
    }

    public sealed class AShareBarraCNE5Target
    {
        public Symbol Symbol { get; init; }
        public decimal Weight { get; init; }
        public decimal Score { get; init; }
    }

    /// <summary>
    /// Cross-sectional multi-factor scoring model for Barra CNE5 inputs.
    /// </summary>
    public static class AShareBarraCNE5SignalModel
    {
        public static Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors,
            AShareBarraCNE5SignalSettings settings)
        {
            settings ??= new AShareBarraCNE5SignalSettings();
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
            return scores;
        }

        public static List<AShareBarraCNE5Target> SelectPortfolio(
            IReadOnlyDictionary<Symbol, decimal> scores,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors,
            int topN,
            decimal minScoreSpread,
            decimal targetExposure,
            string weightingMode,
            AShareBarraCNE5SignalSettings settings = null)
        {
            settings ??= new AShareBarraCNE5SignalSettings
            {
                WeightingMode = weightingMode
            };
            if (scores == null || scores.Count == 0 || topN <= 0 || targetExposure <= 0m)
            {
                return new List<AShareBarraCNE5Target>();
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
                return new List<AShareBarraCNE5Target>();
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
                .Select(item => new AShareBarraCNE5Target
                {
                    Symbol = item.Key,
                    Weight = normalizedWeights[item.Key],
                    Score = item.Value
                })
                .ToList();
        }

        public static Dictionary<string, decimal> ComputePortfolioExposure(
            IReadOnlyDictionary<Symbol, decimal> weights,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors)
        {
            var result = new Dictionary<string, decimal>(StringComparer.Ordinal)
            {
                ["beta"] = 0m,
                ["momentum"] = 0m,
                ["size"] = 0m,
                ["earnyld"] = 0m,
                ["resvol"] = 0m,
                ["growth"] = 0m,
                ["btop"] = 0m,
                ["leverage"] = 0m,
                ["liquidity"] = 0m,
                ["nlsize"] = 0m,
            };

            if (weights == null || factors == null || weights.Count == 0)
            {
                return result;
            }

            var totalWeight = weights.Values.Select(Math.Abs).Sum();
            if (totalWeight <= 0m)
            {
                return result;
            }

            foreach (var pair in weights)
            {
                if (!factors.TryGetValue(pair.Key, out var factor) || factor == null)
                {
                    continue;
                }

                var normalizedWeight = pair.Value / totalWeight;
                Accumulate(result, "beta", factor.Beta, normalizedWeight);
                Accumulate(result, "momentum", factor.Momentum, normalizedWeight);
                Accumulate(result, "size", factor.Size, normalizedWeight);
                Accumulate(result, "earnyld", factor.EarningsYield, normalizedWeight);
                Accumulate(result, "resvol", factor.ResidualVolatility, normalizedWeight);
                Accumulate(result, "growth", factor.Growth, normalizedWeight);
                Accumulate(result, "btop", factor.BookToPrice, normalizedWeight);
                Accumulate(result, "leverage", factor.Leverage, normalizedWeight);
                Accumulate(result, "liquidity", factor.Liquidity, normalizedWeight);
                Accumulate(result, "nlsize", factor.NonLinearSize, normalizedWeight);
            }

            return result;
        }

        private static void ApplyFactor(
            IReadOnlyList<KeyValuePair<Symbol, AShareBarraCNE5FactorData>> eligible,
            IDictionary<Symbol, decimal> scores,
            Func<AShareBarraCNE5FactorData, decimal?> selector,
            decimal weight)
        {
            if (weight == 0m)
            {
                return;
            }

            var indicesWithValues = new List<int>();
            var values = new List<decimal>();
            for (var index = 0; index < eligible.Count; index++)
            {
                var value = selector(eligible[index].Value);
                if (!value.HasValue)
                {
                    continue;
                }

                indicesWithValues.Add(index);
                values.Add(value.Value);
            }

            if (values.Count <= 1)
            {
                return;
            }

            var zScores = SafeZScores(values);
            for (var index = 0; index < indicesWithValues.Count; index++)
            {
                var symbol = eligible[indicesWithValues[index]].Key;
                scores[symbol] += zScores[index] * weight;
            }
        }

        private static List<decimal> SafeZScores(IReadOnlyList<decimal> values)
        {
            if (values.Count <= 1)
            {
                return values.Select(_ => 0m).ToList();
            }

            var mean = values.Average();
            var variance = values.Select(value => Math.Pow((double)(value - mean), 2)).Average();
            var standardDeviation = Math.Sqrt(variance);
            if (standardDeviation <= double.Epsilon)
            {
                return values.Select(_ => 0m).ToList();
            }

            return values
                .Select(value => (decimal)(((double)value - (double)mean) / standardDeviation))
                .ToList();
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0)
            {
                return 0m;
            }

            var midpoint = values.Count / 2;
            return values.Count % 2 == 0
                ? (values[midpoint - 1] + values[midpoint]) / 2m
                : values[midpoint];
        }

        private static void Accumulate(IDictionary<string, decimal> result, string key, decimal? value, decimal normalizedWeight)
        {
            if (!value.HasValue)
            {
                return;
            }

            result[key] += normalizedWeight * value.Value;
        }

        private static Dictionary<Symbol, decimal> BuildPriorWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors,
            bool useMarketCap)
        {
            var weights = new Dictionary<Symbol, decimal>();
            if (selected == null || selected.Count == 0)
            {
                return weights;
            }

            if (!useMarketCap)
            {
                foreach (var item in selected)
                {
                    weights[item.Key] = 1m;
                }
                return weights;
            }

            var totalMarketValue = selected
                .Select(item => factors.TryGetValue(item.Key, out var factor) ? factor.TotalMv.GetValueOrDefault() : 0m)
                .Where(value => value > 0m)
                .Sum();

            if (totalMarketValue <= 0m)
            {
                foreach (var item in selected)
                {
                    weights[item.Key] = 1m;
                }
                return weights;
            }

            foreach (var item in selected)
            {
                var marketValue = factors.TryGetValue(item.Key, out var factor)
                    ? factor.TotalMv.GetValueOrDefault()
                    : 0m;
                weights[item.Key] = marketValue > 0m ? marketValue / totalMarketValue : 0m;
            }

            return weights;
        }

        private static Dictionary<Symbol, decimal> BuildBlackLittermanKellyWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> factors,
            AShareBarraCNE5SignalSettings settings)
        {
            var priorWeights = BuildPriorWeights(selected, factors, useMarketCap: true);
            var viewScores = SafeZScores(selected.Select(item => item.Value).ToList());
            var posteriorReturns = new Dictionary<Symbol, decimal>();
            var rawKellyWeights = new Dictionary<Symbol, decimal>();
            var tau = Clamp(settings.BlackLittermanTau, 0.01m, 1.0m);
            var confidence = Clamp(settings.BlackLittermanViewConfidence, 0.05m, 0.95m);
            var omega = Math.Max(0.05m, 1m - confidence);
            var priorBlend = Clamp(settings.BlackLittermanPriorBlend, 0m, 1m);
            var kellyFraction = Clamp(settings.KellyWeightFraction, 0m, 1m);

            for (var index = 0; index < selected.Count; index++)
            {
                var item = selected[index];
                var symbol = item.Key;
                var priorWeight = priorWeights.TryGetValue(symbol, out var weight) ? weight : 0m;
                var priorReturn = settings.BlackLittermanRiskAversion * priorWeight;
                var viewReturn = settings.BlackLittermanViewScale * viewScores[index];
                var posteriorReturn =
                    (priorReturn / tau + viewReturn / omega) /
                    (1m / tau + 1m / omega);

                posteriorReturns[symbol] = posteriorReturn;

                var factor = factors.TryGetValue(symbol, out var resolvedFactor) ? resolvedFactor : null;
                var riskProxy = ComputeKellyRiskProxy(factor, settings);
                var kellyWeight = posteriorReturn > 0m && riskProxy > 0m
                    ? (posteriorReturn / riskProxy) * kellyFraction
                    : 0m;
                rawKellyWeights[symbol] = Math.Max(0m, kellyWeight);
            }

            var normalizedKellyWeights = NormalizePositiveWeights(rawKellyWeights);
            if (normalizedKellyWeights.Count == 0)
            {
                return priorWeights;
            }

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
            IReadOnlyDictionary<Symbol, decimal> rawWeights,
            decimal targetExposure,
            decimal maxSingleWeight)
        {
            var exposure = Math.Max(0m, targetExposure);
            if (rawWeights == null || rawWeights.Count == 0 || exposure <= 0m)
            {
                return new Dictionary<Symbol, decimal>();
            }

            var normalized = NormalizePositiveWeights(rawWeights);
            if (normalized.Count == 0)
            {
                return new Dictionary<Symbol, decimal>();
            }

            var cap = maxSingleWeight > 0m ? Math.Min(maxSingleWeight, exposure) : exposure;
            var result = normalized.ToDictionary(pair => pair.Key, _ => 0m);
            var remaining = normalized.ToDictionary(pair => pair.Key, pair => pair.Value);
            var remainingExposure = exposure;

            while (remaining.Count > 0 && remainingExposure > 0m)
            {
                var totalRemaining = remaining.Values.Sum();
                if (totalRemaining <= 0m)
                {
                    break;
                }

                var cappedAny = false;
                foreach (var pair in remaining.ToList())
                {
                    var proposed = remainingExposure * pair.Value / totalRemaining;
                    if (proposed <= cap)
                    {
                        continue;
                    }

                    result[pair.Key] = cap;
                    remainingExposure -= cap;
                    remaining.Remove(pair.Key);
                    cappedAny = true;
                }

                if (cappedAny)
                {
                    continue;
                }

                foreach (var pair in remaining)
                {
                    result[pair.Key] = remainingExposure * pair.Value / totalRemaining;
                }
                break;
            }

            return result
                .Where(pair => pair.Value > 0m)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
        }

        private static Dictionary<Symbol, decimal> NormalizePositiveWeights(IReadOnlyDictionary<Symbol, decimal> rawWeights)
        {
            var filtered = rawWeights
                .Where(pair => pair.Value > 0m)
                .ToList();
            if (filtered.Count == 0)
            {
                return new Dictionary<Symbol, decimal>();
            }

            var total = filtered.Sum(pair => pair.Value);
            if (total <= 0m)
            {
                return new Dictionary<Symbol, decimal>();
            }

            return filtered.ToDictionary(pair => pair.Key, pair => pair.Value / total);
        }

        private static decimal ComputeKellyRiskProxy(AShareBarraCNE5FactorData factor, AShareBarraCNE5SignalSettings settings)
        {
            var riskProxy = settings.KellyVarianceFloor;
            if (factor?.ResidualVolatility != null)
            {
                riskProxy += Math.Abs(factor.ResidualVolatility.Value) * settings.KellyResidualVolatilityScale;
            }
            if (factor?.Beta != null)
            {
                riskProxy += Math.Abs(factor.Beta.Value) * settings.KellyBetaPenaltyScale;
            }
            return Math.Max(settings.KellyVarianceFloor, riskProxy);
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue)
            {
                return minValue;
            }
            return value > maxValue ? maxValue : value;
        }
    }
}
