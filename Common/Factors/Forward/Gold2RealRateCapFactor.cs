using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 实际利率帽子因子。包装 GoldRealRateRegimeFactor,RISING_FAST→cap(0.6),其余→1.0。
    /// 与 ExtremeRisk 串联在 L4 取 min。详见 spec §4.4。
    /// </summary>
    public class Gold2RealRateCapFactor : IFactor
    {
        private readonly GoldRealRateRegimeFactor _inner;
        private readonly decimal _risingFastCap;

        public string Id => "gold2_real_rate_cap";
        public string Name => "Gold2 Real-Rate Cap";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "fred_dfii10";

        public Gold2RealRateCapFactor(GoldRealRateRegimeFactor inner, decimal risingFastCap = 0.6m)
        {
            _inner = inner; _risingFastCap = risingFastCap;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            var inner = _inner.Compute(symbol, time, history);
            var regime = (GoldRegime)(decimal)inner.Value;
            var cap = regime == GoldRegime.RISING_FAST ? _risingFastCap : 1.0m;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = cap, RawValue = (decimal)regime };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
