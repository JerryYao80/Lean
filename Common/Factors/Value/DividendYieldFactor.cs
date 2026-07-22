using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Data.AShare;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Value
{
    public class DividendYieldFactor : IFactor
    {
        private readonly AShareDividendYieldModel _model;
        private readonly Dictionary<Symbol, decimal> _cache = new();
        public string Id => "dividend_yield";
        public string Name => "Dividend Yield";
        public FactorCategory Category => FactorCategory.Value;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_fund_div";

        public DividendYieldFactor(string tusharePath = "/home/project/tushare-downloader/tushare_data_v2", string underlying = null)
        {
            try { _model = new AShareDividendYieldModel(tusharePath, underlying ?? "510300"); }
            catch { _model = null; }
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_model == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            try
            {
                if (_cache.TryGetValue(symbol, out var cached))
                    return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = cached, RawValue = cached };
                var dy = _model.GetDividendYield(time);
                _cache[symbol] = dy;
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = dy, RawValue = dy };
            }
            catch { return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing }; }
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _model != null;
    }
}
