using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L4 Risk: 极端开关。Triggered → cap 到 extremeCap(0.30),硬执行绕过 dead-zone。
    /// 与 RealRateCapModel 串联取 min。详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.4。
    /// </summary>
    public class Gold2ExtremeRiskModel : RiskManagementModel
    {
        private readonly Gold2ExtremeRiskFactor _ext;
        private readonly Symbol _gold;
        private readonly decimal _extremeCap;

        public string Name => "Gold2ExtremeRiskModel";

        public Gold2ExtremeRiskModel(Gold2ExtremeRiskFactor ext, Symbol gold, decimal extremeCap)
        {
            _ext = ext;
            _gold = gold;
            _extremeCap = extremeCap;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // 因子值直接判断,不做不必要的 (int) cast(因子返回 triggered ? 1m : 0m)。
            var triggered = _ext.Compute(_gold, algorithm.Time).Value == 1m;
            foreach (var t in targets)
            {
                // 只对本模型的 gold 标的施 cap;非 gold 标的直接透传(若 universe 扩展不会误伤)。
                if (t.Symbol != _gold)
                {
                    yield return t;
                    continue;
                }
                decimal w = TargetToWeight(algorithm, t);
                w = ApplyCap(w, triggered, _extremeCap);
                // Null-guard: PortfolioTarget.Percent 在 warmup(Price==0)或 percent 越界
                // (Min/MaxAbsolutePortfolioTargetPercentage)或未知 symbol 时返回 null。
                // CompositeRiskManagementModel.ManageRisk 用 DistinctBy(t => t.Symbol) 合并,null 会导致 NRE,
                // 与 sibling Gold2VolTargetPortfolioModel 一致地跳过。
                var portfolioTarget = PortfolioTarget.Percent(algorithm, t.Symbol, w);
                if (portfolioTarget != null)
                {
                    yield return portfolioTarget;
                }
            }
        }

        /// <summary>纯逻辑: triggered → min(w, cap),否则 w。</summary>
        public static decimal ApplyCap(decimal weight, bool triggered, decimal cap)
            => triggered ? Math.Min(weight, cap) : weight;

        private static decimal TargetToWeight(QCAlgorithm algorithm, IPortfolioTarget t)
        {
            if (t.Quantity == 0) return 0m;
            var sec = algorithm.Securities[t.Symbol];
            return sec.Price > 0 ? t.Quantity * sec.Price / algorithm.Portfolio.TotalPortfolioValue : 0m;
        }
    }
}
