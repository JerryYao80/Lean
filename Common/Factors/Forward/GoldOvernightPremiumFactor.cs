using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 黄金 ETF 隔夜溢价 Z-signal 因子（Precomputed）。
    /// 由 Python 导出器算好 Z_signal 后通过 InjectValue 注入，算法 OnData 调用 Compute 读取。
    /// 镜像 AuctionGapFactor 的 Precomputed + InjectValue 模式。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §3。
    /// </summary>
    public class GoldOvernightPremiumFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "gold_overnight_premium_z";
        public string Name => "Gold Overnight Premium Z-Signal";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "au_shf_fut_daily+518880_fund_daily";

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
    }
}
