/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0.
 *
 * Factor Scope Enumeration - distinguishes time-series vs cross-section
 */

namespace QuantConnect.Factors.Core
{
    public enum FactorScope
    {
        TimeSeries,
        CrossSection,
        Both
    }
}