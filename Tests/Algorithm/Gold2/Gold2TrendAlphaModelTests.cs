using System;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class Gold2TrendAlphaModelTests
    {
        private static readonly Symbol _gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);

        // 设计 §5.2: dir>0 → Up weight=confirm; 否则 Flat weight=floor
        [Test]
        public void TrendUp_Confirmed_UpInsightWeight1()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 130; i++) trend.Update518880(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 6, 15));
            var insights = model.Update(algo, null);
            var list = insights.ToList();
            Assert.AreEqual(1, list.Count, "单标的单 insight");
            var ins = list[0];
            Assert.AreEqual(InsightDirection.Up, ins.Direction);
            Assert.AreEqual(1.0, ins.Weight.Value, 0.001);
            Assert.AreEqual("Gold2Trend", ins.SourceModel);
        }

        [Test]
        public void TrendBearish_Confirmed_FlatInsightWeightFloor()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 130; i++) trend.Update518880(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 6, 15));
            var insights = model.Update(algo, null);
            var list = insights.ToList();
            Assert.AreEqual(1, list.Count, "单标的单 insight");
            var ins = list[0];
            Assert.AreEqual(InsightDirection.Flat, ins.Direction);
            Assert.AreEqual(0.2, ins.Weight.Value, 0.001);
        }

        [Test]
        public void TrendDivergent_ConfirmHalved_UpInsightWeight05()
        {
            // 518880 多头 + AU 反向 → confirm=0.5,dir 仍 +1 → Up weight=0.5
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 130; i++) trend.Update518880(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 6, 15));
            var insights = model.Update(algo, null).ToList();
            var ins = insights[0];
            Assert.AreEqual(InsightDirection.Up, ins.Direction);
            Assert.AreEqual(0.5, ins.Weight.Value, 0.001, "AU 反向 confirm=0.5 → weight=0.5");
        }

        [Test]
        public void Warmup_InsightFlatWeightFloor()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 10; i++) trend.Update518880(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 1, 12));
            var insights = model.Update(algo, null).ToList();
            var ins = insights[0];
            Assert.AreEqual(InsightDirection.Flat, ins.Direction, "Warmup dir=0 → Flat");
            Assert.AreEqual(0.2, ins.Weight.Value, 0.001, "Warmup → floor");
        }

        // spec §7.2 vol_only 控制实验: trend-disable=true 完全旁路趋势层, dirCoef 恒=1.0
        // (无论 trend 因子是否 AU-disagreement confirm=0.5)。原 trend-floor=1.0 只 neutralize
        // DOWNtrend/warmup 分支,Up 分支仍受 confirm=0.5 污染 → 不能真正"趋势层禁用"。
        [Test]
        public void TrendDisabled_AlwaysUpWeight1_RegardlessOfAuDisagreement()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m, disabled: true);
            // 518880 多头 + AU 反向(trend confirm=0.5 的 disagreement 路径)
            for (int i = 0; i < 130; i++) trend.Update518880(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 6, 15));
            var insights = model.Update(algo, null).ToList();
            var ins = insights[0];
            Assert.AreEqual(InsightDirection.Up, ins.Direction, "disabled → 始终 Up(纯波动目标,无方向判断)");
            Assert.AreEqual(1.0, ins.Weight.Value, 0.001,
                "disabled → dirCoef=1.0,不被 AU disagreement 降到 0.5");
        }

        [Test]
        public void TrendDisabled_BearishTrendStillUpWeight1()
        {
            // 518880 + AU 同步下行(正常 trend 会判 Flat weight=floor)。
            // disabled=true 必须忽略空头信号,dirCoef 恒=1.0,做纯波动率目标。
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m, disabled: true);
            for (int i = 0; i < 130; i++) trend.Update518880(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(230m - i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 6, 15));
            var insights = model.Update(algo, null).ToList();
            var ins = insights[0];
            Assert.AreEqual(InsightDirection.Up, ins.Direction, "disabled → 忽略 trend 空头");
            Assert.AreEqual(1.0, ins.Weight.Value, 0.001, "disabled → dirCoef=1.0 (不走 floor)");
        }

        [Test]
        public void TrendDisabled_WarmupStillUpWeight1()
        {
            // warmup 期(trend 因子未 ready),disabled=true 仍走 dirCoef=1.0 而非 floor。
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m, disabled: true);
            for (int i = 0; i < 10; i++) trend.Update518880(100m + i, new DateTime(2020, 1, 2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetCurrentTime(new DateTime(2020, 1, 12));
            var insights = model.Update(algo, null).ToList();
            var ins = insights[0];
            Assert.AreEqual(InsightDirection.Up, ins.Direction, "disabled → warmup 仍 Up(不走 Flat)");
            Assert.AreEqual(1.0, ins.Weight.Value, 0.001, "disabled → warmup dirCoef=1.0 (不走 floor)");
        }

        /// <summary>
        /// 最小 algorithm 桩，提供 algorithm.Time（因子 Compute 需要时间戳）。
        /// 镜像 GoldOvernightPremiumAlgorithmTests.TestAlgorithm 的 SetCurrentTime 模式。
        /// </summary>
        public class TestAlgo : QuantConnect.Algorithm.QCAlgorithm
        {
            public TestAlgo()
            {
                SetTimeZone(TimeZones.Shanghai);
            }

            public void SetCurrentTime(DateTime localTime)
            {
                SetDateTime(localTime.ConvertToUtc(TimeZone));
            }
        }
    }
}
