using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class GoldRealRateRegimeFactorTests
    {
        [Test]
        public void InjectRegime_ThenCompute_ReturnsEnumEncoded()
        {
            var factor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectRegime(sym, GoldRegime.STABLE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual((decimal)GoldRegime.STABLE, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void UnavailableRegime_DistinctFromStable()
        {
            var factor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectRegime(sym, GoldRegime.UNAVAILABLE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual((decimal)GoldRegime.UNAVAILABLE, result.Value);
            Assert.AreNotEqual((decimal)GoldRegime.STABLE, result.Value);
        }

        [Test]
        public void RegimeFactorIndependentOfSkipReason()
        {
            // 设计 §4: skip_reason 与 regime 独立。regime factor 只知 regime，不知 skip_reason。
            // 一个 DATA_STALE 日可同时 regime=STABLE；正交性由算法层组合两因子实现。
            var regimeFactor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            regimeFactor.InjectRegime(sym, GoldRegime.STABLE);
            var r1 = regimeFactor.Compute(sym, DateTime.Today);
            Assert.AreEqual(GoldRegime.STABLE, (GoldRegime)(int)r1.Value);
            // regime factor 不含 skip_reason 字段 — 物理隔离
        }
    }
}
