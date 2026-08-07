// file: Common/Risk/VaR/VaRRegimeScore.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>
    /// Regime score for VaRFactor: percentile of current VaR vs trailing VaR series.
    /// </summary>
    public readonly struct VaRRegimeScore
    {
        /// <summary>Current VaR value (raw 1D99 fraction).</summary>
        public decimal CurrentVaR { get; }

        /// <summary>Percentile [0,1] within trailing VaR series. Low=calm, High=stressed.</summary>
        public decimal RegimePercentile { get; }

        /// <summary>Marginal VaR contribution (cross-section ranking).</summary>
        public decimal MarginalContribution { get; }

        public Symbol Symbol { get; }
        public bool IsValid { get; }

        public VaRRegimeScore(decimal currentVaR, decimal regimePercentile, decimal marginalContribution, Symbol symbol, bool isValid)
        {
            CurrentVaR = currentVaR;
            RegimePercentile = regimePercentile;
            MarginalContribution = marginalContribution;
            Symbol = symbol;
            IsValid = isValid;
        }
    }
}
