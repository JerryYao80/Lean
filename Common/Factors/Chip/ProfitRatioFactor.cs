using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Chip
{
    /// <summary>获利盘比例因子 = winner_rate / 100. [0,1]</summary>
    public class ProfitRatioFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injectedValues = new();

        public string Id => "chip_profit_ratio";
        public string Name => "Chip Profit Ratio";
        public FactorCategory Category => FactorCategory.Chip;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "cyq_perf";

        public void InjectValue(Symbol symbol, decimal value) => _injectedValues[symbol] = value;
        public void ClearInjectedValues() => _injectedValues.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            if (_injectedValues.TryGetValue(symbol, out var value))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = value, RawValue = value };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injectedValues.ContainsKey(symbol);
    }
}