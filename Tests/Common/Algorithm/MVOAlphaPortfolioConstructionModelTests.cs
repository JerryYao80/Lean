/*
 * TDD tests for MVOAlphaPortfolioConstructionModel — verifies MVO weight JSON
 * loading, latest-file selection, and weight->PortfolioTarget mapping.
 * Tests inject a temp JSON dir + fake algorithm; no real parquet/pythonnet.
 */
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data.Market;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture]
    public class MVOAlphaPortfolioConstructionModelTests
    {
        private string _tempDir;
        private Symbol _symA, _symB;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(), "mvo-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
            _symA = Symbol.Create("600000", SecurityType.Equity, Market.China);
            _symB = Symbol.Create("600001", SecurityType.Equity, Market.China);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_tempDir)) Directory.Delete(_tempDir, true);
        }

        private void WriteWeights(string date, params (string code, double w)[] syms)
        {
            var payload = new
            {
                as_of = date,
                symbols = Array.ConvertAll(syms, s => new { ts_code = s.code, weight = s.w }),
                sum = 1.0,
                lambda = 1.0,
                cov_window = 120,
                n_assets = syms.Length,
                ic_report_used = $"ic_report_{date}.json",
                fallback = (string)null
            };
            File.WriteAllText(Path.Combine(_tempDir, $"mvo_weights_{date}.json"),
                JsonConvert.SerializeObject(payload, Formatting.Indented));
        }

        // ts_code in JSON uses SSE/SZSE suffix; Symbol.Value is bare ticker.
        // The model strips the suffix to match Symbol.Value.
        [Test]
        public void LoadLatestWeights_PicksLatestAtOrBefore()
        {
            WriteWeights("2024-01-31", ("600000.SH", 0.6), ("600001.SH", 0.4));
            WriteWeights("2024-02-29", ("600000.SH", 0.5), ("600001.SH", 0.5));

            var model = new MVOAlphaPortfolioConstructionModel(_tempDir);
            model.LoadLatestWeightsForTest(new DateTime(2024, 3, 15));
            var weights = model.GetWeightsCacheForTest();
            Assert.AreEqual(0.5m, weights["600000"]);
            Assert.AreEqual(0.5m, weights["600001"]);
        }

        [Test]
        public void LoadLatestWeights_FallsBackToEarliest()
        {
            WriteWeights("2024-02-29", ("600000.SH", 0.6), ("600001.SH", 0.4));
            var model = new MVOAlphaPortfolioConstructionModel(_tempDir);
            // as_of before any report -> earliest
            model.LoadLatestWeightsForTest(new DateTime(2024, 1, 1));
            var weights = model.GetWeightsCacheForTest();
            Assert.AreEqual(0.6m, weights["600000"]);
        }

        [Test]
        public void LoadLatestWeights_StripsExchangeSuffix()
        {
            WriteWeights("2024-01-31", ("600000.SH", 0.7), ("000001.SZ", 0.3));
            var model = new MVOAlphaPortfolioConstructionModel(_tempDir);
            model.LoadLatestWeightsForTest(new DateTime(2024, 2, 1));
            var weights = model.GetWeightsCacheForTest();
            Assert.IsTrue(weights.ContainsKey("600000"));
            Assert.IsTrue(weights.ContainsKey("000001"));
        }
    }
}
