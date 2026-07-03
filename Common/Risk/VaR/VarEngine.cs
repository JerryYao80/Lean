// file: Common/Risk/VaR/VarEngine.cs
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using MathNet.Numerics.Random;
using MathNet.Numerics.Statistics;
using QuantConnect.Securities;

namespace QuantConnect.Risk.VaR
{
    /// <summary>
    /// Pure-math VaR engine. Public static API for single-asset and portfolio VaR.
    /// No LEAN engine/algorithm dependencies.
    /// </summary>
    public static partial class VarEngine
    {
        /// <summary>Compute single-asset VaR.</summary>
        public static VaRResult Compute(
            Symbol symbol,
            IEnumerable<double> returns,
            VaRMethod method,
            VaRScenario scenario,
            VarConfig config = default)
        {
            if (config.Equals(default(VarConfig)))
                config = VarConfig.FromScenario(scenario);

            var sw = Stopwatch.StartNew();
            var (clean, allConstant, zeroVariance, count) = VaRMath.PrepareReturns(returns, config.MinHistoryDays);

            if (count < config.MinHistoryDays)
                return InsufficientHistoryResult(symbol, method, scenario, config, count, sw);

            if (allConstant)
                return new VaRResult(0, 0, 1 - config.Confidence, config.HorizonDays, config.Confidence,
                    method, scenario, VaRDataQuality.AllReturnsConstant, "All returns constant",
                    count, sw.ElapsedMilliseconds, symbol);

            if (zeroVariance)
                return new VaRResult(0, 0, 1 - config.Confidence, config.HorizonDays, config.Confidence,
                    method, scenario, VaRDataQuality.ZeroVariance, "Zero variance", count, sw.ElapsedMilliseconds, symbol);

            return method switch
            {
                VaRMethod.BootstrapHistorical => ComputeBootstrapHistorical(symbol, clean, config, scenario, sw),
                VaRMethod.DirectQuantile => ComputeDirectQuantile(symbol, clean, config, scenario, sw),
                VaRMethod.CornishFisher => throw new NotImplementedException("CornishFisher added in Task 6"),
                VaRMethod.MonteCarlo => throw new NotImplementedException("MonteCarlo added in Task 7"),
                _ => throw new ArgumentException($"Unknown method: {method}")
            };
        }

        /// <summary>Compute all 4 methods x 3 scenarios (12 results). Skips NotImplemented.</summary>
        public static IReadOnlyDictionary<(VaRMethod Method, VaRScenario Scenario), VaRResult> ComputeAll(
            Symbol symbol, IEnumerable<double> returns, VarConfig config = default)
        {
            var methods = new[] { VaRMethod.BootstrapHistorical, VaRMethod.CornishFisher, VaRMethod.MonteCarlo, VaRMethod.DirectQuantile };
            var scenarios = new[] { VaRScenario.OneDay95, VaRScenario.OneDay99, VaRScenario.TenDay99 };
            var results = new Dictionary<(VaRMethod, VaRScenario), VaRResult>();
            foreach (var m in methods)
                foreach (var s in scenarios)
                {
                    try { results[(m, s)] = Compute(symbol, returns, m, s, config); }
                    catch (NotImplementedException) { /* skip until implemented */ }
                }
            return results;
        }

        private static VaRResult InsufficientHistoryResult(Symbol symbol, VaRMethod method, VaRScenario scenario, VarConfig config, int count, Stopwatch sw)
        {
            sw.Stop();
            return new VaRResult(double.NaN, double.NaN, 1 - config.Confidence, config.HorizonDays, config.Confidence,
                method, scenario, VaRDataQuality.InsufficientHistory,
                $"Insufficient history: {count} < {config.MinHistoryDays}", count, sw.ElapsedMilliseconds, symbol);
        }

