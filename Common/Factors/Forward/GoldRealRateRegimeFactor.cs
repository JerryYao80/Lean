using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 实际利率 regime 因子（Precomputed）。返回 GoldRegime enum 编码为 decimal。
    /// 当前 FRED_API_KEY 未配置时算法注入 UNAVAILABLE（行为同 STABLE，状态区分）。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §4.1。
    /// </summary>
    public class GoldRealRateRegimeFactor : IFactor
    {
        private readonly Dictionary<Symbol, GoldRegime> _injected = new();

        public string Id => "gold_real_rate_regime";
        public string Name => "Gold Real Rate Regime";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "fred_dfii10(optional)";

        public void InjectRegime(Symbol symbol, GoldRegime regime) => _injected[symbol] = regime;
        public void ClearInjectedValues() => _injected.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_injected.TryGetValue(symbol, out var regime))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = (decimal)regime, RawValue = (decimal)regime };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = (decimal)GoldRegime.UNAVAILABLE };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injected.ContainsKey(symbol);
    }
}
