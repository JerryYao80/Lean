using System;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Core
{
    public struct FactorResult
    {
        public decimal Value { get; set; }
        public decimal RawValue { get; set; }
        public DateTime Time { get; set; }
        public Symbol Symbol { get; set; }
        public string FactorId { get; set; }
        public FactorDataQuality Quality { get; set; }
        public int ComputeTimeMs { get; set; }
    }

    public enum FactorDataQuality { Valid, Missing, Outlier, Stale }
}