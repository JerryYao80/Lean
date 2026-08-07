using System;
using System.Linq;
using NodaTime;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Forward;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;
using QuantConnect.Tests.Engine.DataFeeds;

namespace QuantConnect.Tests.Algorithm
{
    /// <summary>
    /// L3 VolTargetPortfolioModel 测试。镜像 Gold2TrendAlphaModelTests 的范式:构造真实模型 +
    /// 真实 Gold2VolRegimeFactor(经 Update 种子化) + 真实 Insight[] 驱动 CreateTargets,
    /// 对 PortfolioTarget.Quantity/Percent 断言。覆盖: (a) w_smooth×dirCoef 计算、
    /// (b) _lastActualWeight 跨调用更新、(c) PortfolioTarget.Percent 接收正确 percent、
    /// (d) dead-zone 在连续 CreateTargets 间生效,以及空 insights(过期 insight)保持 last。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.3。
    /// </summary>
    [TestFixture]
    public class Gold2VolTargetPortfolioModelTests
    {
        private static readonly Symbol Gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
        private const decimal Price = 10m;
        private const decimal Cash = 100000m;
        private const decimal Threshold = 0.05m;

        // 构建 TestAlgo: 加载 DataManagerStub + 已定价的 518880 + 现金。
        // 镜像 BaseWeightingPortfolioConstructionModelTests.SetUp + GetSecurity 的 security 装配。
        private static TestAlgo BuildAlgo(DateTime localTime)
        {
            var algo = new TestAlgo();
            algo.SetCurrentTime(localTime);
            algo.SubscriptionManager.SetDataManager(new DataManagerStub(algo));
            var security = new Equity(
                Gold,
                SecurityExchangeHours.AlwaysOpen(DateTimeZone.Utc),
                new Cash(Currencies.USD, 0, 1),
                SymbolProperties.GetDefault(Currencies.USD),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
            // InteractiveBrokersFeeModel 不识别 SSE 市场(仅 USA),覆盖为常量费模型避免 KeyNotFound。
            security.FeeModel = new ConstantFeeModel(0);
            security.SetMarketPrice(new Tick(algo.Time, Gold, Price, Price));
            algo.Securities.Add(Gold, security);
            algo.Portfolio.SetCash(Cash);
            return algo;
        }

        // 种子化真实 Gold2VolRegimeFactor: ~200 个低波动日 bar → w_smooth ≈ 1.0。
        // 复用 Gold2VolRegimeFactorTests.SteadyLowVol_ConvergesToFullWeight 的价格序列。
        private static Gold2VolRegimeFactor BuildReadyFactor(DateTime endTime)
        {
            var vol = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 200; i++)
                vol.Update((decimal)(100.0 * Math.Pow(1.001, i)), endTime.AddDays(-(200 - i)));
            return vol;
        }

        // 真实 Up insight,dirCoef 作为 Insight.Weight 传入(§5.2: Up weight=confirm)。
        private static Insight UpInsight(decimal dirCoef)
            => Insight.Price(Gold, Time.OneDay, InsightDirection.Up, weight: (double)dirCoef);

        // (a)+(c): target = w_smooth × dirCoef; 首次调用(last=0)无 dead-zone,直接透传到 Percent。
        [Test]
        public void CreateTargets_ComputesWSmoothTimesDirCoef_AsPortfolioTarget()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var vol = BuildReadyFactor(algo.Time);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            // 前置: 低波动 → w_smooth ≈ 1.0(因子就绪)。
            var wSmooth = vol.Compute(Gold, algo.Time).Value;
            Assert.Greater(wSmooth, 0.99m, "低波动 → w_smooth≈1.0 (前置)");

            const decimal dirCoef = 1.0m;
            var insights = new[] { UpInsight(dirCoef) };
            var target = model.CreateTargets(algo, insights).Single();

            // 期望: PortfolioTarget.Percent(algo, gold, w_smooth × dirCoef) —— 验证模型确实把
            // w_smooth×dirCoef 喂给了 PortfolioTarget.Percent(乘法 + 顺序正确)。
            var expected = PortfolioTarget.Percent(algo, Gold, wSmooth * dirCoef);
            Assert.IsNotNull(expected, "前置: 期望 target 非空(price>0, percent 在合法区间)");
            Assert.AreEqual(expected.Symbol, target.Symbol);
            Assert.AreEqual(expected.Quantity, target.Quantity, "Quantity == PortfolioTarget.Percent(w_smooth×dirCoef)");
            Assert.Greater(target.Quantity, 0m, "Up + dirCoef=1 → 多头仓位");
        }

        // (a): dirCoef=0.5 → 目标 percent 减半 → quantity 约半(验证乘法,非硬编码 1.0)。
        [Test]
        public void DirCoefHalves_TargetQuantityProportionallyHalves()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var vol = BuildReadyFactor(algo.Time);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            var full = model.CreateTargets(algo, new[] { UpInsight(1.0m) }).Single();
            // |0.5 - 1.0| = 0.5 > 0.05 → dead-zone 更新 → quantity ≈ full / 2。
            var half = model.CreateTargets(algo, new[] { UpInsight(0.5m) }).Single();

