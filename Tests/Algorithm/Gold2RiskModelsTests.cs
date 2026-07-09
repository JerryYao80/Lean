using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Algorithm
{
    /// <summary>
    /// L4 Risk models 测试。ExtremeRiskModel(硬 cap 0.30 绕过 dead-zone)+ RealRateCapModel(RISING_FAST cap 0.60)。
    /// 串联取 min。纯逻辑 ApplyCap/ComputeCap 可直接断言,不依赖 algorithm 桩。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.4 + §8.8。
    /// </summary>
    [TestFixture]
    public class Gold2RiskModelsTests
    {
        private static readonly Symbol _gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);

        // 设计 §5.4 + §8.8
        [Test]
        public void ExtremeRisk_Triggered_CapsTo03()
            => Assert.AreEqual(0.30m, Gold2ExtremeRiskModel.ApplyCap(0.80m, triggered: true, 0.30m));

        [Test]
        public void ExtremeRisk_NotTriggered_PassesThrough()
            => Assert.AreEqual(0.80m, Gold2ExtremeRiskModel.ApplyCap(0.80m, triggered: false, 0.30m));

        [Test]
        public void RealRate_RisingFast_CapsTo06()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(_gold, GoldRegime.RISING_FAST);
            Assert.AreEqual(0.60m, Gold2RealRateCapModel.ComputeCap(inner, _gold, DateTime.Now, 0.6m));
        }

        [Test]
        public void BothRisks_TakeMin()
        {
            decimal extremeCapped = Gold2ExtremeRiskModel.ApplyCap(0.80m, true, 0.30m);
            decimal realRateCap = 0.60m;
            Assert.AreEqual(0.30m, Math.Min(extremeCapped, realRateCap));
        }

        [Test]
        public void ExtremeRisk_OverridesDeadZone()
        {
            decimal deadZoneHeld = Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.81m, 0.05m); // 0.80
            decimal afterRisk = Gold2ExtremeRiskModel.ApplyCap(deadZoneHeld, true, 0.30m);
            Assert.AreEqual(0.30m, afterRisk, "极端触发绕过 dead-zone 直接 cap");
        }
    }
}
