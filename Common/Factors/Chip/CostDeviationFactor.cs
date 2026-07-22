using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Chip
{
    public class CostDeviationFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();
        public string Id => "chip_cost_deviation";
        public string Name => "Chip Cost Deviation";
        public FactorCategory Category => FactorCategory.Chip;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "cyq_perf";

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
