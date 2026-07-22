using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 质押风险预警因子（股东/机构行为）。
    /// 原理：pledge_stat.pledge_ratio 质押比例环比上升 + 股价接近平仓线 → 尾部风险预警。
    /// 数据立足：tushare pledge_stat 表，字段 pledge_ratio（质押比例 %）。
    /// 计算立足：risk = (pledge_ratio/100) * (1 - distance_to_liquidation)，distance = (close/liquidation_price - 1)。
    ///           风险分 [0,1]，质押比例高且接近平仓线 → 接近 1。
    /// 消费立足：Layer B 风控做尾部风险预警；Layer A 排除高质押标的。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 股东/机构行为类。
    /// </summary>
    public class PledgeRiskFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "pledge_risk";
        public string Name => "Pledge Liquidation Risk";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_pledge_stat";

        public void InjectValue(Symbol symbol, decimal value) => _injected[symbol] = value;
        public void ClearInjectedValues() => _injected.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_injected.TryGetValue(symbol, out var value))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = value, RawValue = value };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injected.ContainsKey(symbol);

        /// <summary>
        /// 计算质押风险分 = (pledge_ratio/100) * (1 - distance_to_liquidation)，[0,1]。
        /// distance = (close/liquidation_price - 1)，平仓线距离越近风险越高。
        /// pledge_ratio 单位 %（0-100）。
        /// </summary>
        public static decimal ComputeRisk(decimal pledgeRatioPct, decimal close, decimal liquidationPrice)
        {
            if (liquidationPrice <= 0m) return 0m;
            var ratio = Math.Max(0m, Math.Min(1m, pledgeRatioPct / 100m));
            var distance = close / liquidationPrice - 1m;
            var proximity = distance <= 0m ? 1m : Math.Max(0m, 1m - distance / 0.5m);
            return Math.Max(0m, Math.Min(1m, ratio * proximity));
        }
    }
}
