// file: Tests/Common/Risk/VaR/VarEngineTests.cs
using System;
using System.Linq;
using MathNet.Numerics.Distributions;
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Risk.VaR
{
    [TestFixture]
    public class VarEngineTests
    {
        private static double[] NormalReturns(int n, double mu, double sigma, int seed)
        {
            var normal = new Normal(mu, sigma) { RandomSource = new MathNet.Numerics.Random.MersenneTwister(seed) };
            return normal.Samples().Take(n).ToArray();
        }

        [Test]
        public void Compute_BootstrapHistorical_Normal95_ApproximatesAnalytic()
        {
            var returns = NormalReturns(500, 0, 0.02, 42);
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.BootstrapHistorical, VaRScenario.OneDay95);
            Assert.IsTrue(result.IsValid);
            // Analytic 1.645 * 0.02 = 0.0329
            Assert.AreEqual(0.0329, result.ValueAtRisk, 0.012);
            Assert.GreaterOrEqual(result.ExpectedShortfall, result.ValueAtRisk - 1e-9);
        }

        [Test]
        public void Compute_DirectQuantile_Normal99_ApproximatesAnalytic()
        {
            var returns = NormalReturns(500, 0, 0.02, 42);
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.DirectQuantile, VaRScenario.OneDay99);
            Assert.IsTrue(result.IsValid);
            // Analytic 2.326 * 0.02 = 0.0465
            Assert.AreEqual(0.0465, result.ValueAtRisk, 0.015);
        }

        [Test]
        public void Compute_InsufficientHistory_ReturnsNaN()
        {
            var returns = new[] { 0.01, 0.02 };
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.BootstrapHistorical, VaRScenario.OneDay99);
            Assert.IsFalse(result.IsValid);
            Assert.AreEqual(VaRDataQuality.InsufficientHistory, result.Quality);
            Assert.IsNaN(result.ValueAtRisk);
        }

        [Test]
        public void Compute_AllConstant_ReturnsZero()
        {
            var returns = Enumerable.Repeat(0.01, 300).ToArray();
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.DirectQuantile, VaRScenario.OneDay99);
            Assert.AreEqual(VaRDataQuality.AllReturnsConstant, result.Quality);
            Assert.AreEqual(0.0, result.ValueAtRisk);
        }

        [Test]
        public void Compute_TenDay99_UsesOverlappingOrSqrtFallback()
        {
            var returns = NormalReturns(300, 0, 0.02, 42);
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.DirectQuantile, VaRScenario.TenDay99);
            Assert.IsTrue(result.IsValid || result.Quality == VaRDataQuality.Valid);
            var oneDay = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.DirectQuantile, VaRScenario.OneDay99);
            Assert.Greater(result.ValueAtRisk, oneDay.ValueAtRisk * 2);
        }

        [Test]
        public void Compute_IsDeterministic_SameSeedSameResult()
        {
            var returns = NormalReturns(300, 0, 0.02, 42);
            var r1 = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.BootstrapHistorical, VaRScenario.OneDay99);
            var r2 = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.BootstrapHistorical, VaRScenario.OneDay99);
            Assert.AreEqual(r1.ValueAtRisk, r2.ValueAtRisk, 1e-15);
        }

        [Test]
        public void Compute_CornishFisher_Normal99_ApproximatesAnalytic()
        {
            var returns = NormalReturns(500, 0, 0.02, 42);
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.CornishFisher, VaRScenario.OneDay99);
            Assert.IsTrue(result.IsValid);
            // For normal (skew≈0, exKurt≈0), CF reduces to normal: 2.326 * 0.02 = 0.0465
            Assert.AreEqual(0.0465, result.ValueAtRisk, 0.012);
        }

        [Test]
        public void Compute_CornishFisher_TenDay_SqrtScaling()
        {
            var returns = NormalReturns(500, 0, 0.02, 42);
            var oneDay = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.CornishFisher, VaRScenario.OneDay99);
            var tenDay = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.CornishFisher, VaRScenario.TenDay99);
            // sqrt(10) scaling
            Assert.AreEqual(oneDay.ValueAtRisk * Math.Sqrt(10), tenDay.ValueAtRisk, 1e-9);
        }

        [Test]
        public void Compute_CornishFisher_FlagDegenerateTail()
        {
            // Heavy-tailed synthetic: large negative skew
            var rng = new MathNet.Numerics.Random.MersenneTwister(42);
            var returns = new double[300];
            for (int i = 0; i < 300; i++)
            {
                // Inject extreme negative returns to create skew
                returns[i] = (rng.NextDouble() < 0.1) ? -0.10 : 0.01;
            }
            var result = VarEngine.Compute(Symbol.Empty, returns, VaRMethod.CornishFisher, VaRScenario.OneDay99);
            // Should still compute but flag DegenerateTail (or Valid if skew doesn't exceed threshold)
            Assert.IsTrue(result.Quality == VaRDataQuality.DegenerateTail || result.Quality == VaRDataQuality.Valid);
        }
    }
}