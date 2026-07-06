using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 大单净流入率因子（资金流类，短期领先）。
    /// 原理：大单（lg+elg）净买入占成交额比例，连续多日背离价格常是转折信号。
    /// 数据立足：tushare moneyflow 表，字段 buy_lg_amount/buy_elg_amount/sell_lg_amount/sell_elg_amount/amount。
    /// 计算立足：net_lg_rate = (buy_lg + buy_elg - sell_lg - sell_elg) / amount，[-1,1]。
    /// 消费立足：Layer A 选股短期 alpha 触发器；Layer B 风控做资金流背离预警。
    /// Precomputed 模式：由外部 Pipeline（Python 导出器）注入。
    /// 详见 docs/qianzhan.md 资金流/筹码类。
    /// </summary>
    public class BigOrderNetFlowFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "big_order_net_flow";
        public string Name => "Big-Order Net Inflow Rate";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_moneyflow";

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

        /// <summary>计算大单净流入率 = (buy_lg + buy_elg - sell_lg - sell_elg) / amount。供 Python 导出器调用。</summary>
        public static decimal ComputeRate(decimal buyLg, decimal buyElg, decimal sellLg, decimal sellElg, decimal amount)
        {
            if (amount <= 0m) return 0m;
            var net = (buyLg + buyElg) - (sellLg + sellElg);
            var rate = net / amount;
            return Math.Max(-1m, Math.Min(1m, rate));
        }
    }
}
