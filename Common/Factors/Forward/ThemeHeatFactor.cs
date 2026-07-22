using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 题材热度扩散因子（板块轮动/情绪）。
    /// 原理：limit_list_d.up_stat（N天M次涨停）构造题材热度扩散指标。
    ///       连续涨停股数量上升 → 市场题材活跃，赚钱效应扩散。
    /// 数据立足：tushare limit_list_d 表，字段 up_stat（连板统计，如 '3天2板'）。
    /// 计算立足：热度 = log1p(limit_up_count) * avg_consecutive_boards。
    /// 消费立足：Layer A 题材轮动 alpha；Layer B 风控做热度退潮预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 板块轮动/情绪类。
    /// </summary>
    public class ThemeHeatFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "theme_heat";
        public string Name => "Theme Heat Diffusion";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_limit_list_d+kpl_list";

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

        /// <summary>计算题材热度 = log1p(limit_up_count) * avg_consecutive_boards。夹到 [0, 20]。</summary>
        public static decimal ComputeHeat(int limitUpCount, decimal avgConsecutiveBoards)
        {
            if (limitUpCount <= 0) return 0m;
            var heat = Math.Log(1 + limitUpCount) * (double)avgConsecutiveBoards;
            return Math.Max(0m, Math.Min(20m, (decimal)heat));
        }
    }
}
