using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Chip
{
    public class PeakPatternFactor : IFactor
    {
        public string Id => "chip_peak_pattern";
        public string Name => "Chip Peak Pattern";
        public FactorCategory Category => FactorCategory.Chip;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "cyq_perf";

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => false;
    }
}