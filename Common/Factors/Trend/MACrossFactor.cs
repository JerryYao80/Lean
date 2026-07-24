using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Trend
{
    public class MACrossFactor : IFactor
    {
        private readonly int _short, _long;
        public string Id => $"ma_cross_{_short}_{_long}";
        public string Name => $"MA{_short}/{_long} Cross";
        public FactorCategory Category => FactorCategory.Trend;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history";

        public MACrossFactor(int shortWindow = 5, int longWindow = 20) { _short = shortWindow; _long = longWindow; }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            if (history == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).ToList();
            if (bars.Count < _long) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var shortMA = bars.TakeLast(_short).Average(b => (double)b.Close);
            var longMA = bars.TakeLast(_long).Average(b => (double)b.Close);
            var signal = (decimal)(shortMA - longMA);
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = signal, RawValue = signal };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
