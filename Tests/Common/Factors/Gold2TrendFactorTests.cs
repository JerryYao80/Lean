using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2TrendFactorTests
    {
        // 设计 §4.1: TrendSignal = sign(MA20-MA120); Confirm=同号?1:0.5
        [Test]
        public void Trend_BothUp_Confirmed_ReturnsPosDirConfirm1()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(1m, r.Value, "518880 多头方向 +1");
            Assert.AreEqual(1m, r.RawValue, "AU 同向 confirm=1");
        }

        [Test]
        public void Trend_Divergent_ConfirmHalved()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(230m - i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(1m, r.Value, "518880 仍多头");
            Assert.AreEqual(0.5m, r.RawValue, "AU 反向 confirm=0.5");
        }

        [Test]
        public void Trend_Bearish_ReturnsNegDir()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(230m - i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(230m - i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(-1m, r.Value, "空头方向 -1");
        }

        [Test]
        public void Trend_Warmup_ReturnsZero()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 10; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,12));
            Assert.AreEqual(0m, r.Value, "MA120 未满 TrendSignal=0");
        }

        [Test]
        public void NoLookahead_MA_OnlyUsesPastClose()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r1 = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            f.Update518880(9999m, new DateTime(2020,6,16));
            var r2 = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(r1.Value, r2.Value, "Compute(t) 不读 t+1");
        }
    }
}
