// file: Common/Factors/Risk/VaRFactors.cs
using System.Collections.Generic;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Volatility;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Factors.Risk
{
    /// <summary>
    /// Registration helper for VaRFactor. Does NOT modify FactorRegistry.Initialize().
    /// Called by VarStrategy.Initialize() before constructing Alpha/Risk models.
    /// </summary>
    public static class VaRFactors
    {
        private static FactorMetadata _metadata;

        /// <summary>Register VaRFactor + its missing IVHVSpreadFactor dependency (idempotent).</summary>
        public static void Register(VaRScenario scenario = VaRScenario.OneDay99, VaRMethod method = VaRMethod.BootstrapHistorical)
        {
            FactorRegistry.Initialize();

            // Register IVHVSpreadFactor if not present (Initialize() does not register it)
            var iv = FactorRegistry.Get("iv_pct_252d");
            var hv = FactorRegistry.Get("hv_20d");
            if (iv != null && hv != null && FactorRegistry.Get("iv_hv_spread") == null)
                FactorRegistry.Register(new IVHVSpreadFactor(iv, hv));

            var factor = new VaRFactor(scenario: scenario, method: method);
            FactorRegistry.Register(factor);

            _metadata = new FactorMetadata
            {
                Id = factor.Id,
                Name = factor.Name,
                Category = factor.Category,
                Scope = factor.Scope,
                ComputeMode = factor.ComputeMode,
                DataSource = factor.DataSource,
                Dependencies = new List<string> { "hv_20d", "iv_pct_252d", "iv_hv_spread", "chip_concentration", "momentum_20d" },
                Description = "1D99 VaR regime percentile (TS) + marginal VaR contribution (XS); thin wrapper over VarEngine.",
                CsvPath = null
            };
        }

        public static FactorMetadata Metadata => _metadata;
        public static VaRFactor Instance => FactorRegistry.Get("var_1d99") as VaRFactor;
    }
}
