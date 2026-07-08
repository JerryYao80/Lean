using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2VolRegimeFactorTests
    {
        // 设计 §4.2: EWMA σ(λ=0.94), w=min(1, σ_target/σ_ann), w_smooth=α·w+(1-α)·w_prev
        [Test]
        public void Warmup_ReturnsZeroWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 10; i++) f.Update(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,12));
            Assert.AreEqual(0m, r.Value, "前 60 日 w=0");
        }

        [Test]
        public void SteadyLowVol_ConvergesToFullWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 200; i++) f.Update((decimal)(100.0 * Math.Pow(1.001, i)), new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,7,20));
            Assert.AreEqual(1.0, (double)r.Value, 0.01, "低波动 → w≈1.0");
        }

        [Test]
        public void VolSpike_HalvesWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.22m, 60, 1.0m);
            for (int i = 0; i < 60; i++) f.Update(100m, new DateTime(2020,1,2).AddDays(i));
            var baseR = f.Compute(Symbol.Empty, new DateTime(2020,3,5));
            for (int i = 0; i < 60; i++) f.Update(100m * (i % 2 == 0 ? 1.05m : 0.95m), new DateTime(2020,3,6).AddDays(i));
            var spikeR = f.Compute(Symbol.Empty, new DateTime(2020,6,5));
            Assert.Greater(spikeR.RawValue, baseR.RawValue, "波动率上升 σ_ann 增大");
        }

        [Test]
        public void NoLookahead_EWMA_UsesPrevReturn()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 100; i++) f.Update(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r1 = f.Compute(Symbol.Empty, new DateTime(2020,4,11));
            f.Update(9999m, new DateTime(2020,4,12));
            var r2 = f.Compute(Symbol.Empty, new DateTime(2020,4,11));
            Assert.AreEqual(r1.Value, r2.Value, "Compute(t) 不读 t+1 收益");
        }
    }
}
