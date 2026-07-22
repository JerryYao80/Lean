using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Trend
{
    public class MomentumFactor : IFactor
    {
        private readonly int _window;
        public string Id => $"momentum_{_window}d";
        public string Name => $"{_window}-Day Momentum";
        public FactorCategory Category => FactorCategory.Trend;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history";

        public MomentumFactor(int window = 20) => _window = window;

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            if (history == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).ToList();
            if (bars.Count < _window) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var start = bars[bars.Count - _window].Close;
            var end = bars.Last().Close;
            if (start <= 0) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var ret = (end - start) / start;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = ret, RawValue = ret };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
