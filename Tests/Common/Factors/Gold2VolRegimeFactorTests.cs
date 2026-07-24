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
            // volTarget=0.22, alpha=1.0(无平滑): 低波动 baseline → w≈1.0; 大幅 ±5% 交替收益 → σ_ann ↑ → w↓。
            var f = new Gold2VolRegimeFactor(0.94m, 0.22m, 60, 1.0m);
            // 第 1 个 Update 仅 seed _prevClose 不产出收益,故需 warmup+1=61 bar 才能攒满 60 个 warmup 收益并令 _ewmaReady=true。
            // 用微小稳定上涨(1.0001^i)填充 warmup 与额外低波动稳定期,使 _wSmoothPrev 收敛至 ~1.0、_sigma2Prev 收敛至真实低波动 σ²(非 0)。
            for (int i = 0; i < 200; i++) f.Update((decimal)(100.0 * Math.Pow(1.0001, i)), new DateTime(2020,1,2).AddDays(i));
            var baseR = f.Compute(Symbol.Empty, new DateTime(2020,7,20));
            // baseR 是真实低波动基线: RawValue=σ_ann>0(非 0), Value=w_smooth≈1.0。
            Assert.Greater(baseR.RawValue, 0m, "baseR.RawValue 是真实低波动 σ_ann, 非 0");
            Assert.AreEqual(1.0, (double)baseR.Value, 0.01, "低波动 baseline → w≈1.0");
            // 注入 ±5% 交替收益(价格在 105/95 间跳变)→ σ_ann 升至 ~1.57 量级。
            for (int i = 0; i < 60; i++) f.Update(100m * (i % 2 == 0 ? 1.05m : 0.95m), new DateTime(2020,7,21).AddDays(i));
            var spikeR = f.Compute(Symbol.Empty, new DateTime(2020,10,19));
            Assert.Greater(spikeR.RawValue, baseR.RawValue, "波动率上升 σ_ann 增大");
            // w=min(1,σ_target/σ_ann): σ_ann 从 ~0.0016 升至 ~1.57,远超 σ_target=0.22 → w 被显著压低。
            Assert.Less(spikeR.Value, baseR.Value, "σ_ann↑ → w↓ (w=min(1,σ_target/σ_ann))");
            Assert.Less(spikeR.Value, 0.5m, "σ_ann≈1.57 远超 σ_target=0.22 → w<0.5");
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

        // MINOR review fix: 构造函数参数校验(对齐 Gold2TrendFactor.cs:71 的 guard 风格)。
        // 注: C# attribute 不支持 decimal 参数,故用独立 [Test] 而非 [TestCase]。
        [Test]
        public void Constructor_RejectsLambdaOutOfRange()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0m, 0.11m, 60, 0.25m));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(1m, 0.11m, 60, 0.25m));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(-0.1m, 0.11m, 60, 0.25m));
        }

        [Test]
        public void Constructor_RejectsNonPositiveVolTarget()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, 0m, 60, 0.25m));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, -1m, 60, 0.25m));
        }

        [Test]
        public void Constructor_RejectsNonPositiveWarmup()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, 0.11m, 0, 0.25m));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, 0.11m, -1, 0.25m));
        }

        [Test]
        public void Constructor_RejectsAlphaOutOfRange()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, 0.11m, 60, -0.1m));
            Assert.Throws<ArgumentOutOfRangeException>(() => new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 1.1m));
        }

        [Test]
        public void Constructor_AcceptsBoundaryAlpha()
        {
            // alpha=0 与 alpha=1 为闭区间边界,应被接受(不抛异常)。
            Assert.DoesNotThrow(() => new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0m));
            Assert.DoesNotThrow(() => new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 1m));
        }
    }
}
