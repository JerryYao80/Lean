using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Volatility
{
    public class HVFactor : IFactor
    {
        private readonly int _windowDays;
        public string Id => $"hv_{_windowDays}d";
        public string Name => $"{_windowDays}-Day Realized Volatility";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history";

        public HVFactor(int windowDays = 20) { _windowDays = windowDays; }

        public FactorResult Compute(Symbol symbol, System.DateTime time, IEnumerable<BaseData> history = null)
        {
            if (history == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).TakeLast(_windowDays).ToList();
            if (bars.Count < 2) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var returns = new List<double>();
            for (int i = 1; i < bars.Count; i++) { if (bars[i-1].Close > 0) returns.Add((double)(bars[i].Close / bars[i-1].Close - 1m)); }
            if (returns.Count < 2) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var mean = returns.Average();
            var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
            var vol = (decimal)(System.Math.Sqrt(variance) * System.Math.Sqrt(252));
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = vol, RawValue = vol };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, System.DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, System.DateTime time) => true;
    }
}