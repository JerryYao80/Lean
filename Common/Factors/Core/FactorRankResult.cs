using System;
using System.Collections.Generic;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Core
{
    public struct FactorRankResult
    {
        public Dictionary<Symbol, decimal> Values { get; set; }
        public Dictionary<Symbol, decimal> PercentileRanks { get; set; }
        public List<Symbol> RankedSymbols { get; set; }
        public DateTime Time { get; set; }
        public string FactorId { get; set; }
        public int ValidCount { get; set; }
        public int MissingCount { get; set; }
    }
}