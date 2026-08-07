using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 高管增减持因子（股东/机构行为，内部人信号）。
    /// 原理：stk_holdertrade 高管增减持金额占流通市值比例。高管增持 → 内部人看多。
    /// 数据立足：tushare stk_holdertrade 表，字段 in_de（BUY/SELL）/change_vol/avg_price。
    /// 计算立足：net_ratio = (BUY 金额 - SELL 金额) / 流通市值，正=净增持。
    /// 消费立足：Layer A 选股内部人信号 alpha；Layer B 风控做高管减持预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 股东/机构行为类。
    /// </summary>
    public class InsiderTradeFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "insider_trade";
        public string Name => "Insider Trade Net Ratio";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_stk_holdertrade";

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

        /// <summary>计算高管净增减持比例 = (BUY 金额 - SELL 金额) / 流通市值。夹到 [-0.2, 0.2]。</summary>
        public static decimal ComputeNetRatio(decimal buyAmount, decimal sellAmount, decimal circMarketValue)
        {
            if (circMarketValue <= 0m) return 0m;
            var ratio = (buyAmount - sellAmount) / circMarketValue;
            return Math.Max(-0.2m, Math.Min(0.2m, ratio));
        }
    }
}
