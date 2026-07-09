using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L4 Risk: 实际利率帽子。RISING_FAST → cap 0.60,否则 1.0。与 ExtremeRisk 串联取 min。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.4。
    /// cap 逻辑直接复用真实 Gold2RealRateCapFactor.Compute(不再保留测试用 ComputeCap 死代码;
    /// 见 Common/Factors/Forward/Gold2RealRateCapFactor.cs)。
    /// </summary>
    public class Gold2RealRateCapModel : RiskManagementModel
    {
        private readonly Gold2RealRateCapFactor _cap;
        private readonly Symbol _gold;

        public string Name => "Gold2RealRateCapModel";

        public Gold2RealRateCapModel(Gold2RealRateCapFactor cap, Symbol gold)
        {
            _cap = cap;
            _gold = gold;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // 直接调用真实因子 wrapper 的 Compute(而非自行重写 regime→cap 逻辑);
            // 与 Gold2RealRateCapFactor 单一事实来源,避免双路分叉。
            var capFactor = _cap.Compute(_gold, algorithm.Time).Value;
            foreach (var t in targets)
            {
                // 只对本模型的 gold 标的施 cap;非 gold 标的直接透传。
                if (t.Symbol != _gold)
                {
                    yield return t;
                    continue;
                }
                decimal w = TargetToWeight(algorithm, t);
                w = Math.Min(w, capFactor);
                // Null-guard: PortfolioTarget.Percent 在 warmup(Price==0)或 percent 越界时返回 null。
                // CompositeRiskManagementModel.ManageRisk 用 DistinctBy(t => t.Symbol) 合并,null 会 NRE,
                // 与 sibling Gold2VolTargetPortfolioModel 一致地跳过。
                var portfolioTarget = PortfolioTarget.Percent(algorithm, t.Symbol, w);
                if (portfolioTarget != null)
                {
                    yield return portfolioTarget;
                }
            }
        }

        private static decimal TargetToWeight(QCAlgorithm algorithm, IPortfolioTarget t)
        {
            if (t.Quantity == 0) return 0m;
            var sec = algorithm.Securities[t.Symbol];
            return sec.Price > 0 ? t.Quantity * sec.Price / algorithm.Portfolio.TotalPortfolioValue : 0m;
        }
    }
}
