using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 披露提前/推迟天数因子（基本面意外/预期差）。
    /// 原理：disclosure_date.actual_date 相对 pre_date 的提前/推迟天数。
    /// 历史上有"报喜早报忧晚"效应——提前披露 → 业绩大概率好。
    /// 数据立足：tushare disclosure_date 表，字段 actual_date/pre_date。
    /// 计算立足：delta = (pre_date - actual_date).Days，正=提前披露（偏利好），负=推迟（偏利空）。
    /// 消费立足：Layer A 选股中低频 alpha；Layer B 风控做业绩雷预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 基本面意外/预期差类。
    /// </summary>
    public class DisclosureTimingFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "disclosure_timing";
        public string Name => "Disclosure Advance/Delay Days";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_disclosure_date";

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

        /// <summary>计算披露提前/推迟天数 = (pre_date - actual_date).Days。正=提前（利好），负=推迟（利空）。夹到 [-90, 90]。</summary>
        public static decimal ComputeTimingDays(DateTime preDate, DateTime actualDate)
        {
            var days = (preDate - actualDate).Days;
            return Math.Max(-90m, Math.Min(90m, days));
        }
    }
}
