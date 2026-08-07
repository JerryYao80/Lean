using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 转股溢价率因子（衍生品隐含信息）。
    /// 原理：cb_share.convert_val（转股溢价率）反映可转债市场对正股的隐含预期。
    ///       高溢价率 → 可转债市场对正股看多但正股未涨，潜在上涨预期。
    /// 数据立足：tushare cb_share 表，字段 convert_val（转股溢价率 %）。
    /// 计算立足：直接用 convert_val，正=正股折价（可转债看多），负=正股溢价。
    /// 消费立足：Layer A 选股可转债隐含预期 alpha；Layer B 风控做溢价率极端预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 衍生品隐含信息类。
    /// </summary>
    public class ConvertPremiumFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "convert_premium";
        public string Name => "Convertible Bond Premium";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_cb_share";

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

        /// <summary>转股溢价率归一化：夹到 [-100, 100]（单位 %）。</summary>
        public static decimal ComputePremium(decimal convertVal)
        {
            return Math.Max(-100m, Math.Min(100m, convertVal));
        }
    }
}
