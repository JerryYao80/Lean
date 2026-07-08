using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class GoldOvernightPremiumFactorTests
    {
        [Test]
        public void InjectValue_ThenCompute_ReturnsValue()
        {
            var factor = new GoldOvernightPremiumFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectValue(sym, 1.7m);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual(1.7m, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void Compute_WithoutInjection_ReturnsMissing()
        {
            var factor = new GoldOvernightPremiumFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        [Test]
        public void Id_IsStable()
        {
            Assert.AreEqual("gold_overnight_premium_z", new GoldOvernightPremiumFactor().Id);
        }
    }
}
