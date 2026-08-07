// file: Common/Risk/VaR/VaRMethod.cs
namespace QuantConnect.Risk.VaR
{
    /// <summary>VaR computation methods.</summary>
    public enum VaRMethod
    {
        /// <summary>Bootstrap Historical Simulation.</summary>
        BootstrapHistorical,
        /// <summary>Cornish-Fisher parametric (skew/kurtosis-adjusted).</summary>
        CornishFisher,
        /// <summary>Refined Monte Carlo (Bayesian Bootstrap MC / Cholesky portfolio).</summary>
        MonteCarlo,
        /// <summary>Backtest-period direct empirical quantile.</summary>
        DirectQuantile
    }
}
