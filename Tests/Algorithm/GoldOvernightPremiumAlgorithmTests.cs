using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class GoldOvernightPremiumAlgorithmTests
    {
        // 风控测试用最小 algorithm 桩提供 algorithm.Time。GoldOvernightPremiumAlgorithm 在 Task 8 实现；
        // 此处先测 ManageRisk 纯逻辑：14:45 后任何持仓归零。
        // 注意：QCAlgorithm.Time 非 virtual，无法用 `new` 隐藏在 ManageRisk(QCAlgorithm,...) 内生效。
        // 故用 SetDateTime 真正推进 algorithm 时间 (LEAN-native 方式)。

        [Test]
        public void NoOvernightPosition_AfterForcedCloseTime_LiquidatesAll()
        {
            var model = new NoOvernightPositionRiskModel(forcedCloseTime: new TimeSpan(14, 45, 0));
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var target = new PortfolioTarget(sym, 1000);
            var algo = new TestAlgorithm();
            algo.SetCurrentTime(new DateTime(2026, 6, 16, 14, 46, 0));
            var result = model.ManageRisk(algo, new[] { target });
            foreach (var t in result)
                Assert.AreEqual(0, t.Quantity, "14:46 应强制平仓");
        }

        [Test]
        public void NoOvernightPosition_BeforeForcedCloseTime_PassesThrough()
        {
            var model = new NoOvernightPositionRiskModel(forcedCloseTime: new TimeSpan(14, 45, 0));
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var target = new PortfolioTarget(sym, 1000);
            var algo = new TestAlgorithm();
            algo.SetCurrentTime(new DateTime(2026, 6, 16, 10, 0, 0));
            var result = model.ManageRisk(algo, new[] { target });
            foreach (var t in result)
                Assert.AreEqual(1000, t.Quantity, "10:00 应放行");
        }

        [Test]
        public void EntryLogic_LongOnly_ZAboveThreshold()
        {
            // 设计 §5: Z > 1.5 做多；Z < -1.5 空仓观望（不可做空）；|Z|<=1.5 空仓。
            Assert.AreEqual(1, GoldOvernightPremiumAlgorithm.EntryDirection(1.6m));   // 多
            Assert.AreEqual(0, GoldOvernightPremiumAlgorithm.EntryDirection(-1.6m));  // 观望（不可做空）
            Assert.AreEqual(0, GoldOvernightPremiumAlgorithm.EntryDirection(0.5m));   // 无 edge
        }

        [Test]
        public void RegimeCap_RisingFast_ReducesTo03x()
        {
            Assert.AreEqual(0.3m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.RISING_FAST));
            Assert.AreEqual(1.0m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.UNAVAILABLE));
            Assert.AreEqual(1.0m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.STABLE));
        }

        public class TestAlgorithm : QuantConnect.Algorithm.QCAlgorithm
        {
            public TestAlgorithm()
            {
                // 黄金 ETF (518880) 在 SSE 交易；算法时区设为上海，使 algorithm.Time 为北京时间。
                SetTimeZone(TimeZones.Shanghai);
            }

            /// <summary>推进 algorithm.Time 到指定的本地 (上海) 时间。</summary>
            public void SetCurrentTime(DateTime localTime)
            {
                SetDateTime(localTime.ConvertToUtc(TimeZone));
            }
        }
    }
}
