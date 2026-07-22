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
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Lean.Engine.Results;
using QuantConnect.Configuration;
using QuantConnect.Util;

namespace QuantConnect.Tests.Engine.Results
{
    [TestFixture]
    public class BacktestMonteCarloAnalysisTests
    {
        [SetUp]
        public void SetUp()
        {
            Config.Reset();
        }

        [Test]
        public void ReadsSettingsFromParameters()
        {
            var settings = BacktestMonteCarloSettings.From(new Dictionary<string, string>
            {
                ["monte-carlo-enabled"] = "true",
                ["monte-carlo-trials"] = "64",
                ["monte-carlo-horizon-days"] = "21",
                ["monte-carlo-block-size"] = "3",
                ["monte-carlo-extra-fee-rate"] = "0.0012",
                ["monte-carlo-shock-probability"] = "0.15",
                ["monte-carlo-shock-mean"] = "0.02",
                ["monte-carlo-shock-std"] = "0.005",
                ["monte-carlo-seed"] = "99"
            });

            Assert.IsTrue(settings.Enabled);
            Assert.AreEqual(64, settings.TrialCount);
            Assert.AreEqual(21, settings.HorizonDays);
            Assert.AreEqual(3, settings.BlockSize);
            Assert.AreEqual(0.0012, settings.ExtraFeeRate, 1e-12);
            Assert.AreEqual(0.15, settings.ShockProbability, 1e-12);
            Assert.AreEqual(0.02, settings.ShockMean, 1e-12);
            Assert.AreEqual(0.005, settings.ShockStandardDeviation, 1e-12);
            Assert.AreEqual(99, settings.Seed);
        }

        [Test]
        public void ExtractDailyReturnsUsesLastSampleOfEachDate()
        {
            var chart = new Chart(BaseResultsHandler.StrategyEquityKey);
            var returns = new Series(BaseResultsHandler.ReturnKey, SeriesType.Bar, 1, "%");
            returns.AddPoint(new ChartPoint(new DateTime(2024, 1, 2, 0, 0, 0), 0.25m));
            returns.AddPoint(new ChartPoint(new DateTime(2024, 1, 2, 20, 0, 0), 1.50m));
            returns.AddPoint(new ChartPoint(new DateTime(2024, 1, 3, 0, 0, 0), -0.75m));
            chart.Series[BaseResultsHandler.ReturnKey] = returns;

            var extracted = BacktestMonteCarloAnalysis.ExtractDailyReturns(new Dictionary<string, Chart>
            {
                [BaseResultsHandler.StrategyEquityKey] = chart
            });

            CollectionAssert.AreEqual(new[] { 0.015, -0.0075 }, extracted.ToArray());
        }

        [Test]
        public void AppliesFeeAndShockStressDeterministically()
        {
            var paths = new List<List<double>>
            {
                new() { 0.01, 0.02 },
                new() { 0.0, -0.01 }
            };

            var feeAdjusted = BacktestMonteCarloAnalysis.ApplyFeeStress(paths, 0.0015);
            var shockAdjusted = BacktestMonteCarloAnalysis.ApplyShockStress(paths, 1.0, 0.02, 0.0, 11);

            CollectionAssert.AreEqual(new[] { 0.0085, 0.0185 }, feeAdjusted[0].ToArray());
            CollectionAssert.AreEqual(new[] { -0.0015, -0.0115 }, feeAdjusted[1].ToArray());
            CollectionAssert.AreEqual(new[] { -0.01, 0.0 }, shockAdjusted[0].ToArray());
            CollectionAssert.AreEqual(new[] { -0.02, -0.03 }, shockAdjusted[1].ToArray());
        }

        [Test]
        public void CreatesSummaryStatisticsFromCombinedScenario()
        {
            var report = BacktestMonteCarloAnalysis.CreateReport(
                new[] { 0.01, -0.02, 0.03, 0.0 },
                new BacktestMonteCarloSettings
                {
                    Enabled = true,
                    TrialCount = 32,
                    HorizonDays = 10,
                    BlockSize = 2,
                    ExtraFeeRate = 0.001,
                    ShockProbability = 0.2,
                    ShockMean = 0.015,
                    ShockStandardDeviation = 0.0,
                    Seed = 7
                });

            var summary = BacktestMonteCarloAnalysis.CreateSummaryStatistics(report);

            Assert.AreEqual("32", summary[BacktestMonteCarloAnalysis.TrialsSummaryKey]);
            Assert.IsTrue(summary.ContainsKey(BacktestMonteCarloAnalysis.CombinedLossProbabilitySummaryKey));
            Assert.IsTrue(summary.ContainsKey(BacktestMonteCarloAnalysis.CombinedMedianReturnSummaryKey));
            Assert.IsTrue(summary.ContainsKey(BacktestMonteCarloAnalysis.CombinedP95DrawdownSummaryKey));
        }

        [Test]
        public void RegressionBacktestStoresMonteCarloReportWhenEnabled()
        {
            var algorithm = nameof(BasicTemplateDailyAlgorithm);
            var reportPath = Path.Combine(Globals.ResultsDestinationFolder, $"{algorithm}-monte-carlo.json");
            if (File.Exists(reportPath))
            {
                File.Delete(reportPath);
            }

            var results = AlgorithmRunner.RunLocalBacktest(
                algorithm,
                new Dictionary<string, string>(),
                Language.CSharp,
                AlgorithmStatus.Completed,
                startDate: new DateTime(2013, 10, 7),
                endDate: new DateTime(2013, 10, 11),
                customConfigurations: new Dictionary<string, string>
                {
                    ["results-monte-carlo-enabled"] = "true",
                    ["results-monte-carlo-trials"] = "16",
                    ["results-monte-carlo-horizon-days"] = "8",
                    ["results-monte-carlo-block-size"] = "2",
                    ["results-monte-carlo-extra-fee-rate"] = "0.0005",
                    ["results-monte-carlo-shock-probability"] = "0.10",
                    ["results-monte-carlo-shock-mean"] = "0.01",
                    ["results-monte-carlo-shock-std"] = "0.0",
                    ["results-monte-carlo-seed"] = "5"
                });

            Assert.IsTrue(results.Results.FinalStatistics.ContainsKey(BacktestMonteCarloAnalysis.TrialsSummaryKey));
            Assert.IsTrue(results.Results.FinalStatistics.ContainsKey(BacktestMonteCarloAnalysis.CombinedLossProbabilitySummaryKey));
            Assert.IsTrue(File.Exists(reportPath));

            var reportJson = File.ReadAllText(reportPath);
            StringAssert.Contains("combinedStress", reportJson);
        }
    }
}
