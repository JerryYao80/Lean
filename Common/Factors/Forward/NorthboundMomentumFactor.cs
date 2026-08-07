using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 北向资金动量因子（资金流类，短期领先）。
    /// 原理：moneyflow_hsgt.north_money 的 3 日/5 日动量，对权重股有领先性。
    /// 数据立足：tushare moneyflow_hsgt 表，字段 north_money（北向当日净流入，万元）。
    /// 计算立足：mom_n = north_money(t) / mean(|north_money|, 过去 n 日)，n=3/5，正=持续流入。
    /// 消费立足：Layer A 选股（权重股 alpha）；Layer B 风控做外资撤退预警。
    /// 与现有 NorthboundFactor（持仓比例，存量）互补：本因子是流量动量。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 资金流/筹码类。
    /// </summary>
    public class NorthboundMomentumFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "northbound_momentum";
        public string Name => "Northbound Capital Momentum (3D/5D)";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_moneyflow_hsgt";

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
        /// 计算北向资金动量 = north_money(t) / mean(|north_money|, n 日)，供 Python 导出器调用。
        /// northMoneySeries 按时间倒序：[t, t-1, ..., t-n+1]。返回值夹到 [-5, 5]。
        /// </summary>
        public static decimal ComputeMomentum(IReadOnlyList<decimal> northMoneySeries, int window = 3)
        {
            if (northMoneySeries == null || northMoneySeries.Count == 0 || window <= 0)
                return 0m;
            var take = Math.Min(window, northMoneySeries.Count);
            decimal absSum = 0m;
            for (int i = 0; i < take; i++)
                absSum += Math.Abs(northMoneySeries[i]);
            var meanAbs = absSum / take;
            if (meanAbs <= 0m) return 0m;
            var mom = northMoneySeries[0] / meanAbs;
            return Math.Max(-5m, Math.Min(5m, mom));
        }
    }
}
