using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 集合竞价缺口率因子（量价微观结构，日内领先）。
    /// 原理：stk_auction_o.open（集合竞价开盘价）vs 前一日收盘的缺口率，对开盘后走势有短期预测力。
    /// 数据立足：tushare stk_auction_o 表，字段 open（集合竞价价）/close（前收）。
    /// 计算立足：gap = (auction_open / prev_close - 1)，正缺口=高开，负缺口=低开。
    /// 消费立足：Layer A 日内 alpha 触发器；Layer C 执行层做开盘撮合优化。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 量价微观结构类。
    /// </summary>
    public class AuctionGapFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "auction_gap";
        public string Name => "Auction Gap Rate";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_stk_auction_o";

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

        /// <summary>计算集合竞价缺口率 = (auction_open / prev_close - 1)。供 Python 导出器调用。夹到 [-0.2, 0.2]。</summary>
        public static decimal ComputeGap(decimal auctionOpen, decimal prevClose)
        {
            if (prevClose <= 0m) return 0m;
            var gap = auctionOpen / prevClose - 1m;
            return Math.Max(-0.2m, Math.Min(0.2m, gap));
        }
    }
}
