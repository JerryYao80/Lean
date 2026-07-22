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
using System.Linq;
using QuantConnect.Configuration;

namespace QuantConnect.Lean.Engine.Results
{
    internal sealed class BacktestMonteCarloSettings
    {
        private const int DefaultTrialCount = 500;
        private const int DefaultHorizonDays = 63;
        private const int DefaultBlockSize = 5;
        private const double DefaultExtraFeeRate = 0.0004;
        private const double DefaultShockProbability = 0.04;
        private const double DefaultShockMean = 0.012;
        private const double DefaultShockStandardDeviation = 0.006;
        private const int DefaultSeed = 42;

        public bool Enabled { get; set; }
        public int TrialCount { get; set; } = DefaultTrialCount;
        public int HorizonDays { get; set; } = DefaultHorizonDays;
        public int BlockSize { get; set; } = DefaultBlockSize;
        public double ExtraFeeRate { get; set; } = DefaultExtraFeeRate;
        public double ShockProbability { get; set; } = DefaultShockProbability;
        public double ShockMean { get; set; } = DefaultShockMean;
        public double ShockStandardDeviation { get; set; } = DefaultShockStandardDeviation;
        public int Seed { get; set; } = DefaultSeed;

        public static BacktestMonteCarloSettings From(IDictionary<string, string> parameters)
        {
            return new BacktestMonteCarloSettings
            {
                Enabled = ReadBool(parameters, "monte-carlo-enabled", "results-monte-carlo-enabled", false),
                TrialCount = Math.Max(1, ReadInt(parameters, "monte-carlo-trials", "results-monte-carlo-trials", DefaultTrialCount)),
                HorizonDays = Math.Max(1, ReadInt(parameters, "monte-carlo-horizon-days", "results-monte-carlo-horizon-days", DefaultHorizonDays)),
                BlockSize = Math.Max(1, ReadInt(parameters, "monte-carlo-block-size", "results-monte-carlo-block-size", DefaultBlockSize)),
                ExtraFeeRate = Math.Max(0, ReadDouble(parameters, "monte-carlo-extra-fee-rate", "results-monte-carlo-extra-fee-rate", DefaultExtraFeeRate)),
                ShockProbability = Math.Clamp(ReadDouble(parameters, "monte-carlo-shock-probability", "results-monte-carlo-shock-probability", DefaultShockProbability), 0, 1),
                ShockMean = Math.Max(0, ReadDouble(parameters, "monte-carlo-shock-mean", "results-monte-carlo-shock-mean", DefaultShockMean)),
                ShockStandardDeviation = Math.Max(0, ReadDouble(parameters, "monte-carlo-shock-std", "results-monte-carlo-shock-std", DefaultShockStandardDeviation)),
                Seed = ReadInt(parameters, "monte-carlo-seed", "results-monte-carlo-seed", DefaultSeed)
            };
        }

        private static bool ReadBool(IDictionary<string, string> parameters, string parameterKey, string configKey, bool defaultValue)
        {
            if (parameters != null && parameters.TryGetValue(parameterKey, out var parameterValue) &&
                bool.TryParse(parameterValue, out var parsed))
            {
                return parsed;
            }

            return Config.GetBool(configKey, defaultValue);
        }

        private static int ReadInt(IDictionary<string, string> parameters, string parameterKey, string configKey, int defaultValue)
        {
            if (parameters != null && parameters.TryGetValue(parameterKey, out var parameterValue) &&
                int.TryParse(parameterValue, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed))
            {
                return parsed;
            }

            return Config.GetInt(configKey, defaultValue);
        }

        private static double ReadDouble(IDictionary<string, string> parameters, string parameterKey, string configKey, double defaultValue)
        {
            if (parameters != null && parameters.TryGetValue(parameterKey, out var parameterValue) &&
                double.TryParse(parameterValue, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed))
            {
                return parsed;
            }

            return Config.GetDouble(configKey, defaultValue);
        }
    }

