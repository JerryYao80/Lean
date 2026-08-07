using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// M1-M2 剪刀差因子（宏观流动性，中低频领先）。
    /// 原理：cn_m 的 m1_yoy - m2_yoy，传统的 A 股流动性领先指标。
    ///       M1 增速大于 M2，资金活化，企业存款活期化，股市流动性充裕（利好）。
    ///       M1 小于 M2，资金定期化，流动性收缩（利空）。
    /// 数据立足：tushare cn_m 表，字段 m1_yoy/m2_yoy（同比增速 %）。
    /// 计算立足：scissors = m1_yoy - m2_yoy，正=流动性扩张。
    /// 消费立足：Layer B 风控/仓位调节（宏观外生变量）；Layer C 执行层做仓位 scaling。
    /// Precomputed 模式：由外部 Pipeline 注入（宏观广播：对每个 symbol 注入同一值）。
    /// 详见 docs/qianzhan.md 宏观流动性类。
    /// </summary>
    public class M1M2ScissorsFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "m1_m2_scissors";
        public string Name => "M1-M2 YoY Scissors";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_cn_m";

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

        /// <summary>计算 M1-M2 剪刀差 = m1_yoy - m2_yoy。夹到 [-15, 15]。</summary>
        public static decimal ComputeScissors(decimal m1YoY, decimal m2YoY)
        {
            var diff = m1YoY - m2YoY;
            return Math.Max(-15m, Math.Min(15m, diff));
        }
    }
}
