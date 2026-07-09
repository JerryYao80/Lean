using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2ExtremeRiskFactorTests
    {
        // 设计 §4.3: Trigger = VIX_t > VIX_P95 OR RVol_60d > RVol_P95
        // P95 用 ≤ t-1 历史(不含当日 VIX/RVol)。VIX 缺失日: 因子用 lastVix 前值填充(从历史中查 Time ≤ time 的最近值)。

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
            // 喂 100 天正常 VIX,历史窗口填满
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            // 第 101 天 VIX 飙升,Compute(t101) 用 ≤ t100 的历史 P95 + lastVix
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
            // 当日 VIX 仍属正常区间,P95 用历史 100 天(不含当日)
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(0m, r.Value, "正常区间不触发");
        }

        [Test]
        public void RVolAboveP95_TriggersEvenWithNormalVix()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) { f.UpdateVix(15m, t0.AddDays(i)); f.UpdateRvol60(0.10m, t0.AddDays(i)); }
            // 第 101 天 RVol 飙升(Compute 时 lastRvol=0.90,P95 用历史 100 天 0.10)
            f.UpdateRvol60(0.90m, t0.AddDays(100));
            f.UpdateVix(15m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "RVol>P95 触发(GPR 代理)");
        }

        // CRITICAL review fix (issue #1+#2): 无前视测试 — Compute(t) 不应读取 t+1 或当日(t)的数据进入 P95。
        // 对齐 Gold2TrendFactorTests.NoLookahead_MA_OnlyUsesPastClose 与 Gold2VolRegimeFactorTests.NoLookahead_EWMA_UsesPrevReturn。
        [Test]
        public void NoLookahead_P95_UsesOnlyHistoryBeforeTime()
        {
            // 历史 100 天 VIX=15;当日 VIX=15(lastVix);Compute(t100) 在 t+1 极端数据注入后应保持不触发。
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            f.UpdateVix(15m, t0.AddDays(100));
            var r1 = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(0m, r1.Value, "baseline: VIX=15=P95 → 不触发(严格 >)");
            // 注入未来(t+1)极端 VIX;若 P95 偷看未来则会改变触发状态。
            f.UpdateVix(9999m, t0.AddDays(101));
            var r2 = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(r1.Value, r2.Value, "Compute(t) 不读 t+1 的 VIX 历史");
        }

        [Test]
        public void NoLookahead_ComputeDoesNotPeekSameDayVix()
        {
            // 关键无前视校验:P95 必须用 ≤ t-1 历史(不含当日 VIX)。
            // 构造场景:历史 100 天全部 VIX=15,当日 VIX=50。
            // 若 P95 含当日 → P95=50 → lastVix(50) > P95(50) = false → 不触发(错误)。
            // 若 P95 不含当日 → P95=15 → lastVix(50) > P95(15) = true → 触发(正确)。
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            f.UpdateVix(50m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "P95 必须不含当日 VIX(lastVix=50 > P95=15 → 触发)");
        }

        // IMPORTANT review fix (issue #4): VIX 缺失日前值填充 — 当日不调用 UpdateVix 时,Compute 用历史中 Time ≤ time 的最近值。
        [Test]
        public void MissingVixDay_ForwardFillsLastVix_NoTrigger()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            // 喂 100 天正常 VIX=15(时间 t0..t99),lastVix 前值=15
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            // 第 101 天(t100)VIX 缺失(不调用 UpdateVix),Compute(t100) 用历史 Time ≤ t100 的最近值=15(t99) → 不触发
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(0m, r.Value, "VIX 缺失日 lastVix 前值填充(=15),正常区间不触发");
            // RawVix/lastVix=15,P95(历史 100 天)=15,严格 > 不触发
        }

        [Test]
        public void MissingVixDay_ForwardFillsExtremeLastVix_Triggers()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            // 喂 99 天正常 VIX=15(t0..t98),第 100 天(t99)VIX 飙升到 50(lastVix 前值=50)
            for (int i = 0; i < 99; i++) f.UpdateVix(15m, t0.AddDays(i));
            f.UpdateVix(50m, t0.AddDays(99));
            // 第 101 天(t100)VIX 缺失,Compute(t100) 用历史 Time ≤ t100 的最近值=50(t99)
            // P95 = 历史 Time < t100 = t0..t99 共 100 个值,其中 t99=50 → P95 仍=15(99 个 15 + 1 个 50,sorted[94]=15)
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "VIX 缺失日 lastVix 前值填充=50 > P95=15 → 触发");
        }

        // MINOR review fix (issue #6): Percentile 边界用例(空、单元素、q=0/1)。
        [Test]
        public void Percentile_EmptyInput_ReturnsMaxValue()
        {
            // 空输入返回 decimal.MaxValue(安全默认:使任何 current 值不触发)
            var p95 = Gold2ExtremeRiskFactor.Percentile(new decimal[0], 0.95);
            Assert.AreEqual(decimal.MaxValue, p95, "空输入返回 MaxValue(不触发)");
        }

        [Test]
        public void Percentile_SingleElement_ReturnsThatElement()
        {
            // 单元素:任何 q 都返回该元素
            var xs = new decimal[] { 42m };
            Assert.AreEqual(42m, Gold2ExtremeRiskFactor.Percentile(xs, 0.0), "q=0 单元素返回自身");
            Assert.AreEqual(42m, Gold2ExtremeRiskFactor.Percentile(xs, 0.5), "q=0.5 单元素返回自身");
            Assert.AreEqual(42m, Gold2ExtremeRiskFactor.Percentile(xs, 1.0), "q=1 单元素返回自身");
        }

        [Test]
        public void Percentile_Q0_ReturnsMinimum()
        {
            // q=0:idx=ceil(0*n)-1=-1 → clamp 到 0 → 最小值
            var xs = new decimal[] { 10m, 20m, 30m, 40m, 50m };
            Assert.AreEqual(10m, Gold2ExtremeRiskFactor.Percentile(xs, 0.0), "q=0 返回最小值");
        }

        [Test]
        public void Percentile_Q1_ReturnsMaximum()
        {
            // q=1:idx=ceil(1*n)-1=n-1 → 最大值
            var xs = new decimal[] { 10m, 20m, 30m, 40m, 50m };
            Assert.AreEqual(50m, Gold2ExtremeRiskFactor.Percentile(xs, 1.0), "q=1 返回最大值");
        }

        [Test]
        public void Percentile_Q95_NearestRank()
        {
            // 100 个元素全为 15:ceil(0.95*100)-1=94 → sorted[94]=15
            var xs = new List<decimal>();
            for (int i = 0; i < 100; i++) xs.Add(15m);
            Assert.AreEqual(15m, Gold2ExtremeRiskFactor.Percentile(xs, 0.95), "100 个相同值 P95=该值");
        }

        [Test]
        public void Percentile_TwoElements_BoundaryClamp()
        {
            // 2 个元素:q=0.95 → idx=ceil(0.95*2)-1=ceil(1.9)-1=2-1=1 → 最大值
            var xs = new decimal[] { 10m, 90m };
            Assert.AreEqual(90m, Gold2ExtremeRiskFactor.Percentile(xs, 0.95), "2 元素 P95 clamp 到最大值");
        }

        // MINOR review fix (issue #5): 命名常量校验 — 默认窗口为命名常量且可通过构造函数覆盖。
        [Test]
        public void Constructor_AcceptsDefaultConstants()
        {
            Assert.DoesNotThrow(() => new Gold2ExtremeRiskFactor());
            var f = new Gold2ExtremeRiskFactor();
            // 默认使用命名常量 DefaultVixWindow=1260, DefaultMinWindow=252
            Assert.AreEqual(Gold2ExtremeRiskFactor.DefaultVixWindow, 1260);
            Assert.AreEqual(Gold2ExtremeRiskFactor.DefaultMinWindow, 252);
        }

        [Test]
        public void Constructor_RejectsNonPositiveVixWindow()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2ExtremeRiskFactor(vixWindow: 0, minWindow: 1));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2ExtremeRiskFactor(vixWindow: -1, minWindow: 1));
        }

        [Test]
        public void Constructor_RejectsNonPositiveMinWindow()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2ExtremeRiskFactor(vixWindow: 10, minWindow: 0));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2ExtremeRiskFactor(vixWindow: 10, minWindow: -1));
        }

        [Test]
        public void Constructor_RejectsMinWindowGreaterThanVixWindow()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2ExtremeRiskFactor(vixWindow: 100, minWindow: 200));
        }
    }
}
