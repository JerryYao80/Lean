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
        // C2: optional effective cap override. When >0 it overrides the factor's
        // cap value (used by G2 shaping: effRealRateCap = base_realrate_cap / penalty).
        // G0 path uses the legacy 2-arg ctor -> _effRealRateCap=0 -> no override ->
        // behavior byte-identical to before.
        private readonly decimal _effRealRateCap;

        public string Name => "Gold2RealRateCapModel";

        public Gold2RealRateCapModel(Gold2RealRateCapFactor cap, Symbol gold)
        {
            _cap = cap;
            _gold = gold;
            _effRealRateCap = 0m;
        }

        public Gold2RealRateCapModel(Gold2RealRateCapFactor cap, Symbol gold, decimal effRealRateCap)
            : this(cap, gold)
        {
            _effRealRateCap = effRealRateCap;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // C2: the realrate cap applies ONLY in RISING_FAST (mirrors
            // Gold2RealRateCapFactor.Compute: RISING_FAST -> risingFastCap, else 1.0).
            // Compute the factor once for the regime + regime-dependent cap.
            // In RISING_FAST: use the effective-cap override when set (G2 shaping:
            // effRealRateCap = risingFastCap / penalty), else the factor's cap.
            // In non-rising regimes: no cap (1.0) — the shaping penalty must NOT
            // leak a cap into regimes that the factor leaves uncapped.
            // G0 byte-identical: 2-arg ctor -> _effRealRateCap=0 -> no override ->
            // factor.Value; OR 3-arg ctor with penalty=1.0 -> effRealRateCap=
            // risingFastCap = factor.Value. Either way capFactor == factor.Value.
            var computed = _cap.Compute(_gold, algorithm.Time);
            var regime = (GoldRegime)(decimal)computed.RawValue;
            var capFactor = regime == GoldRegime.RISING_FAST
                ? (_effRealRateCap > 0m ? _effRealRateCap : computed.Value)
                : 1.0m;
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
