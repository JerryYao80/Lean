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
        public string WeightingMode { get; set; } = "equal";
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
            string weightingMode)
        {
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
            var mode = (weightingMode ?? "equal").Trim().ToLowerInvariant();
            var totalMv = selected
                .Select(pair => factors.TryGetValue(pair.Key, out var factor) ? factor.TotalMv.GetValueOrDefault() : 0m)
                .Where(value => value > 0m)
                .Sum();

            var targets = new List<AShareBarraCNE5Target>(selected.Count);
            foreach (var item in selected)
            {
                decimal weight;
                if (mode == "market-cap" && totalMv > 0m && factors.TryGetValue(item.Key, out var factor) && factor.TotalMv.GetValueOrDefault() > 0m)
                {
                    weight = targetExposure * factor.TotalMv.Value / totalMv;
                }
                else
                {
                    weight = targetExposure / selected.Count;
                }

                targets.Add(new AShareBarraCNE5Target
                {
                    Symbol = item.Key,
                    Weight = weight,
                    Score = item.Value
                });
            }

            return targets;
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
    }
}