    internal static class BacktestMonteCarloAnalysis
    {
        public const string TrialsSummaryKey = "Monte Carlo Trials";
        public const string HorizonSummaryKey = "Monte Carlo Horizon Days";
        public const string BaselineLossProbabilitySummaryKey = "Monte Carlo Baseline Loss Probability";
        public const string CombinedLossProbabilitySummaryKey = "Monte Carlo Combined Loss Probability";
        public const string CombinedMedianReturnSummaryKey = "Monte Carlo Combined Median Return";
        public const string CombinedP95DrawdownSummaryKey = "Monte Carlo Combined P95 Drawdown";
        public const string ReportStateKey = "MonteCarloReport";
        public const string EnabledStateKey = "MonteCarloEnabled";

        public static IReadOnlyList<double> ExtractDailyReturns(IReadOnlyDictionary<string, Chart> charts)
        {
            if (charts == null ||
                !charts.TryGetValue(BaseResultsHandler.StrategyEquityKey, out var chart) ||
                !chart.Series.TryGetValue(BaseResultsHandler.ReturnKey, out var series))
            {
                return Array.Empty<double>();
            }

            var dailyReturns = new SortedDictionary<DateTime, double>();
            foreach (var point in series.Values)
            {
                if (point is ChartPoint chartPoint && chartPoint.Y.HasValue)
                {
                    dailyReturns[chartPoint.Time.Date] = (double)chartPoint.Y.Value / 100d;
                }
            }

            return dailyReturns.Values.ToList();
        }

        public static bool TryCreateReport(IReadOnlyDictionary<string, Chart> charts, BacktestMonteCarloSettings settings, out BacktestMonteCarloReport report)
        {
            report = null;
            if (settings == null || !settings.Enabled)
            {
                return false;
            }

            var returns = ExtractDailyReturns(charts);
            if (returns.Count == 0)
            {
                return false;
            }

            report = CreateReport(returns, settings);
            return true;
        }

        public static BacktestMonteCarloReport CreateReport(IReadOnlyList<double> sourceReturns, BacktestMonteCarloSettings settings)
        {
            var horizonDays = Math.Max(1, settings.HorizonDays > 0 ? settings.HorizonDays : sourceReturns.Count);
            var baselinePaths = BlockBootstrapReturns(sourceReturns, settings.TrialCount, horizonDays, settings.BlockSize, settings.Seed);
            var feeStressPaths = ApplyFeeStress(baselinePaths, settings.ExtraFeeRate);
            var shockStressPaths = ApplyShockStress(baselinePaths, settings.ShockProbability, settings.ShockMean, settings.ShockStandardDeviation, settings.Seed + 1);
            var combinedStressPaths = ApplyShockStress(feeStressPaths, settings.ShockProbability, settings.ShockMean, settings.ShockStandardDeviation, settings.Seed + 2);

            return new BacktestMonteCarloReport
            {
                GeneratedAtUtc = DateTime.UtcNow,
                SourceTradeDays = sourceReturns.Count,
                TrialCount = settings.TrialCount,
                HorizonDays = horizonDays,
                BlockSize = settings.BlockSize,
                Scenarios = new Dictionary<string, BacktestMonteCarloScenarioReport>
                {
                    ["baseline"] = Summarize(baselinePaths),
                    ["feeStress"] = Summarize(feeStressPaths),
                    ["shockStress"] = Summarize(shockStressPaths),
                    ["combinedStress"] = Summarize(combinedStressPaths)
                }
            };
        }

