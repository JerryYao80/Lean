using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Volatility
{
    public class IVHVSpreadFactor : IFactor
    {
        private readonly IFactor _iv;
        private readonly IFactor _hv;

        public string Id => "iv_hv_spread";
        public string Name => "IV-HV Spread";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "tushare_opt_daily+price";

        public IVHVSpreadFactor(IFactor iv, IFactor hv) { _iv = iv; _hv = hv; }

        public FactorResult Compute(Symbol symbol, System.DateTime time, IEnumerable<BaseData> history = null)
        {
            var ivR = _iv.Compute(symbol, time, history);
            var hvR = _hv.Compute(symbol, time, history);
            if (ivR.Quality != FactorDataQuality.Valid || hvR.Quality != FactorDataQuality.Valid)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var spread = ivR.RawValue - hvR.RawValue;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = spread, RawValue = spread };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, System.DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, System.DateTime time) => _iv.IsAvailable(symbol, time) && _hv.IsAvailable(symbol, time);
    }
}