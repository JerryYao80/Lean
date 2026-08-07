using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    public sealed class AShareLlmQuantSignalSettings
    {
        public int TopN { get; set; } = 10;
        public int RetentionBuffer { get; set; } = 6;
        public decimal MinPrice { get; set; } = 5m;
        public decimal MinCircMv { get; set; } = 1000000m;
        public decimal MinTurnoverRateF { get; set; } = 0.3m;
        public decimal? MaxVolatility20 { get; set; } = 0.045m;
        public bool RequirePositiveFlow { get; set; } = true;
        public bool RequirePositiveMomentum { get; set; }
        public decimal MomentumWeight { get; set; } = 1.2m;
        public decimal ValuePbWeight { get; set; } = 0.6m;
        public decimal ValuePsWeight { get; set; } = 0.2m;
        public decimal DividendWeight { get; set; } = 0.2m;
        public decimal FlowWeight { get; set; } = 0.8m;
        public decimal BigFlowWeight { get; set; } = 0.6m;
        public decimal LowVolWeight { get; set; } = 1.0m;
        public decimal ShortReversalWeight { get; set; } = 0.2m;
    }

    public sealed class AShareLlmQuantTarget
    {
        public Symbol Symbol { get; set; }
        public decimal Weight { get; set; }
        public decimal Score { get; set; }
    }

    public static class AShareLlmQuantSignalModel
    {
        public static Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareLlmQuantFeatureData> features,
            AShareLlmQuantSignalSettings settings)
        {
            settings ??= new AShareLlmQuantSignalSettings();
            var eligible = features
                .Where(pair => IsEligible(pair.Value, settings))
                .ToList();

            var scores = eligible.ToDictionary(pair => pair.Key, _ => 0m);
            if (eligible.Count == 0)
            {
                return scores;
            }

            Accumulate(scores, eligible, settings.MomentumWeight, factor => factor.Momentum12020);
            Accumulate(scores, eligible, settings.ValuePbWeight, factor => TransformLogInverse(factor.Pb));
            Accumulate(scores, eligible, settings.ValuePsWeight, factor => TransformLogInverse(factor.PsTtm));
            Accumulate(scores, eligible, settings.DividendWeight, factor => factor.DvTtm);
            Accumulate(scores, eligible, settings.FlowWeight, factor => factor.FlowRatio);
            Accumulate(scores, eligible, settings.BigFlowWeight, factor => factor.BigFlowRatio);
            Accumulate(scores, eligible, settings.LowVolWeight, factor => Negate(factor.Volatility20));
            Accumulate(scores, eligible, settings.ShortReversalWeight, factor => Negate(factor.Return5));
            return scores;
        }

        public static List<AShareLlmQuantTarget> SelectPortfolio(
            IReadOnlyDictionary<Symbol, decimal> scores,
            AShareLlmQuantSignalSettings settings,
            decimal targetExposure,
            IReadOnlyCollection<Symbol> currentHoldings = null)
        {
            settings ??= new AShareLlmQuantSignalSettings();
            if (targetExposure <= 0m || scores == null || scores.Count == 0 || settings.TopN <= 0)
            {
                return new List<AShareLlmQuantTarget>();
            }

            var ordered = scores
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                .ToList();
            var selected = SelectSymbolsWithRetention(ordered, settings, currentHoldings);

            if (selected.Count == 0)
            {
                return new List<AShareLlmQuantTarget>();
            }

            var weight = targetExposure / selected.Count;
            return selected
                .Select(pair => new AShareLlmQuantTarget
                {
                    Symbol = pair.Key,
                    Weight = weight,
                    Score = pair.Value
                })
                .ToList();
        }

        private static List<KeyValuePair<Symbol, decimal>> SelectSymbolsWithRetention(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> ordered,
            AShareLlmQuantSignalSettings settings,
            IReadOnlyCollection<Symbol> currentHoldings)
        {
            var capacity = Math.Min(settings.TopN, ordered.Count);
            if (capacity <= 0)
            {
                return new List<KeyValuePair<Symbol, decimal>>();
            }

            var holdingSet = currentHoldings == null
                ? new HashSet<Symbol>()
                : new HashSet<Symbol>(currentHoldings);
            var selected = new List<KeyValuePair<Symbol, decimal>>(capacity);
            var used = new HashSet<Symbol>();

            if (holdingSet.Count > 0 && settings.RetentionBuffer > 0)
            {
                var cutoff = Math.Min(ordered.Count, capacity + settings.RetentionBuffer);
                foreach (var pair in ordered.Take(cutoff))
                {
                    if (!holdingSet.Contains(pair.Key) || !used.Add(pair.Key))
                    {
                        continue;
                    }

                    selected.Add(pair);
                    if (selected.Count >= capacity)
                    {
                        return selected;
                    }
                }
            }

            foreach (var pair in ordered)
            {
                if (!used.Add(pair.Key))
                {
                    continue;
                }

                selected.Add(pair);
                if (selected.Count >= capacity)
                {
                    break;
                }
            }

            return selected;
        }

        private static bool IsEligible(AShareLlmQuantFeatureData feature, AShareLlmQuantSignalSettings settings)
        {
            if (feature == null || !feature.InUniverse)
            {
                return false;
            }

            if (!feature.Close.HasValue || feature.Close.Value < settings.MinPrice)
            {
                return false;
            }

            if (!feature.CircMv.HasValue || feature.CircMv.Value < settings.MinCircMv)
            {
                return false;
            }

            if (!feature.TurnoverRateF.HasValue || feature.TurnoverRateF.Value < settings.MinTurnoverRateF)
            {
                return false;
            }

            if (settings.RequirePositiveFlow && (!feature.FlowRatio.HasValue || feature.FlowRatio.Value <= 0m))
            {
                return false;
            }

            if (settings.RequirePositiveMomentum && (!feature.Momentum12020.HasValue || feature.Momentum12020.Value <= 0m))
            {
                return false;
            }

            if (settings.MaxVolatility20.HasValue && (!feature.Volatility20.HasValue || feature.Volatility20.Value > settings.MaxVolatility20.Value))
            {
                return false;
            }

            return true;
        }

        private static void Accumulate(
            IDictionary<Symbol, decimal> scores,
            IReadOnlyList<KeyValuePair<Symbol, AShareLlmQuantFeatureData>> eligible,
            decimal weight,
            Func<AShareLlmQuantFeatureData, decimal?> selector)
        {
            if (weight == 0m)
            {
                return;
            }

            var indices = new List<int>();
            var values = new List<decimal>();
            for (var index = 0; index < eligible.Count; index++)
            {
                var value = selector(eligible[index].Value);
                if (!value.HasValue)
                {
                    continue;
                }

                indices.Add(index);
                values.Add(value.Value);
            }

            if (values.Count <= 1)
            {
                return;
            }

            var zScores = SafeZScores(values);
            for (var index = 0; index < indices.Count; index++)
            {
                var symbol = eligible[indices[index]].Key;
                scores[symbol] += zScores[index] * weight;
            }
        }

        private static decimal? TransformLogInverse(decimal? value)
        {
            if (!value.HasValue || value.Value <= 0m)
            {
                return null;
            }

            return -(decimal)Math.Log((double)Math.Max(value.Value, 0.05m));
        }

        private static decimal? Negate(decimal? value)
        {
            return value.HasValue ? -value.Value : null;
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
    }
}
