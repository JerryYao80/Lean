using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 异常放量 Z-score 因子（量价微观结构，日内领先）。
    /// 原理：daily_basic.volume_ratio + turnover_rate_f 的 Z-score，异常放量前置指标。
    /// 数据立足：tushare bak_daily 表，字段 vol_ratio（量比）/turn_over（换手率）。
    /// 计算立足：z = (vol_ratio - mean) / std，横截面 z-score，>2 为异常放量。
    /// 消费立足：Layer A 选股放量突破 alpha；Layer B 风控做流动性枯竭预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 量价微观结构类。
    /// </summary>
    public class VolumeAnomalyFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "volume_anomaly_zscore";
        public string Name => "Volume Anomaly Z-Score";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_bak_daily";

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

        /// <summary>计算量比 Z-score = (vol_ratio - mean) / std。供 Python 导出器调用。夹到 [-5, 5]。</summary>
        public static decimal ComputeZScore(decimal volRatio, decimal mean, decimal std)
        {
            if (std <= 0m) return 0m;
            var z = (volRatio - mean) / std;
            return Math.Max(-5m, Math.Min(5m, z));
        }
    }
}
