// file: Common/Risk/VaR/VaRResult.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>
    /// Result of VaR computation. ValueAtRisk and ExpectedShortfall are
    /// POSITIVE loss magnitudes (e.g. 0.0234 = 2.34% loss).
    /// </summary>
    public readonly struct VaRResult
    {
        public double ValueAtRisk { get; }          // positive loss fraction
        public double ExpectedShortfall { get; }    // CVaR (>= ValueAtRisk)
        public double Quantile { get; }             // 1 - confidence
        public int HorizonDays { get; }
        public double Confidence { get; }
        public VaRMethod Method { get; }
        public VaRScenario Scenario { get; }
        public VaRDataQuality Quality { get; }
        public string DiagnosticMessage { get; }
        public int ObservationsUsed { get; }
        public double ComputeTimeMs { get; }
        public Symbol Symbol { get; }               // QuantConnect.Symbol, opaque tag, may be null for portfolio

        public bool IsValid => Quality == VaRDataQuality.Valid;

        public VaRResult(
            double valueAtRisk, double expectedShortfall, double quantile,
            int horizonDays, double confidence, VaRMethod method, VaRScenario scenario,
            VaRDataQuality quality, string diagnosticMessage, int observationsUsed,
            double computeTimeMs, Symbol symbol = null)
        {
            ValueAtRisk = valueAtRisk;
            ExpectedShortfall = expectedShortfall;
            Quantile = quantile;
            HorizonDays = horizonDays;
            Confidence = confidence;
            Method = method;
            Scenario = scenario;
            Quality = quality;
            DiagnosticMessage = diagnosticMessage;
            ObservationsUsed = observationsUsed;
            ComputeTimeMs = computeTimeMs;
            Symbol = symbol;
        }
    }
}
