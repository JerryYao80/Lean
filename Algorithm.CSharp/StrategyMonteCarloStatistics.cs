using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Reusable Monte Carlo helper for LEAN algorithms that want summary statistics
    /// based on historical strategy returns and optional factor exposure perturbations.
    /// </summary>
    public static class StrategyMonteCarloStatistics
    {
        public static StrategyMonteCarloSummary Compute(
            StrategyMonteCarloConfig config,
            IEnumerable<StrategyMonteCarloDailyReturn> dailyReturns,
            IEnumerable<StrategyMonteCarloFactorExposure> factorExposures = null)
        {
            if (config == null || !config.Enabled)
            {
                return StrategyMonteCarloSummary.Empty;
            }

            var orderedReturns = (dailyReturns ?? Enumerable.Empty<StrategyMonteCarloDailyReturn>())
                .Where(row => row != null && !double.IsNaN(row.NetReturn) && !double.IsInfinity(row.NetReturn))
                .OrderBy(row => row.TradeDate)
                .ToList();
            if (orderedReturns.Count == 0)
            {
                return StrategyMonteCarloSummary.Empty;
            }

            var trials = Math.Max(1, config.Trials);
            var horizonDays = Math.Max(1, config.HorizonDays > 0 ? config.HorizonDays : orderedReturns.Count);
            var blockSize = Math.Max(1, config.BlockSize);
            var baselinePaths = BlockBootstrapReturns(
                orderedReturns.Select(row => row.NetReturn).ToArray(),
                trials,
                horizonDays,
                blockSize,
                config.Seed);
            if (baselinePaths.Count == 0)
            {
                return StrategyMonteCarloSummary.Empty;
            }

            var factorPaths = FactorPerturbationReturns(
                orderedReturns,
                factorExposures,
                trials,
                horizonDays,
                config.Seed + 1,
                config.FactorPerturbationScale);
            var combinedPaths = CombinePaths(baselinePaths, factorPaths);

            var baselineSummary = SummarizeTerminalPaths(baselinePaths);
            var combinedSummary = SummarizeTerminalPaths(combinedPaths);
            return new StrategyMonteCarloSummary
            {
                HasData = true,
                Trials = trials,
                HorizonDays = horizonDays,
                BaselineLossProbability = baselineSummary.LossProbability,
                CombinedLossProbability = combinedSummary.LossProbability,
                CombinedMedianReturn = combinedSummary.MedianReturn,
                CombinedP95Drawdown = combinedSummary.P95Drawdown
            };
        }

        private static List<double[]> BlockBootstrapReturns(
            IReadOnlyList<double> returns,
            int trials,
            int horizonDays,
            int blockSize,
            int seed)
        {
            if (returns == null || returns.Count == 0)
            {
                return new List<double[]>();
            }

            var random = new Random(seed);
            var paths = new List<double[]>(trials);
            for (var trialIndex = 0; trialIndex < trials; trialIndex++)
            {
                var path = new double[horizonDays];
                var position = 0;
                while (position < horizonDays)
                {
                    var startIndex = random.Next(returns.Count);
                    for (var offset = 0; offset < blockSize && position < horizonDays; offset++)
                    {
                        path[position++] = returns[(startIndex + offset) % returns.Count];
                    }
                }
                paths.Add(path);
            }

            return paths;
        }

        private static List<double[]> FactorPerturbationReturns(
            IReadOnlyList<StrategyMonteCarloDailyReturn> dailyReturns,
            IEnumerable<StrategyMonteCarloFactorExposure> factorExposures,
            int trials,
            int horizonDays,
            int seed,
            double perturbationScale)
        {
            var exposureByDate = (factorExposures ?? Enumerable.Empty<StrategyMonteCarloFactorExposure>())
                .Where(row => row != null)
                .GroupBy(row => row.TradeDate.Date)
                .ToDictionary(group => group.Key, group => group.Last());
            if (exposureByDate.Count == 0)
            {
                return BlockBootstrapReturns(
                    dailyReturns.Select(row => row.NetReturn).ToArray(),
                    trials,
                    horizonDays,
                    1,
                    seed);
            }

            var merged = new List<MergedObservation>();
            foreach (var row in dailyReturns)
            {
                if (!exposureByDate.TryGetValue(row.TradeDate.Date, out var exposure))
                {
                    exposure = new StrategyMonteCarloFactorExposure { TradeDate = row.TradeDate };
                }

                merged.Add(new MergedObservation
                {
                    NetReturn = row.NetReturn,
                    Factors = exposure.ToVector()
                });
            }

            if (merged.Count == 0)
            {
                return new List<double[]>();
            }

            var factorCount = StrategyMonteCarloFactorExposure.FactorCount;
            var standardized = StandardizeFactors(merged.Select(row => row.Factors).ToList(), factorCount);
            var returns = merged.Select(row => row.NetReturn).ToArray();
            var beta = SolveLeastSquares(standardized, returns);
            var fitted = new double[merged.Count];
            var residuals = new double[merged.Count];
            for (var rowIndex = 0; rowIndex < merged.Count; rowIndex++)
            {
                var estimate = Dot(standardized[rowIndex], beta);
                fitted[rowIndex] = estimate;
                residuals[rowIndex] = returns[rowIndex] - estimate;
            }

            var returnStd = StandardDeviation(returns);
            var baseSigma = factorCount > 0 ? returnStd / factorCount : returnStd;
            var factorSigma = new double[factorCount];
            for (var factorIndex = 0; factorIndex < factorCount; factorIndex++)
            {
                factorSigma[factorIndex] = Math.Max(Math.Abs(beta[factorIndex]), baseSigma) * Math.Max(0d, perturbationScale);
            }

            var random = new Random(seed);
            var paths = new List<double[]>(trials);
            for (var trialIndex = 0; trialIndex < trials; trialIndex++)
            {
                var path = new double[horizonDays];
                for (var horizonIndex = 0; horizonIndex < horizonDays; horizonIndex++)
                {
                    var sampleIndex = random.Next(merged.Count);
                    var perturbation = 0d;
                    for (var factorIndex = 0; factorIndex < factorCount; factorIndex++)
                    {
                        perturbation += standardized[sampleIndex][factorIndex] * NextGaussian(random, factorSigma[factorIndex]);
                    }

                    path[horizonIndex] = fitted[sampleIndex] + residuals[sampleIndex] + perturbation;
                }
                paths.Add(path);
            }

            return paths;
        }

        private static List<double[]> CombinePaths(IReadOnlyList<double[]> primary, IReadOnlyList<double[]> secondary)
        {
            if (primary == null || primary.Count == 0)
            {
                return secondary?.ToList() ?? new List<double[]>();
            }

            if (secondary == null || secondary.Count == 0)
            {
                return primary.ToList();
            }

            var count = Math.Min(primary.Count, secondary.Count);
            var combined = new List<double[]>(count);
            for (var pathIndex = 0; pathIndex < count; pathIndex++)
            {
                var left = primary[pathIndex];
                var right = secondary[pathIndex];
                var length = Math.Min(left.Length, right.Length);
                var merged = new double[length];
                for (var valueIndex = 0; valueIndex < length; valueIndex++)
                {
                    merged[valueIndex] = (left[valueIndex] + right[valueIndex]) / 2d;
                }
                combined.Add(merged);
            }
            return combined;
        }

        private static TerminalPathSummary SummarizeTerminalPaths(IEnumerable<double[]> paths)
        {
            var terminalReturns = new List<double>();
            var maxDrawdowns = new List<double>();
            foreach (var path in paths ?? Enumerable.Empty<double[]>())
            {
                if (path == null || path.Length == 0)
                {
                    continue;
                }

                var equity = 1d;
                var peak = 1d;
                var maxDrawdown = 0d;
                foreach (var netReturn in path)
                {
                    equity *= 1d + netReturn;
                    if (equity > peak)
                    {
                        peak = equity;
                    }

                    var drawdown = peak == 0d ? 0d : equity / peak - 1d;
                    if (drawdown < maxDrawdown)
                    {
                        maxDrawdown = drawdown;
                    }
                }

                terminalReturns.Add(equity - 1d);
                maxDrawdowns.Add(maxDrawdown);
            }

            if (terminalReturns.Count == 0)
            {
                return TerminalPathSummary.Empty;
            }

            return new TerminalPathSummary
            {
                LossProbability = terminalReturns.Count(value => value < 0d) / (double)terminalReturns.Count,
                MedianReturn = Quantile(terminalReturns, 0.5d),
                P95Drawdown = Quantile(maxDrawdowns, 0.95d)
            };
        }

        private static List<double[]> StandardizeFactors(IReadOnlyList<double[]> rows, int factorCount)
        {
            var means = new double[factorCount];
            var stds = new double[factorCount];
            for (var factorIndex = 0; factorIndex < factorCount; factorIndex++)
            {
                var column = new double[rows.Count];
                for (var rowIndex = 0; rowIndex < rows.Count; rowIndex++)
                {
                    column[rowIndex] = rows[rowIndex][factorIndex];
                    means[factorIndex] += column[rowIndex];
                }

                means[factorIndex] /= Math.Max(1, rows.Count);
                for (var rowIndex = 0; rowIndex < column.Length; rowIndex++)
                {
                    var delta = column[rowIndex] - means[factorIndex];
                    stds[factorIndex] += delta * delta;
                }

                stds[factorIndex] = Math.Sqrt(stds[factorIndex] / Math.Max(1, rows.Count));
                if (stds[factorIndex] <= 1e-12)
                {
                    stds[factorIndex] = 1d;
                }
            }

            var standardized = new List<double[]>(rows.Count);
            for (var rowIndex = 0; rowIndex < rows.Count; rowIndex++)
            {
                var row = new double[factorCount];
                for (var factorIndex = 0; factorIndex < factorCount; factorIndex++)
                {
                    row[factorIndex] = (rows[rowIndex][factorIndex] - means[factorIndex]) / stds[factorIndex];
                }
                standardized.Add(row);
            }

            return standardized;
        }

        private static double[] SolveLeastSquares(IReadOnlyList<double[]> x, IReadOnlyList<double> y)
        {
            if (x == null || y == null || x.Count == 0 || y.Count == 0 || x.Count != y.Count)
            {
                return new double[StrategyMonteCarloFactorExposure.FactorCount];
            }

            var factorCount = x[0].Length;
            var xtx = new double[factorCount, factorCount];
            var xty = new double[factorCount];
            for (var rowIndex = 0; rowIndex < x.Count; rowIndex++)
            {
                var row = x[rowIndex];
                for (var left = 0; left < factorCount; left++)
                {
                    xty[left] += row[left] * y[rowIndex];
                    for (var right = 0; right < factorCount; right++)
                    {
                        xtx[left, right] += row[left] * row[right];
                    }
                }
            }

            for (var index = 0; index < factorCount; index++)
            {
                xtx[index, index] += 1e-9d;
            }

            return SolveLinearSystem(xtx, xty);
        }

        private static double[] SolveLinearSystem(double[,] matrix, double[] rhs)
        {
            var size = rhs.Length;
            var augmented = new double[size, size + 1];
            for (var row = 0; row < size; row++)
            {
                for (var column = 0; column < size; column++)
                {
                    augmented[row, column] = matrix[row, column];
                }

                augmented[row, size] = rhs[row];
            }

            for (var pivotColumn = 0; pivotColumn < size; pivotColumn++)
            {
                var pivotRow = pivotColumn;
                var pivotAbs = Math.Abs(augmented[pivotRow, pivotColumn]);
                for (var row = pivotColumn + 1; row < size; row++)
                {
                    var candidate = Math.Abs(augmented[row, pivotColumn]);
                    if (candidate > pivotAbs)
                    {
                        pivotAbs = candidate;
                        pivotRow = row;
                    }
                }

                if (pivotAbs <= 1e-12d)
                {
                    return new double[size];
                }

                if (pivotRow != pivotColumn)
                {
                    for (var column = pivotColumn; column <= size; column++)
                    {
                        var temp = augmented[pivotColumn, column];
                        augmented[pivotColumn, column] = augmented[pivotRow, column];
                        augmented[pivotRow, column] = temp;
                    }
                }

                var pivot = augmented[pivotColumn, pivotColumn];
                for (var column = pivotColumn; column <= size; column++)
                {
                    augmented[pivotColumn, column] /= pivot;
                }

                for (var row = 0; row < size; row++)
                {
                    if (row == pivotColumn)
                    {
                        continue;
                    }

                    var scale = augmented[row, pivotColumn];
                    if (Math.Abs(scale) <= 1e-12d)
                    {
                        continue;
                    }

                    for (var column = pivotColumn; column <= size; column++)
                    {
                        augmented[row, column] -= scale * augmented[pivotColumn, column];
                    }
                }
            }

            var solution = new double[size];
            for (var row = 0; row < size; row++)
            {
                solution[row] = augmented[row, size];
            }

            return solution;
        }

        private static double Dot(IReadOnlyList<double> left, IReadOnlyList<double> right)
        {
            var value = 0d;
            for (var index = 0; index < Math.Min(left.Count, right.Count); index++)
            {
                value += left[index] * right[index];
            }

            return value;
        }

        private static double StandardDeviation(IReadOnlyList<double> values)
        {
            if (values == null || values.Count == 0)
            {
                return 0d;
            }

            var mean = values.Average();
            var variance = 0d;
            for (var index = 0; index < values.Count; index++)
            {
                var delta = values[index] - mean;
                variance += delta * delta;
            }

            return Math.Sqrt(variance / Math.Max(1, values.Count));
        }

        private static double Quantile(IReadOnlyList<double> values, double probability)
        {
            if (values == null || values.Count == 0)
            {
                return double.NaN;
            }

            var ordered = values.OrderBy(value => value).ToArray();
            if (ordered.Length == 1)
            {
                return ordered[0];
            }

            var clippedProbability = Math.Max(0d, Math.Min(1d, probability));
            var position = clippedProbability * (ordered.Length - 1);
            var lowerIndex = (int)Math.Floor(position);
            var upperIndex = (int)Math.Ceiling(position);
            if (lowerIndex == upperIndex)
            {
                return ordered[lowerIndex];
            }

            var weight = position - lowerIndex;
            return ordered[lowerIndex] + (ordered[upperIndex] - ordered[lowerIndex]) * weight;
        }

        private static double NextGaussian(Random random, double standardDeviation)
        {
            if (standardDeviation <= 0d)
            {
                return 0d;
            }

            var u1 = 1d - random.NextDouble();
            var u2 = 1d - random.NextDouble();
            var normal = Math.Sqrt(-2d * Math.Log(u1)) * Math.Cos(2d * Math.PI * u2);
            return normal * standardDeviation;
        }

        private sealed class MergedObservation
        {
            public double NetReturn { get; set; }
            public double[] Factors { get; set; }
        }

        private sealed class TerminalPathSummary
        {
            public static TerminalPathSummary Empty { get; } = new TerminalPathSummary();

            public double LossProbability { get; set; }
            public double MedianReturn { get; set; }
            public double P95Drawdown { get; set; }
        }
    }

    public sealed class StrategyMonteCarloConfig
    {
        public bool Enabled { get; set; }
        public int Trials { get; set; } = 500;
        public int HorizonDays { get; set; } = 63;
        public int BlockSize { get; set; } = 5;
        public int Seed { get; set; } = 42;
        public double FactorPerturbationScale { get; set; } = 0.15d;
    }

    public sealed class StrategyMonteCarloDailyReturn
    {
        public DateTime TradeDate { get; set; }
        public double NetReturn { get; set; }
    }

    public sealed class StrategyMonteCarloFactorExposure
    {
        public const int FactorCount = 10;

        public DateTime TradeDate { get; set; }
        public double Beta { get; set; }
        public double Momentum { get; set; }
        public double Size { get; set; }
        public double EarningsYield { get; set; }
        public double ResidualVolatility { get; set; }
        public double Growth { get; set; }
        public double BookToPrice { get; set; }
        public double Leverage { get; set; }
        public double Liquidity { get; set; }
        public double NonLinearSize { get; set; }

        public double[] ToVector()
        {
            return new[]
            {
                Beta,
                Momentum,
                Size,
                EarningsYield,
                ResidualVolatility,
                Growth,
                BookToPrice,
                Leverage,
                Liquidity,
                NonLinearSize
            };
        }
    }

    public sealed class StrategyMonteCarloSummary
    {
        public static StrategyMonteCarloSummary Empty { get; } = new StrategyMonteCarloSummary();

        public bool HasData { get; set; }
        public int Trials { get; set; }
        public int HorizonDays { get; set; }
        public double BaselineLossProbability { get; set; }
        public double CombinedLossProbability { get; set; }
        public double CombinedMedianReturn { get; set; }
        public double CombinedP95Drawdown { get; set; }

        public Dictionary<string, string> ToSummaryStatistics()
        {
            return new Dictionary<string, string>
            {
                ["Monte Carlo Trials"] = Trials.ToString(CultureInfo.InvariantCulture),
                ["Monte Carlo Horizon Days"] = HorizonDays.ToString(CultureInfo.InvariantCulture),
                ["Monte Carlo Baseline Loss Probability"] = BaselineLossProbability.ToString("P2", CultureInfo.InvariantCulture),
                ["Monte Carlo Combined Loss Probability"] = CombinedLossProbability.ToString("P2", CultureInfo.InvariantCulture),
                ["Monte Carlo Combined Median Return"] = CombinedMedianReturn.ToString("P2", CultureInfo.InvariantCulture),
                ["Monte Carlo Combined P95 Drawdown"] = CombinedP95Drawdown.ToString("P2", CultureInfo.InvariantCulture)
            };
        }
    }
}