        public static List<List<double>> BlockBootstrapReturns(IReadOnlyList<double> returns, int trialCount, int horizonDays, int blockSize, int seed)
        {
            var scenarios = new List<List<double>>();
            if (returns == null || returns.Count == 0 || trialCount <= 0 || horizonDays <= 0 || blockSize <= 0)
            {
                return scenarios;
            }

            var random = new Random(seed);
            for (var trial = 0; trial < trialCount; trial++)
            {
                var path = new List<double>(horizonDays);
                while (path.Count < horizonDays)
                {
                    var start = random.Next(returns.Count);
                    for (var offset = 0; offset < blockSize && path.Count < horizonDays; offset++)
                    {
                        path.Add(returns[(start + offset) % returns.Count]);
                    }
                }
                scenarios.Add(path);
            }

            return scenarios;
        }

        public static List<List<double>> ApplyFeeStress(IReadOnlyList<List<double>> paths, double extraFeeRate)
        {
            return paths.Select(path => path.Select(value => value - extraFeeRate).ToList()).ToList();
        }

        public static List<List<double>> ApplyShockStress(IReadOnlyList<List<double>> paths, double shockProbability, double shockMean, double shockStandardDeviation, int seed)
        {
            var random = new Random(seed);
            var stressedPaths = new List<List<double>>(paths.Count);

            foreach (var path in paths)
            {
                var stressedPath = new List<double>(path.Count);
                foreach (var value in path)
                {
                    var stressed = value;
                    if (shockProbability > 0 && random.NextDouble() < shockProbability)
                    {
                        var magnitude = Math.Abs(NextGaussian(random, shockMean, shockStandardDeviation));
                        stressed = Math.Max(-0.95, value - magnitude);
                    }
                    stressedPath.Add(stressed);
                }
                stressedPaths.Add(stressedPath);
            }

            return stressedPaths;
        }

        public static IReadOnlyDictionary<string, string> CreateSummaryStatistics(BacktestMonteCarloReport report)
        {
            var baseline = report.Scenarios["baseline"];
            var combined = report.Scenarios["combinedStress"];
            return new Dictionary<string, string>
            {
                [TrialsSummaryKey] = report.TrialCount.ToString(CultureInfo.InvariantCulture),
                [HorizonSummaryKey] = report.HorizonDays.ToString(CultureInfo.InvariantCulture),
                [BaselineLossProbabilitySummaryKey] = ToPercent(baseline.LossProbability),
                [CombinedLossProbabilitySummaryKey] = ToPercent(combined.LossProbability),
                [CombinedMedianReturnSummaryKey] = ToPercent(combined.MedianTotalReturn),
                [CombinedP95DrawdownSummaryKey] = ToPercent(combined.P95MaxDrawdown)
            };
        }

        private static BacktestMonteCarloScenarioReport Summarize(IReadOnlyList<List<double>> paths)
        {
            if (paths == null || paths.Count == 0)
            {
                return new BacktestMonteCarloScenarioReport();
            }

            var metrics = paths.Select(ComputePathMetrics).ToList();
            return new BacktestMonteCarloScenarioReport
            {
                TrialCount = paths.Count,
                HorizonDays = paths[0].Count,
                MeanFinalEquity = metrics.Average(x => x.FinalEquity),
                MedianFinalEquity = Percentile(metrics.Select(x => x.FinalEquity), 0.5),
                P05FinalEquity = Percentile(metrics.Select(x => x.FinalEquity), 0.05),
                P95FinalEquity = Percentile(metrics.Select(x => x.FinalEquity), 0.95),
                MeanTotalReturn = metrics.Average(x => x.TotalReturn),
                P05TotalReturn = Percentile(metrics.Select(x => x.TotalReturn), 0.05),
                MedianTotalReturn = Percentile(metrics.Select(x => x.TotalReturn), 0.5),
                P95TotalReturn = Percentile(metrics.Select(x => x.TotalReturn), 0.95),
                MeanMaxDrawdown = metrics.Average(x => x.MaxDrawdown),
                MedianMaxDrawdown = Percentile(metrics.Select(x => x.MaxDrawdown), 0.5),
                P95MaxDrawdown = Percentile(metrics.Select(x => x.MaxDrawdown), 0.95),
                MeanSharpe = metrics.Average(x => x.Sharpe),
                MedianSharpe = Percentile(metrics.Select(x => x.Sharpe), 0.5),
                LossProbability = metrics.Count(x => x.FinalEquity < 1) / (double)metrics.Count
            };
        }

