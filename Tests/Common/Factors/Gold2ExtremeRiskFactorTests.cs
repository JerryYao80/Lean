using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2ExtremeRiskFactorTests
    {
        // 设计 §4.3: Trigger = VIX_t > VIX_P95 OR RVol_60d > RVol_P95
        [Test]
        public void Warmup_ReturnsNotTriggered()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 252);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(0m, r.Value, "窗口未满不触发");
        }

        [Test]
        public void VixAboveP95_Triggers()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            f.UpdateVix(50m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "VIX>P95 触发");
        }

        [Test]
        public void NormalVix_NoTrigger()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m + (i % 3), t0.AddDays(i));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(0m, r.Value, "正常区间不触发");
        }

        [Test]
        public void RVolAboveP95_TriggersEvenWithNormalVix()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) { f.UpdateVix(15m, t0.AddDays(i)); f.UpdateRvol60(0.10m, t0.AddDays(i)); }
            f.UpdateRvol60(0.90m, t0.AddDays(100));
            f.UpdateVix(15m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "RVol>P95 触发(GPR 代理)");
        }
    }
}