        private static VaRResult ComputeBootstrapHistorical(Symbol symbol, double[] returns, VarConfig config, VaRScenario scenario, Stopwatch sw)
        {
            var (working, diagMsg, fallbackResult) = PrepareWorkingReturns(symbol, returns, config, scenario, VaRMethod.BootstrapHistorical, sw);
            if (fallbackResult != null) return fallbackResult.Value;

            var rng = new MersenneTwister(config.RandomSeed, true);
            var tau = 1 - config.Confidence;
            var n = working.Length;
            var varSamples = new double[config.BootstrapIterations];
            var esSamples = new double[config.BootstrapIterations];

            for (int b = 0; b < config.BootstrapIterations; b++)
            {
                var sample = new double[n];
                for (int i = 0; i < n; i++) sample[i] = working[rng.Next(n)];
                Array.Sort(sample);
                var varB = -SortedArrayStatistics.Quantile(sample, tau);
                varSamples[b] = varB;
                var threshold = -varB;
                var tail = sample.TakeWhile(r => r <= threshold).ToArray();
                esSamples[b] = tail.Length > 0 ? -tail.Average() : varB;
            }

            var varFinal = varSamples.Median();
            var esFinal = esSamples.Median();
            if (esFinal < varFinal - 1e-9) { diagMsg = (diagMsg ?? "") + "ES clamped to VaR"; esFinal = varFinal; }
            sw.Stop();

            return new VaRResult(varFinal, esFinal, tau, config.HorizonDays, config.Confidence,
                VaRMethod.BootstrapHistorical, scenario, VaRDataQuality.Valid, diagMsg, n, sw.ElapsedMilliseconds, symbol);
        }

        private static VaRResult ComputeDirectQuantile(Symbol symbol, double[] returns, VarConfig config, VaRScenario scenario, Stopwatch sw)
        {
            var (working, diagMsg, fallbackResult) = PrepareWorkingReturns(symbol, returns, config, scenario, VaRMethod.DirectQuantile, sw);
            if (fallbackResult != null) return fallbackResult.Value;

            var sorted = working.OrderBy(r => r).ToArray();
            var tau = 1 - config.Confidence;
            var varValue = -SortedArrayStatistics.Quantile(sorted, tau);
            var threshold = -varValue;
            var tail = sorted.TakeWhile(r => r <= threshold).ToArray();
            var esValue = tail.Length > 0 ? -tail.Average() : varValue;
            if (esValue < varValue - 1e-9) { diagMsg = (diagMsg ?? "") + "ES clamped to VaR"; esValue = varValue; }
            sw.Stop();

            return new VaRResult(varValue, esValue, tau, config.HorizonDays, config.Confidence,
                VaRMethod.DirectQuantile, scenario, VaRDataQuality.Valid, diagMsg, working.Length, sw.ElapsedMilliseconds, symbol);
        }

        /// <summary>Build 1D or overlapping-10D working returns; fallback to sqrt(10) if overlap too short.</summary>
        private static (double[] Working, string DiagMsg, VaRResult? Fallback) PrepareWorkingReturns(
            Symbol symbol, double[] returns, VarConfig config, VaRScenario scenario, VaRMethod method, Stopwatch sw)
        {
            string diagMsg = null;
            if (config.HorizonDays == 1)
                return (returns, diagMsg, null);

            if (config.UseOverlappingFor10Day)
            {
                var overlap = VaRMath.BuildOverlappingKDayReturns(returns, config.HorizonDays);
                if (overlap.Length < VaRConstants.MinOverlappingBlocks)
                {
                    // Fallback to sqrt(10) scaling on the 1D result
                    var oneDayConfig = new VarConfig(
                        lookbackDays: config.LookbackDays, minHistoryDays: config.MinHistoryDays,
                        horizonDays: 1, confidence: config.Confidence,
                        bootstrapIterations: config.BootstrapIterations, monteCarloPaths: config.MonteCarloPaths,
                        randomSeed: config.RandomSeed, enforcePdCovariance: config.EnforcePdCovariance,
                        eigenvalueFloor: config.EigenvalueFloor,
                        useOverlappingFor10Day: config.UseOverlappingFor10Day,
                        useSqrtScalingFor10Day: config.UseSqrtScalingFor10Day);
                    var oneDay = method == VaRMethod.BootstrapHistorical
                        ? ComputeBootstrapHistorical(symbol, returns, oneDayConfig, VaRScenario.OneDay99, sw)
                        : ComputeDirectQuantile(symbol, returns, oneDayConfig, VaRScenario.OneDay99, sw);
                    var sqrtFactor = Math.Sqrt(config.HorizonDays);
                    sw.Stop();
                    var fallback = new VaRResult(
                        oneDay.ValueAtRisk * sqrtFactor, oneDay.ExpectedShortfall * sqrtFactor,
                        1 - config.Confidence, config.HorizonDays, config.Confidence, method, scenario,
                        oneDay.Quality, $"10D sqrt({config.HorizonDays}) fallback (overlap<{VaRConstants.MinOverlappingBlocks})",
                        returns.Length, sw.ElapsedMilliseconds, symbol);
                    return (null, diagMsg, fallback);
                }
                return (overlap, diagMsg, null);
            }

            return (returns, "10D non-overlapping not implemented, using 1D", null);
        }
    }
}