using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Factors.Forward;

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

        /// <summary>Last post-deadzone target weight. Review adapter reads this for the
        /// vol_target-layer attribution (spec §3.3). 0 before first CreateTargets.</summary>
        public decimal LastActualWeight => _lastActualWeight;

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
            // Null-guard: PortfolioTarget.Percent returns null during warmup (Price==0) or
            // when the percent falls outside Settings.Min/MaxAbsolutePortfolioTargetPercentage.
            // The base CreateTargets filters these via `if (target != null) targets.Add(target)`;
            // mirror that here so the override never yields a null into the enumerable.
            var portfolioTarget = PortfolioTarget.Percent(algorithm, _gold, target);
            if (portfolioTarget != null)
            {
                yield return portfolioTarget;
            }
        }

        /// <summary>dead-zone 纯逻辑: |target-last| < threshold → 维持 last,否则更新。
        /// internal + InternalsVisibleTo(QuantConnect.Tests) 仅供单元测试可达,无运行期复用消费者
        /// (对照 Gold2VolRegimeFactor.Variance 有明确的"供后续任务复用"语义)。</summary>
        internal static decimal ApplyDeadZone(decimal lastActual, decimal target, decimal threshold)
            => Math.Abs(target - lastActual) < threshold ? lastActual : target;
    }
}
