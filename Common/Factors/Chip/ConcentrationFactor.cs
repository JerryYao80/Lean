using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Chip
{
    /// <summary>筹码集中度因子 = exp(-spread), spread = (cost_95-cost_5)/weight_avg</summary>
    public class ConcentrationFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injectedValues = new();

        public string Id => "chip_concentration";
        public string Name => "Chip Concentration";
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