        private static BacktestMonteCarloPathMetrics ComputePathMetrics(IReadOnlyList<double> path)
        {
            var equity = 1d;
            var peak = 1d;
            var maxDrawdown = 0d;
            var mean = path.Count == 0 ? 0 : path.Average();
            var variance = path.Count == 0 ? 0 : path.Select(value => Math.Pow(value - mean, 2)).Average();
            var sharpe = variance > 0 ? mean / Math.Sqrt(variance) * Math.Sqrt(252d) : 0d;

            foreach (var value in path)
            {
                equity *= 1 + value;
                peak = Math.Max(peak, equity);
                maxDrawdown = Math.Min(maxDrawdown, equity / peak - 1);
            }

            return new BacktestMonteCarloPathMetrics
            {
                FinalEquity = equity,
                TotalReturn = equity - 1,
                MaxDrawdown = maxDrawdown,
                Sharpe = sharpe
            };
        }

        private static double Percentile(IEnumerable<double> values, double percentile)
        {
            var ordered = values.OrderBy(x => x).ToArray();
            if (ordered.Length == 0)
            {
                return 0;
            }
            if (ordered.Length == 1)
            {
                return ordered[0];
            }

            var position = (ordered.Length - 1) * percentile;
            var lower = (int)Math.Floor(position);
            var upper = (int)Math.Ceiling(position);
            if (lower == upper)
            {
                return ordered[lower];
            }

            var weight = position - lower;
            return ordered[lower] + (ordered[upper] - ordered[lower]) * weight;
        }

        private static string ToPercent(double value)
        {
            return (value * 100d).ToString("0.00", CultureInfo.InvariantCulture) + "%";
        }

        private static double NextGaussian(Random random, double mean, double standardDeviation)
        {
            if (standardDeviation == 0)
            {
                return mean;
            }

            var u1 = 1.0 - random.NextDouble();
            var u2 = 1.0 - random.NextDouble();
            var standardNormal = Math.Sqrt(-2.0 * Math.Log(u1)) * Math.Cos(2.0 * Math.PI * u2);
            return mean + standardDeviation * standardNormal;
        }
    }

    internal sealed class BacktestMonteCarloReport
    {
        public DateTime GeneratedAtUtc { get; set; }
        public int SourceTradeDays { get; set; }
        public int TrialCount { get; set; }
        public int HorizonDays { get; set; }
        public int BlockSize { get; set; }
        public Dictionary<string, BacktestMonteCarloScenarioReport> Scenarios { get; set; } = new Dictionary<string, BacktestMonteCarloScenarioReport>();
    }

    internal sealed class BacktestMonteCarloScenarioReport
    {
        public int TrialCount { get; set; }
        public int HorizonDays { get; set; }
        public double MeanFinalEquity { get; set; }
        public double MedianFinalEquity { get; set; }
        public double P05FinalEquity { get; set; }
        public double P95FinalEquity { get; set; }
        public double MeanTotalReturn { get; set; }
        public double P05TotalReturn { get; set; }
        public double MedianTotalReturn { get; set; }
        public double P95TotalReturn { get; set; }
        public double MeanMaxDrawdown { get; set; }
        public double MedianMaxDrawdown { get; set; }
        public double P95MaxDrawdown { get; set; }
        public double MeanSharpe { get; set; }
        public double MedianSharpe { get; set; }
        public double LossProbability { get; set; }
    }

    internal sealed class BacktestMonteCarloPathMetrics
    {
        public double FinalEquity { get; set; }
        public double TotalReturn { get; set; }
        public double MaxDrawdown { get; set; }
        public double Sharpe { get; set; }
    }
}
