using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L3 Portfolio: target = w_smooth × dirCoef(insight.Weight) + dead-zone。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.3。
    /// </summary>
    public class Gold2VolTargetPortfolioModel : PortfolioConstructionModel
    {
        private readonly Gold2VolRegimeFactor _vol;
        private readonly Symbol _gold;
        private readonly decimal _threshold;
        private decimal _lastActualWeight;

        public Gold2VolTargetPortfolioModel(Gold2VolRegimeFactor vol, Symbol gold, decimal threshold)
        {
            _vol = vol;
            _gold = gold;
            _threshold = threshold;
        }

        public override IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
        {
            if (insights.Length == 0)
            {
                yield break;
            }

            var ins = insights[0];
            decimal wSmooth = _vol.Compute(_gold, algorithm.Time).Value;
            decimal dirCoef = (decimal)(ins.Weight ?? 0);
            decimal target = wSmooth * dirCoef;
            target = ApplyDeadZone(_lastActualWeight, target, _threshold);
            _lastActualWeight = target;
            yield return PortfolioTarget.Percent(algorithm, _gold, target);
        }

        /// <summary>dead-zone 纯逻辑: |target-last| < threshold → 维持 last,否则更新。</summary>
        public static decimal ApplyDeadZone(decimal lastActual, decimal target, decimal threshold)
            => Math.Abs(target - lastActual) < threshold ? lastActual : target;
    }
}
