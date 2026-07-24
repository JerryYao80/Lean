using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Configurable thresholds and weights for IV signal computation.
    /// </summary>
    public class AShareImpliedVolatilitySignalSettings
    {
        public decimal SkewHighThreshold { get; set; } = 0.05m;
        public decimal SkewLowThreshold { get; set; } = -0.02m;
        public decimal IvtsHighThreshold { get; set; } = 1.2m;
        public decimal IvtsLowThreshold { get; set; } = 0.8m;
        public decimal IvLevelHighThreshold { get; set; } = 0.30m;
        public decimal IvLevelLowThreshold { get; set; } = 0.15m;
        public decimal SkewWeight { get; set; } = 0.35m;
        public decimal IvtsWeight { get; set; } = 0.35m;
        public decimal IvLevelWeight { get; set; } = 0.30m;
        public int MinOptionCount { get; set; } = 4;
    }

    /// <summary>
    /// IV-driven signal model computing skew, IV term structure, and IV level signals.
    /// Outputs a regime classification (fear/neutral/greed) and allocation weights.
    /// </summary>
    public static class AShareImpliedVolatilitySignalModel
    {
        public static (decimal skew, decimal ivts, decimal ivLevel) ComputeSignals(
            Dictionary<string, AShareImpliedVolatilityData> ivData,
            AShareImpliedVolatilitySignalSettings settings)
        {
            var skewValues = new List<decimal>();
            var ivtsValues = new List<decimal>();
            var ivLevelValues = new List<decimal>();

            foreach (var kvp in ivData)
            {
                var snap = kvp.Value;
                if (snap == null) continue;
                if (snap.OptionCount.HasValue && snap.OptionCount.Value < settings.MinOptionCount)
                    continue;

                if (snap.Skew.HasValue)
                    skewValues.Add(snap.Skew.Value);

                if (snap.IvTermStructureRatio.HasValue)
                    ivtsValues.Add(snap.IvTermStructureRatio.Value);

                if (snap.AtmIv.HasValue)
                    ivLevelValues.Add(snap.AtmIv.Value);
            }

            var skew = skewValues.Count > 0 ? skewValues.Average() : 0m;
            var ivts = ivtsValues.Count > 0 ? ivtsValues.Average() : 1.0m;
            var ivLevel = ivLevelValues.Count > 0 ? ivLevelValues.Average() : 0.20m;

            return (skew, ivts, ivLevel);
        }

        public static string DetermineRegime(
            decimal skew,
            decimal ivts,
            decimal ivLevel,
            AShareImpliedVolatilitySignalSettings settings)
        {
            var skewScore = NormalizeSignal(skew, settings.SkewLowThreshold, settings.SkewHighThreshold);
            var ivtsScore = NormalizeSignal(ivts, settings.IvtsLowThreshold, settings.IvtsHighThreshold);
            var ivLevelScore = NormalizeSignal(ivLevel, settings.IvLevelLowThreshold, settings.IvLevelHighThreshold);

            var composite = skewScore * settings.SkewWeight
                          + ivtsScore * settings.IvtsWeight
                          + ivLevelScore * settings.IvLevelWeight;

            if (composite > 0.6m) return "fear";
            if (composite < 0.4m) return "greed";
            return "neutral";
        }

        public static (decimal equityWeight, decimal safeWeight) ComputeAllocation(
            string regime,
            decimal equityWeightFear,
            decimal equityWeightGreed,
            decimal safeWeightFear,
            decimal safeWeightGreed)
        {
            return regime switch
            {
                "fear" => (equityWeightFear, safeWeightFear),
                "greed" => (equityWeightGreed, safeWeightGreed),
                _ => ((equityWeightFear + equityWeightGreed) / 2m, (safeWeightFear + safeWeightGreed) / 2m),
            };
        }

        /// <summary>
        /// Map a raw signal value to [0, 1] where 0 = low end, 1 = high end.
        /// Clamped to [0, 1].
        /// </summary>
        public static decimal NormalizeSignal(decimal value, decimal lowThreshold, decimal highThreshold)
        {
            if (highThreshold == lowThreshold) return 0.5m;
            var normalized = (value - lowThreshold) / (highThreshold - lowThreshold);
            return Math.Max(0m, Math.Min(1m, normalized));
        }
    }
}
