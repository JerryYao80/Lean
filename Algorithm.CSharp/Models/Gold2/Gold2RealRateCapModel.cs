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
            var capFactor = _cap.Compute(_gold, algorithm.Time).Value;
            foreach (var t in targets)
            {
                decimal w = TargetToWeight(algorithm, t);
                w = Math.Min(w, capFactor);
                yield return PortfolioTarget.Percent(algorithm, t.Symbol, w);
            }
        }

        /// <summary>纯逻辑(测试用): 直接从 inner regime factor 算 cap。</summary>
        public static decimal ComputeCap(GoldRealRateRegimeFactor inner, Symbol gold, DateTime time, decimal risingFastCap)
        {
            var regime = (GoldRegime)(decimal)inner.Compute(gold, time).Value;
            return regime == GoldRegime.RISING_FAST ? risingFastCap : 1.0m;
        }

        private static decimal TargetToWeight(QCAlgorithm algorithm, IPortfolioTarget t)
        {
            if (t.Quantity == 0) return 0m;
            var sec = algorithm.Securities[t.Symbol];
            return sec.Price > 0 ? t.Quantity * sec.Price / algorithm.Portfolio.TotalPortfolioValue : 0m;
        }
    }
}
