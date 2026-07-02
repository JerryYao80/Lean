using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Trend
{
    public class RSIFactor : IFactor
    {
        private readonly int _window;
        public string Id => $"rsi_{_window}d";
        public string Name => $"{_window}-Day RSI";
        public FactorCategory Category => FactorCategory.Trend;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history";

        public RSIFactor(int window = 14) => _window = window;

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            if (history == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).ToList();
            if (bars.Count < _window + 1) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            
            var gains = 0d; var losses = 0d;
            for (int i = bars.Count - _window; i < bars.Count; i++)
            {
                var change = (double)(bars[i].Close - bars[i-1].Close);
                if (change > 0) gains += change;
                else losses += -change;
            }
            if (losses == 0) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = 100m, RawValue = 100m };
            var rs = gains / losses;
            var rsi = 100m - (100m / (decimal)(1 + rs));
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = rsi, RawValue = rsi };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
