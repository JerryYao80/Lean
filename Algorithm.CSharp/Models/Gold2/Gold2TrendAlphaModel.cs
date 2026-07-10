using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L2 Alpha: 消费 Gold2TrendFactor。dir&gt;0→Up weight=confirm;否则 Flat weight=floor。
    /// long-only(518880 不可做空,空头也走 Flat 保留底仓)。
    /// Update 不读 Slice —— 因子状态由 Strategy OnData 推进;null Slice 安全。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.2。
    /// </summary>
    public class Gold2TrendAlphaModel : AlphaModel
    {
        private readonly Gold2TrendFactor _trend;
        private readonly Symbol _gold;
        private readonly decimal _floor;
        private readonly bool _disabled;

        /// <summary>Last dirCoef emitted (insight.Weight). Review adapter reads this for the
        /// trend-layer attribution (spec §3.3). 0 before first Update.</summary>
        public decimal LastDirCoef;

        public override string Name => "Gold2TrendAlphaModel";

        public Gold2TrendAlphaModel(Gold2TrendFactor trend, Symbol gold, decimal floor, bool disabled = false)
        {
            _trend = trend;
            _gold = gold;
            _floor = floor;
            _disabled = disabled;
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            // trend-disable=true(spec §7.2 vol_only 控制实验):完全旁路趋势层,dirCoef 恒=1.0,
            // 使 portfolio target = w_smooth × 1.0,即纯波动率目标。trend-floor 仅在 disabled=false
            /// 时生效(否则 floor 的"空头保留底仓"语义与"趋势层禁用"矛盾,故 disabled 时忽略 floor)。
            if (_disabled)
            {
                LastDirCoef = 1.0m;
                yield return new Insight(
                    _gold,
                    TimeSpan.FromDays(1),
                    InsightType.Price,
                    InsightDirection.Up,
                    null,
                    null,
                    "Gold2Trend",
                    1.0,
                    "trend-disabled: dirCoef=1.0 (pure vol-target)");
                yield break;
            }

            var v = _trend.Compute(_gold, algorithm.Time);
            int dir = (int)v.Value;
            decimal confirm = v.RawValue;

            InsightDirection direction;
            decimal weight;
            if (dir > 0)
            {
                direction = InsightDirection.Up;
                weight = confirm; // confirm ∈ {0.5, 1.0}
            }
            else
            {
                // dir <= 0: 空头或 warmup → long-only Flat,保留底仓 floor
                direction = InsightDirection.Flat;
                weight = _floor;
            }
            LastDirCoef = weight;

            yield return new Insight(
                _gold,
                TimeSpan.FromDays(1),
                InsightType.Price,
                direction,
                null,
                null,
                "Gold2Trend",
                (double)weight,
                "");
        }
    }
}
