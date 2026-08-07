using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2RealRateCapFactorTests
    {
        // 设计 §4.4: Cap = RISING_FAST ? 0.6 : 1.0; UNAVAILABLE→1.0
        [Test]
        public void RisingFast_CapsTo06()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Symbol.Empty, GoldRegime.RISING_FAST);
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(0.6m, r.Value);
        }

        [Test]
        public void Stable_NoCap()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Symbol.Empty, GoldRegime.STABLE);
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(1.0m, r.Value);
        }

        [Test]
        public void Unavailable_NoCapNotBlocked()
        {
            var inner = new GoldRealRateRegimeFactor();
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(1.0m, r.Value, "DFII10 缺失不阻断 cap=1.0");
        }
    }
}
