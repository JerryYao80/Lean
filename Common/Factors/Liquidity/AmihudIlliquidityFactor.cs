using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Liquidity
{
    public class AmihudIlliquidityFactor : IFactor
    {
        private readonly int _window;
        public string Id => $"amihud_{_window}d";
        public string Name => $"{_window}-Day Amihud Illiquidity";
        public FactorCategory Category => FactorCategory.Liquidity;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history";

        public AmihudIlliquidityFactor(int window = 20) => _window = window;

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (history == null) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var bars = history.OfType<TradeBar>().OrderBy(b => b.Time).TakeLast(_window).ToList();
            if (bars.Count < 2) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var ratios = new List<double>();
            for (int i = 1; i < bars.Count; i++)
            {
                if (bars[i - 1].Close > 0 && bars[i].Volume > 0)
                {
                    var ret = Math.Abs((double)(bars[i].Close / bars[i - 1].Close - 1m));
                    var turnover = (double)(bars[i].Close * bars[i].Volume);
                    if (turnover > 0) ratios.Add(ret / turnover);
                }
            }
            if (ratios.Count == 0) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var amihud = ratios.Average() * 1e9;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = (decimal)amihud, RawValue = (decimal)amihud };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
