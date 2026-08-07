/*
 * AlphaWeightedMVOPortfolioConstructionModel.cs — MVO Portfolio Construction (Simplified)
 *
 * Replaces equal-weight portfolio with alpha-weighted portfolio.
 * Uses alpha scores directly as weights (simplified MVO).
 */
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Logging;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Portfolio
{
    /// <summary>
    /// Alpha-weighted portfolio construction model.
    /// Uses alpha scores as weights with concentration limits.
    /// </summary>
    public class AlphaWeightedMVOPortfolioConstructionModel : PortfolioConstructionModel
    {
        private readonly decimal _maxWeight;
        private readonly decimal _minWeight;
        private readonly Resolution _rebalanceResolution;

        private DateTime? _lastRebalanceTime;

        public AlphaWeightedMVOPortfolioConstructionModel(
            decimal maxWeight = 0.10m,
            decimal minWeight = 0.0m,
            Resolution rebalanceResolution = Resolution.Daily)
        {
            _maxWeight = maxWeight;
            _minWeight = minWeight;
            _rebalanceResolution = rebalanceResolution;
        }

        public override List<PortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
        {
            var targets = new List<PortfolioTarget>();

            if (!ShouldRebalance(algorithm.Time))
                return targets;

            if (insights.Length == 0)
                return targets;

            // Extract alpha scores from insights
            var symbolScores = new Dictionary<Symbol, double>();
            foreach (var insight in insights)
            {
                symbolScores[insight.Symbol] = insight.Magnitude.HasValue ? (double)insight.Magnitude.Value : 1.0;
            }

            // Normalize scores to sum to 1.0
            var totalScore = symbolScores.Values.Sum();
            if (totalScore <= 0)
            {
                // Fall back to equal weighting
                var equalWeight = 1.0m / insights.Length;
                foreach (var insight in insights)
                {
                    var t = PortfolioTarget.Percent(algorithm, insight.Symbol, equalWeight);
                    if (t != null) targets.Add((PortfolioTarget)t);
                }
                return targets;
            }

            // Compute weights with max concentration limit
            var weights = new Dictionary<Symbol, decimal>();
            var remainingWeight = 1.0m;
            var remainingSymbols = new List<Symbol>();

            // First pass: apply max weight limit
            foreach (var (symbol, score) in symbolScores.OrderByDescending(x => x.Value))
            {
                var rawWeight = (decimal)(score / totalScore);
                var weight = Math.Min(rawWeight, _maxWeight);
                weights[symbol] = weight;
                remainingWeight -= weight;
            }

            // Second pass: redistribute excess weight
            if (remainingWeight > 0)
            {
                var underweightSymbols = weights.Where(x => x.Value < _maxWeight).Select(x => x.Key).ToList();
                if (underweightSymbols.Count > 0)
                {
                    var redistribution = remainingWeight / underweightSymbols.Count;
                    foreach (var symbol in underweightSymbols)
                    {
                        weights[symbol] = Math.Min(weights[symbol] + redistribution, _maxWeight);
                    }
                }
            }

            // Create targets
            foreach (var insight in insights)
            {
                if (weights.ContainsKey(insight.Symbol))
                {
                    var weight = weights[insight.Symbol];
                    if (weight >= _minWeight)
                    {
                        var t = PortfolioTarget.Percent(algorithm, insight.Symbol, weight);
                        if (t != null) targets.Add((PortfolioTarget)t);
                    }
                }
            }

            _lastRebalanceTime = algorithm.Time;
            return targets;
        }

        private bool ShouldRebalance(DateTime now)
        {
            if (_lastRebalanceTime == null) return true;
            return now.Date != _lastRebalanceTime.Value.Date;
        }
    }
}
