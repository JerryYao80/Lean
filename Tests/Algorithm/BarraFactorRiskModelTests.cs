/*
 * BarraFactorRiskModelTests.cs — NUnit tests for BarraFactorRiskModel (P2-C Task 3).
 *
 * Tests:
 *   1. LoadLatestRisk_PicksLatestAtOrBeforeAsOf
 *   2. PortfolioVariance_ComputesCorrectly
 *   3. VolTargeting_ScalesDownWhenVolExceedsTarget
 *   4. MissingRiskData_LoadReturnsMinValue_NoCrash
 *   5. FactorExposureBudget_ScalesOverBudgetSymbol
 */
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class BarraFactorRiskModelTests
    {
        private static string WriteRiskJson(string dir, string date,
            double[][] sigmaF, Dictionary<string, double> delta,
            Dictionary<string, double[]> exposures)
        {
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, $"barra_risk_{date}.json");
            var payload = new
            {
                as_of = date,
                factors = new[] { "f0", "f1" },
                sigma_f = sigmaF,
                delta = delta,
                exposures = exposures,
                est_window = 504, decay_halflife = 252,
                n_symbols = exposures.Count, n_obs = 504, fallback = (string)null
            };
            File.WriteAllText(path, JsonConvert.SerializeObject(payload));
            return path;
        }

        [Test]
        public void LoadLatestRisk_PicksLatestAtOrBeforeAsOf()
        {
            var dir = Path.Combine(Path.GetTempPath(), $"barra_risk_{Guid.NewGuid():N}");
            try
            {
                WriteRiskJson(dir, "2024-01-31",
                    new[] { new[] { 0.04, 0.0 }, new[] { 0.0, 0.04 } },
                    new Dictionary<string, double>(), new Dictionary<string, double[]>());
                WriteRiskJson(dir, "2024-03-31",
                    new[] { new[] { 0.05, 0.0 }, new[] { 0.0, 0.05 } },
                    new Dictionary<string, double>(), new Dictionary<string, double[]>());

                var model = new BarraFactorRiskModel(dir);
                model.LoadLatestRiskForTest(new DateTime(2024, 2, 15));
                Assert.That(model.RiskAsOfForTest(), Is.EqualTo(new DateTime(2024, 1, 31)));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        [Test]
        public void PortfolioVariance_ComputesCorrectly()
        {
            // w=[0.5,0.5], B=[[1,0],[0,1]], Sigma_f=diag(0.04,0.04), Delta=[0.1,0.2]
            // factor var = 0.5*0.04*0.5 + 0.5*0.04*0.5 = 0.02
            // specific var = 0.5^2*0.1 + 0.5^2*0.2 = 0.075; total = 0.095
            var dir = Path.Combine(Path.GetTempPath(), $"barra_risk_{Guid.NewGuid():N}");
            try
            {
                WriteRiskJson(dir, "2024-01-31",
                    new[] { new[] { 0.04, 0.0 }, new[] { 0.0, 0.04 } },
                    new Dictionary<string, double> { { "600519", 0.1 }, { "000858", 0.2 } },
                    new Dictionary<string, double[]>
                    {
                        { "600519", new[] { 1.0, 0.0 } },
                        { "000858", new[] { 0.0, 1.0 } }
                    });

                var model = new BarraFactorRiskModel(dir);
                model.LoadLatestRiskForTest(new DateTime(2024, 2, 1));
                var variance = model.ComputePortfolioVarianceForTest(
                    new Dictionary<string, double> { { "600519", 0.5 }, { "000858", 0.5 } });
                Assert.That(variance, Is.EqualTo(0.095).Within(1e-6));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        [Test]
        public void VolTargeting_ScalesDownWhenVolExceedsTarget()
        {
            // w=[1.0] on symbol with factor var 0.5 -> vol = sqrt(0.5) = 0.707 > 0.20 target
            // scale = 0.20 / 0.707 = 0.2828; scaled weight = 1.0 * 0.2828 = 0.2828
            // post-scale variance = 0.2828^2 * 0.5 = 0.04 = targetVol^2
            var dir = Path.Combine(Path.GetTempPath(), $"barra_risk_{Guid.NewGuid():N}");
            try
            {
                WriteRiskJson(dir, "2024-01-31",
                    new[] { new[] { 0.5, 0.0 }, new[] { 0.0, 0.5 } },
                    new Dictionary<string, double> { { "600519", 0.0 } },
                    new Dictionary<string, double[]> { { "600519", new[] { 1.0, 0.0 } } });

                var model = new BarraFactorRiskModel(dir, targetVol: 0.20m, maxFactorExposure: 10.0m);
                model.LoadLatestRiskForTest(new DateTime(2024, 2, 1));
                var weights = new Dictionary<string, double> { { "600519", 1.0 } };
                var scaled = model.ApplyVolTargetingForTest(weights);
                var expectedScale = 0.20 / Math.Sqrt(0.5);
                Assert.That(scaled["600519"], Is.EqualTo(expectedScale).Within(1e-4));
                // post-scale variance should be ~ targetVol^2 = 0.04
                var postVar = model.ComputePortfolioVarianceForTest(scaled);
                Assert.That(postVar, Is.EqualTo(0.04).Within(1e-4));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }

        [Test]
        public void MissingRiskData_LoadReturnsMinValue_NoCrash()
        {
            var emptyDir = Path.Combine(Path.GetTempPath(), $"barra_empty_{Guid.NewGuid():N}");
            Directory.CreateDirectory(emptyDir);
            try
            {
                var model = new BarraFactorRiskModel(emptyDir);
                model.LoadLatestRiskForTest(new DateTime(2024, 2, 1));
                Assert.That(model.RiskAsOfForTest(), Is.EqualTo(DateTime.MinValue));
                // ComputePortfolioVariance on missing data must not crash; returns 0.
                var v = model.ComputePortfolioVarianceForTest(
                    new Dictionary<string, double> { { "600519", 1.0 } });
                Assert.That(v, Is.EqualTo(0.0));
            }
            finally
            {
                if (Directory.Exists(emptyDir)) Directory.Delete(emptyDir, true);
            }
        }

        [Test]
        public void FactorExposureBudget_ScalesOverBudgetSymbol()
        {
            // w=[1.0] on symbol with factor0 exposure 2.0; maxFactorExposure=0.5 -> scale 0.25
            var dir = Path.Combine(Path.GetTempPath(), $"barra_risk_{Guid.NewGuid():N}");
            try
            {
                WriteRiskJson(dir, "2024-01-31",
                    new[] { new[] { 0.01, 0.0 }, new[] { 0.0, 0.01 } },
                    new Dictionary<string, double> { { "600519", 0.0 } },
                    new Dictionary<string, double[]> { { "600519", new[] { 2.0, 0.0 } } });

                var model = new BarraFactorRiskModel(dir, targetVol: 10.0m, maxFactorExposure: 0.5m);
                model.LoadLatestRiskForTest(new DateTime(2024, 2, 1));
                // factor0 exp = 1.0 * 2.0 = 2.0 > 0.5 -> scale = 0.25
                var scaled = model.ApplyFactorExposureBudgetForTest(
                    new Dictionary<string, double> { { "600519", 1.0 } });
                Assert.That(scaled["600519"], Is.EqualTo(0.25).Within(1e-9));
                // verify variance > 0 after scaling
                var variance = model.ComputePortfolioVarianceForTest(scaled);
                Assert.That(variance, Is.GreaterThan(0));
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
        }
    }
}
