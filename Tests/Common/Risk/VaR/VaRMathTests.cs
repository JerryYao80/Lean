// file: Tests/Common/Risk/VaR/VaRMathTests.cs
using System;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Tests.Common.Risk.VaR
{
    [TestFixture]
    public class VaRMathTests
    {
        [Test]
        public void PrepareReturns_DropsNaNAndInf()
        {
            var returns = new[] { 0.01, double.NaN, 0.02, double.PositiveInfinity, -0.01 };
            var (clean, _, _, count) = VaRMath.PrepareReturns(returns, 2);
            Assert.AreEqual(3, clean.Length);
            Assert.AreEqual(3, count);
        }

        [Test]
        public void PrepareReturns_DetectsAllConstant()
        {
            var returns = Enumerable.Repeat(0.01, 300).ToArray();
            var (clean, allConstant, _, _) = VaRMath.PrepareReturns(returns, 250);
            Assert.IsTrue(allConstant);
        }

        [Test]
        public void SampleMoments_ReturnsCorrectMean()
        {
            var returns = new[] { 0.01, -0.01, 0.02, -0.02, 0.00 };
            var (mean, sd, _, _) = VaRMath.SampleMoments(returns);
            Assert.AreEqual(0.0, mean, 1e-10);
            Assert.Greater(sd, 0);
        }

        [Test]
        public void BuildOverlappingKDayReturns_CorrectLength()
        {
            var daily = Enumerable.Range(0, 10).Select(_ => 0.01).ToArray();
            var overlap3 = VaRMath.BuildOverlappingKDayReturns(daily, 3);
            Assert.AreEqual(8, overlap3.Length); // 10 - 3 + 1
        }

        [Test]
        public void BuildOverlappingKDayReturns_PreservesCompound()
        {
            var daily = new[] { 0.01, 0.02, 0.03 };
            var overlap3 = VaRMath.BuildOverlappingKDayReturns(daily, 3);
            // 1.01 * 1.02 * 1.03 - 1 = 0.061106
            Assert.AreEqual(0.061106, overlap3[0], 1e-6);
        }

        [Test]
        public void EnsurePd_PassesAlreadyPd()
        {
            var pd = new double[,] { { 1, 0.5 }, { 0.5, 1 } };
            var (matrix, repaired, _) = VaRMath.EnsurePd(pd, 1e-10);
            Assert.IsFalse(repaired);
        }

        [Test]
        public void EnsurePd_RepairsNonPd()
        {
            var nonPd = new double[,] { { 1, 2 }, { 2, 1 } };
            var (matrix, repaired, _) = VaRMath.EnsurePd(nonPd, 1e-10);
            Assert.IsTrue(repaired);
            Assert.IsNotNull(matrix);
        }

        [Test]
        public void CholeskySimulate_ReturnsCorrectShape()
        {
            var cov = new double[,] { { 0.0004, 0.0002 }, { 0.0002, 0.0004 } };
            var result = VaRMath.CholeskySimulate(cov, 100, 42);
            Assert.AreEqual(100, result.GetLength(0));
            Assert.AreEqual(2, result.GetLength(1));
        }
    }
}