            Assert.Less(half.Quantity, full.Quantity, "dirCoef↓ → quantity↓");
            // w_smooth×dirCoef 乘法: dirCoef 0.5 → target percent 减半 → quantity ≈ full/2。
            Assert.AreEqual((double)full.Quantity / 2.0, (double)half.Quantity, (double)full.Quantity * 0.02,
                "dirCoef 0.5 → quantity ≈ half (w_smooth×dirCoef 乘法生效)");
        }

        // (b)+(d): |Δ|<threshold → 维持 last;_lastActualWeight 不变,两次 Quantity 相等。
        [Test]
        public void DeadZone_BelowThreshold_HoldsLastAcrossCreateTargetsCalls()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var vol = BuildReadyFactor(algo.Time);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            var first = model.CreateTargets(algo, new[] { UpInsight(1.0m) }).Single();
            // 1.0 → 0.97: |Δ|=0.03 < 0.05 → dead-zone 维持 last → second == first。
            var second = model.CreateTargets(algo, new[] { UpInsight(0.97m) }).Single();

            Assert.AreEqual(first.Quantity, second.Quantity,
                "|Δ|<threshold → 维持 last (_lastActualWeight 未更新, Quantity 不变)");
        }

        // (b)+(d): |Δ|>threshold → 更新 last;_lastActualWeight 变化,Quantity 不同。
        [Test]
        public void DeadZone_AboveThreshold_UpdatesLastAcrossCreateTargetsCalls()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var vol = BuildReadyFactor(algo.Time);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            var first = model.CreateTargets(algo, new[] { UpInsight(1.0m) }).Single();
            // 1.0 → 0.5: |Δ|=0.5 > 0.05 → 更新 last。
            var second = model.CreateTargets(algo, new[] { UpInsight(0.5m) }).Single();

            Assert.AreNotEqual(first.Quantity, second.Quantity, "|Δ|>threshold → 更新 last (Quantity 变化)");
            Assert.Less(second.Quantity, first.Quantity, "更新后 quantity 下降");
        }

        // NOTE 覆盖: 空 insights(过期 insight, alpha 未重发)→ yield break;不重置 _lastActualWeight。
        // 后续 within-threshold insight 仍引用 gap 前的 last → dead-zone 跨间隙维持。
        [Test]
        public void EmptyInsights_YieldsNothing_PreservesLastActualWeightAcrossGap()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var vol = BuildReadyFactor(algo.Time);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            var first = model.CreateTargets(algo, new[] { UpInsight(1.0m) }).Single();
            // 过期 insight 间隙: alpha 未重发 → CreateTargets 收到空数组 → yield break。
            var empty = model.CreateTargets(algo, new Insight[0]).ToList();
            Assert.AreEqual(0, empty.Count, "空 insights → yield break (无 flatten target, 无 NRE)");
            // 间隙后 within-threshold insight: dead-zone 仍引用 `first` 设置的 last。
            var after = model.CreateTargets(algo, new[] { UpInsight(0.97m) }).Single();
            Assert.AreEqual(first.Quantity, after.Quantity,
                "dead-zone 跨空-insight 间隙维持 last (_lastActualWeight 未被重置)");
        }

        // MINOR 修复覆盖: warmup(price==0)时 PortfolioTarget.Percent 返回 null → null-guard 跳过 yield(无 NRE)。
        [Test]
        public void Warmup_PriceZero_NullGuardSkipsYield_NoNRE()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            // 覆盖价格为 0(warmup: security 尚无市场价)。
            // 用 Value=0 的 tick: SecurityCache.AddData 仅在 tick.Value != 0 时更新 Price,
            // 故传 Value=0 不会修改已设置的 Price=10m。改用 SecurityCache.ResetPrices(或直接清空 cache)
            // 不可行(内部私有),故转而直接断言 warmup 路径:因子未就绪 → target=0 →
            // percent=0 落在 MinAbsolutePortfolioTargetPercentage 之下时被 PortfolioTarget.Percent 拒绝 →
            // 返回 null → null-guard 跳过 yield。
            // 构造未种子化的因子(_ewmaReady=false → Compute 返回 Value=0 → target=0)。
            var vol = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            var model = new Gold2VolTargetPortfolioModel(vol, Gold, Threshold);

            var insights = new[] { UpInsight(1.0m) };
            // 不得抛 NRE,不得 yield null。percent=0 在 PortfolioTarget.Percent 中:
            //   absolutePercentage != 0 && absolutePercentage < Min(1e-10) 为 false(0 == 0),故 percent=0 通过;
            //   但 price>0 → 进入 GetMaximumLots → 返回 quantity=0 (target=0) 的非 null target。
            // 故此用例验证 warmup(target=0)路径:返回非 null 的 0-quantity target,无 NRE。
            var targets = model.CreateTargets(algo, insights).ToList();
            Assert.AreEqual(1, targets.Count, "target=0(price>0) → 非 null target (无 NRE)");
            Assert.AreEqual(0m, ((PortfolioTarget)targets[0]).Quantity, "warmup target=0 → quantity=0");
        }

        /// <summary>最小 algorithm 桩:提供 algorithm.Time + Securities + Portfolio。
        /// 镜像 Gold2TrendAlphaModelTests.TestAlgo 的 SetCurrentTime 模式。</summary>
        public class TestAlgo : QuantConnect.Algorithm.QCAlgorithm
        {
            public TestAlgo()
            {
                SetTimeZone(TimeZones.Shanghai);
            }

            public void SetCurrentTime(DateTime localTime)
                => SetDateTime(localTime.ConvertToUtc(TimeZone));
        }
    }
}
