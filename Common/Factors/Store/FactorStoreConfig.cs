/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 default adapter registration.
 */
using QuantConnect.Factors.Core;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Wires default factor_id -> adapter mappings into a FactorStore.
    /// Tasks 2-4 populate this as adapters are built. For Task 1 it is a no-op
    /// stub so FactorStore compiles and routes unknown ids to Missing.
    /// </summary>
    internal static class FactorStoreConfig
    {
        public static void RegisterDefaults(FactorStore store)
        {
            FactorRegistry.Initialize();
            // Task 2: Runtime factors -> RRegistryAdapter
            // Task 3: Barra factors -> RBarraAdapter
            // Task 4: parquet factors -> RParquetAdapter
        }
    }
}
