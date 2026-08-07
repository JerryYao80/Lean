// file: Common/Risk/VaR/VaRConstants.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>
    /// Shared constants for VaR computation across all three layers
    /// (VarEngine, VaRFactor, VaRRiskModel, VarStrategy).
    /// Eliminates cross-layer inconsistencies: lookback threshold, return type,
    /// VaR sign convention (positive loss), seed determinism.
    /// </summary>
    public static class VaRConstants
    {
        /// <summary>Default lookback window in trading days (1 A-share trading year).</summary>
        public const int DefaultLookbackDays = 252;

        /// <summary>Minimum history days (below this, VarEngine returns InsufficientHistory).</summary>
        public const int MinHistoryDays = 250;

        /// <summary>Fixed random seed for deterministic backtests.</summary>
        public const int FixedSeed = 42;

        /// <summary>Trading days per year (matches HVFactor √252).</summary>
        public const int TradingDaysPerYear = 252;

        /// <summary>Default bootstrap iterations.</summary>
        public const int DefaultBootstrapIterations = 10000;

        /// <summary>Default Monte Carlo paths.</summary>
        public const int DefaultMonteCarloPaths = 50000;

        /// <summary>Eigenvalue floor for PD repair.</summary>
        public const double EigenvalueFloor = 1e-10;

        /// <summary>Minimum overlapping 10-day blocks for 10D horizon.</summary>
        public const int MinOverlappingBlocks = 30;

        /// <summary>Max iterations for VaR budget scaling refinement.</summary>
        public const int MaxScalingIterations = 8;

        /// <summary>Tolerance for VaR budget convergence (1%).</summary>
        public const double ScaleTolerance = 0.01;
    }
}
