using System;
using System.Linq;
using NodaTime;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
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
    /// L4 Risk models 测试。镜像 Gold2VolTargetPortfolioModelTests 范式:构造真实 TestAlgo
    /// (已定价 518880 + 现金) + 真实因子(经 UpdateVix/InjectRegime 种子化) + 真实 PortfolioTarget
    /// 输入,对 ManageRisk → PortfolioTarget.Quantity 端到端断言。覆盖: (a) ExtremeRisk 触发 cap 0.30、
    /// (b) ExtremeRisk 未触发透传、(c) RealRate RISING_FAST cap 0.60、(d) RealRate STABLE 不 cap、
    /// (e) Composite(extreme, realrate) 串联取 min=0.30、(f) VolTarget→ExtremeRisk 链极端触发绕过 dead-zone、
    /// (g) warmup(Price==0) null-guard 不抛 NRE、(h) 非 gold 标的透传不被误 cap。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.4 + §8.8。
    /// </summary>
    [TestFixture]
    public class Gold2RiskModelsTests
    {
        private static readonly Symbol Gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
        private const decimal Price = 10m;
        private const decimal Cash = 100000m;
        private const decimal Tpv = Cash; // 无持仓 → TPV == Cash

        // 构建 TestAlgo: 加载 DataManagerStub + 已定价的 518880 + 现金。
        // 镜像 Gold2VolTargetPortfolioModelTests.BuildAlgo + BaseWeightingPortfolioConstructionModelTests 装配。
        private static TestAlgo BuildAlgo(DateTime localTime, bool setPrice = true)
        {
            var algo = new TestAlgo();
            algo.SetCurrentTime(localTime);
            // 关闭 FreePortfolioValuePercentage(默认 0.0025): Composite 管线中每个 risk model 都做
            // quantity→weight→Percent 往返,FreeBuffer 会被重复扣减导致 quantity 漂移。
            // FreeBuffer 是 LEAN 内部的安全垫,非 risk-model 语义,关闭后 "取 min" 断言确定可比。
            algo.Settings.FreePortfolioValuePercentage = 0m;
            algo.SubscriptionManager.SetDataManager(new DataManagerStub(algo));
            var security = new Equity(
                Gold,
                SecurityExchangeHours.AlwaysOpen(DateTimeZone.Utc),
                new Cash(Currencies.USD, 0, 1),
                SymbolProperties.GetDefault(Currencies.USD),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
            // InteractiveBrokersFeeModel 不识别 SSE 市场,覆盖为常量费模型避免 KeyNotFound。
            security.FeeModel = new ConstantFeeModel(0);
            if (setPrice)
            {
                security.SetMarketPrice(new Tick(algo.Time, Gold, Price, Price));
            }
            algo.Securities.Add(Gold, security);
            algo.Portfolio.SetCash(Cash);
            return algo;
        }

        // 种子化真实 Gold2ExtremeRiskFactor 至触发态: 100 天正常 VIX=15 + 当日 VIX=50 → lastVix=50 > P95=15。
        // 复用 Gold2ExtremeRiskFactorTests.VixAboveP95_Triggers 的种子序列。
        private static Gold2ExtremeRiskFactor BuildTriggeredFactor(DateTime algoTime)
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, algoTime.AddDays(-(100 - i)));
            f.UpdateVix(50m, algoTime);
            return f;
        }

        // 种子化真实 Gold2ExtremeRiskFactor 至未触发态: 100 天 VIX=15 + 当日 VIX=15 → 15 > 15 严格 false。
        private static Gold2ExtremeRiskFactor BuildQuietFactor(DateTime algoTime)
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, algoTime.AddDays(-(100 - i)));
            f.UpdateVix(15m, algoTime);
            return f;
        }

        // 构造代表给定 weight 的真实 PortfolioTarget(Price=10, TPV=100000 → quantity = weight*TPV/Price)。
        private static IPortfolioTarget TargetAtWeight(decimal weight)
            => new PortfolioTarget(Gold, weight * Tpv / Price);

        // 期望: 对给定 percent,PortfolioTarget.Percent 产出的 quantity(吸收 FreeBuffer/Leverage 细节)。
        private static decimal ExpectedQuantityForPercent(TestAlgo algo, decimal percent)
            => ((PortfolioTarget)PortfolioTarget.Percent(algo, Gold, percent)).Quantity;

        // (a) 设计 §5.4+§8.8: ExtremeRisk 触发 → cap 0.30。真实因子(UpdateVix 种子化)+ 真实 target
        // (weight=0.80) → ManageRisk → PortfolioTarget.Percent(0.30) 的 quantity。
        [Test]
        public void ExtremeRisk_Triggered_CapsTo03()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var ext = BuildTriggeredFactor(algo.Time);
            // 前置: 因子确实触发(Value==1m,不做 (int) cast)。
            Assert.AreEqual(1m, ext.Compute(Gold, algo.Time).Value, "前置: VIX 飙升 → 触发");

            var model = new Gold2ExtremeRiskModel(ext, Gold, extremeCap: 0.30m);
            var input = new[] { TargetAtWeight(0.80m) };
            var output = model.ManageRisk(algo, input).Single();

            Assert.AreEqual(Gold, output.Symbol);
            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.30m), output.Quantity,
                "triggered → min(0.80, 0.30)=0.30 → PortfolioTarget.Percent(0.30)");
            Assert.Less(output.Quantity, ((PortfolioTarget)input[0]).Quantity, "cap 后 quantity 下降");
        }

        // (b) 设计 §5.4: ExtremeRisk 未触发 → 透传原 weight。
        [Test]
        public void ExtremeRisk_NotTriggered_PassesThrough()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var ext = BuildQuietFactor(algo.Time);
            Assert.AreEqual(0m, ext.Compute(Gold, algo.Time).Value, "前置: 正常 VIX → 不触发");

            var model = new Gold2ExtremeRiskModel(ext, Gold, extremeCap: 0.30m);
            var input = new[] { TargetAtWeight(0.80m) };
            var output = model.ManageRisk(algo, input).Single();

            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.80m), output.Quantity,
                "未触发 → 透传 0.80 → PortfolioTarget.Percent(0.80)");
        }

        // (c) 设计 §4.4+§8.8: RealRate RISING_FAST → cap 0.60。真实 Gold2RealRateCapFactor
        // (InjectRegime 种子化)+ 真实 target(weight=0.80)→ ManageRisk → Percent(0.60)。
        // 直接断言真实因子 wrapper(不再用死代码 ComputeCap)。
        [Test]
        public void RealRate_RisingFast_CapsTo06()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Gold, GoldRegime.RISING_FAST);
            var cap = new Gold2RealRateCapFactor(inner, 0.60m);
            // 前置: 真实因子 wrapper Compute → 0.60(单一事实来源)。
            Assert.AreEqual(0.60m, cap.Compute(Gold, algo.Time).Value, "前置: RISING_FAST → cap 0.60");

            var model = new Gold2RealRateCapModel(cap, Gold);
            var input = new[] { TargetAtWeight(0.80m) };
            var output = model.ManageRisk(algo, input).Single();

            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.60m), output.Quantity,
                "RISING_FAST → min(0.80, 0.60)=0.60 → PortfolioTarget.Percent(0.60)");
        }

        // (d) 设计 §4.4: RealRate STABLE → cap 1.0 → 不 cap,透传 0.80。
        [Test]
        public void RealRate_Stable_NoCap()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Gold, GoldRegime.STABLE);
            var cap = new Gold2RealRateCapFactor(inner, 0.60m);
            Assert.AreEqual(1.0m, cap.Compute(Gold, algo.Time).Value, "前置: STABLE → cap 1.0");

            var model = new Gold2RealRateCapModel(cap, Gold);
            var input = new[] { TargetAtWeight(0.80m) };
            var output = model.ManageRisk(algo, input).Single();

            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.80m), output.Quantity,
                "STABLE → min(0.80, 1.0)=0.80 → 透传");
        }

        // (e) 设计 §5.4+§8.8: 串联取 min。真实 CompositeRiskManagementModel(extreme, realrate)
        // .ManageRisk 端到端: extreme=0.30, realrate=0.60 → final=min(0.30,0.60)=0.30。
        // 不再用 Math.Min 静态断言,走真实 Composite 管线(DistinctBy 合并 + 顺序应用)。
        [Test]
        public void BothRisks_TakeMin_ThroughCompositePipeline()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            var ext = BuildTriggeredFactor(algo.Time);
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Gold, GoldRegime.RISING_FAST);
            var cap = new Gold2RealRateCapFactor(inner, 0.60m);

            var composite = new CompositeRiskManagementModel(
                new Gold2ExtremeRiskModel(ext, Gold, 0.30m),
                new Gold2RealRateCapModel(cap, Gold));

            var input = new[] { TargetAtWeight(0.80m) };
            var output = composite.ManageRisk(algo, input).Single();

            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.30m), output.Quantity,
                "Composite(extreme=0.30, realrate=0.60) → min=0.30 → PortfolioTarget.Percent(0.30)");
        }

        // (f) 设计 §5.4+§8.8: 极端触发绕过 dead-zone。真实 VolTarget→ExtremeRisk 链:
        // VolTarget(dead-zone 维持 0.80) → ExtremeRisk(triggered) 强制 cap 0.30。
        // 不再静态组合 ApplyDeadZone+ApplyCap,走真实模型链。
        [Test]
        public void ExtremeRisk_OverridesDeadZone_ThroughVolTargetChain()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            // 种子化 VolRegimeFactor 至低波动(w_smooth≈1.0),使 dirCoef=0.80 → target=0.80。
            var vol = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 200; i++) vol.Update((decimal)(100.0 * Math.Pow(1.001, i)), algo.Time.AddDays(-(200 - i)));
            var volTarget = new Gold2VolTargetPortfolioModel(vol, Gold, threshold: 0.05m);

            // 首次: dirCoef=0.80 → target=0.80(last=0 无 dead-zone,更新 last=0.80)。
            var firstTarget = volTarget.CreateTargets(algo, new[] { Insight.Price(Gold, Time.OneDay, InsightDirection.Up, weight: 0.80) }).Single();
            // 二次: dirCoef=0.81 → |0.81-0.80|=0.01 < 0.05 → dead-zone 维持 last=0.80。
            var deadZoneHeldTarget = volTarget.CreateTargets(algo, new[] { Insight.Price(Gold, Time.OneDay, InsightDirection.Up, weight: 0.81) }).Single();
            Assert.AreEqual(firstTarget.Quantity, deadZoneHeldTarget.Quantity, "前置: dead-zone 维持 last=0.80");

            // 喂入 ExtremeRisk: triggered → 强制 cap 0.30(绕过 dead-zone 持有的 0.80)。
            var ext = BuildTriggeredFactor(algo.Time);
            var extremeModel = new Gold2ExtremeRiskModel(ext, Gold, 0.30m);
            var afterRisk = extremeModel.ManageRisk(algo, new[] { deadZoneHeldTarget }).Single();

            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.30m), afterRisk.Quantity,
                "极端触发绕过 dead-zone → cap 0.30");
            Assert.Less(((PortfolioTarget)afterRisk).Quantity, ((PortfolioTarget)deadZoneHeldTarget).Quantity,
                "cap 后 quantity 低于 dead-zone 持有量");
        }

        // (g) warmup(Price==0) null-guard: PortfolioTarget.Percent 返回 null → 跳过 yield,不抛 NRE。
        // 镜像 Gold2VolTargetPortfolioModelTests.Warmup_PriceZero_NullGuardSkipsYield_NoNRE。
        [Test]
        public void Warmup_PriceZero_NullGuardSkipsYield_NoNRE()
        {
            // setPrice=false → security.Price 保持 0(PortfolioTarget.Percent 返回 null)。
            var algo = BuildAlgo(new DateTime(2020, 7, 20), setPrice: false);
            var ext = BuildTriggeredFactor(algo.Time);
            var model = new Gold2ExtremeRiskModel(ext, Gold, 0.30m);

            // 非 0 quantity 的 target:TargetToWeight 因 Price==0 返回 0 → Percent(0) 因 Price==0 返回 null → 跳过。
            var input = new[] { TargetAtWeight(0.80m) };
            // 不得抛 NRE,不得 yield null。
            var output = model.ManageRisk(algo, input).ToList();
            Assert.AreEqual(0, output.Count, "Price==0 → PortfolioTarget.Percent null → null-guard 跳过 yield(无 NRE)");
        }

        // (h) 非 gold 标的透传: target filtering 修复 — 只对 t.Symbol==_gold 施 cap,其他标的直接 yield。
        // 防止 universe 扩展时非 gold 标的被 gold 专属因子误 cap。
        [Test]
        public void NonGoldTarget_PassesThrough_Uncapped()
        {
            var algo = BuildAlgo(new DateTime(2020, 7, 20));
            // 额外加一个非 gold 标的(已定价)。
            var other = Symbol.Create("000001", SecurityType.Equity, Market.SSE);
            var otherSec = new Equity(
                other,
                SecurityExchangeHours.AlwaysOpen(DateTimeZone.Utc),
                new Cash(Currencies.USD, 0, 1),
                SymbolProperties.GetDefault(Currencies.USD),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
            otherSec.FeeModel = new ConstantFeeModel(0);
            otherSec.SetMarketPrice(new Tick(algo.Time, other, Price, Price));
            algo.Securities.Add(other, otherSec);

            var ext = BuildTriggeredFactor(algo.Time); // gold 因子触发
            var model = new Gold2ExtremeRiskModel(ext, Gold, 0.30m);

            var goldTarget = new PortfolioTarget(Gold, 8000m);   // weight 0.80
            var otherTarget = new PortfolioTarget(other, 8000m); // 非 gold,应透传不被 cap
            var output = model.ManageRisk(algo, new[] { goldTarget, otherTarget }).ToList();

            // gold 被 cap 到 0.30;other 透传保持 8000(quantity 不变)。
            var outGold = output.Single(t => t.Symbol == Gold);
            var outOther = output.Single(t => t.Symbol == other);
            Assert.AreEqual(ExpectedQuantityForPercent(algo, 0.30m), outGold.Quantity, "gold 被 cap 到 0.30");
            Assert.AreEqual(8000m, outOther.Quantity, "非 gold 标的透传,不被 gold 专属因子 cap");
        }

        /// <summary>最小 algorithm 桩:提供 algorithm.Time + Securities + Portfolio。
        /// 镜像 Gold2VolTargetPortfolioModelTests.TestAlgo 的 SetCurrentTime 模式。</summary>
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
