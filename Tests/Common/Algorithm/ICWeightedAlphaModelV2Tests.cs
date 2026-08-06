/*
 * TDD tests for ICWeightedAlphaModelV2 — verifies JSON IC-report loading,
 * latest-report selection, and fallback behavior. Tests inject a temp JSON
 * directory so they are deterministic and do not touch real parquet data.
 */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Alpha;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture]
    public class ICWeightedAlphaModelV2Tests
    {
        private string _tempDir;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(), "ic-v2-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_tempDir)) Directory.Delete(_tempDir, true);
        }

        private void WriteReport(string date, params (string id, double ic, double ir, double weight)[] factors)
        {
            var payload = new
            {
                report_date = date,
                ic_window_start = "2020-01-02",
                ic_window_end = date,
                horizon = 21,
                min_ic = 0.02,
                min_ir = 0.3,
                factors = factors.Select(f => new
                {
                    factor_id = f.id,
                    rank_ic_mean = f.ic,
                    rank_ic_ir = f.ir,
                    weight = f.weight
                }).ToList()
            };
            File.WriteAllText(
                Path.Combine(_tempDir, $"ic_report_{date}.json"),
                JsonConvert.SerializeObject(payload, Formatting.Indented));
        }

        [Test]
        public void SelectLatestReport_ReadsJsonAndPicksLatestAtOrBeforeDate()
        {
            WriteReport("2024-01-31", ("alpha012", 0.052, 0.42, 0.6), ("alpha077", 0.048, 0.39, 0.4));
            WriteReport("2024-02-29", ("alpha012", 0.055, 0.45, 0.5), ("alpha001", 0.040, 0.35, 0.5));

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 3, 15));

            Assert.IsNotNull(report);
            Assert.AreEqual("2024-02-29", report.ReportDate);
            Assert.AreEqual(2, report.Factors.Count);
            Assert.AreEqual("alpha012", report.Factors[0].FactorId);
            Assert.AreEqual(0.5, report.Factors[0].Weight, 1e-9);
        }

        [Test]
        public void LoadLatestReport_NoReportAtOrBeforeDate_FallsBackToEarliest()
        {
            WriteReport("2024-06-30", ("alpha012", 0.052, 0.42, 1.0));

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 1, 1));

            Assert.IsNotNull(report, "should fall back to earliest report when none <= asOf");
            Assert.AreEqual("2024-06-30", report.ReportDate);
        }

        [Test]
        public void LoadLatestReport_MissingDir_ReturnsNull()
        {
            var model = new ICWeightedAlphaModelV2("/nonexistent/path/xyz");
            Assert.DoesNotThrow(() =>
            {
                var report = model.LoadLatestReport(new DateTime(2024, 6, 30));
                Assert.IsNull(report);
            });
        }

        [Test]
        public void LoadLatestReport_CorruptJson_SkipsFileDoesNotCrash()
        {
            WriteReport("2024-01-31", ("alpha012", 0.052, 0.42, 1.0));
            File.WriteAllText(Path.Combine(_tempDir, "ic_report_2024-02-29.json"),
                "{ this is not valid json }");

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 6, 30));
            Assert.IsNotNull(report);
            Assert.AreEqual("2024-01-31", report.ReportDate);
        }
    }
}
