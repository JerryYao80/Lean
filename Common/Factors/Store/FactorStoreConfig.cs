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
            // Task 2: Runtime factors -> RRegistryAdapter.
            // iv_hv_spread is also Runtime but is registered by VaRFactors.Register() (called by
            // VarStrategy), NOT by FactorRegistry.Initialize(); default-registering it here would
            // always return Missing. A strategy needing it must Register(new RRegistryAdapter("iv_hv_spread"))
            // after VaRFactors.Register().
            foreach (var fid in new[] { "hv_20d", "momentum_20d", "ma_cross_5_20", "rsi_14d", "amihud_20d" })
            {
                store.Register(fid, new RRegistryAdapter(fid));
            }
            // Task 3: Barra factors -> RBarraAdapter
            var barraRoot = System.IO.Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-factors");
            foreach (var col in new[] { "beta","momentum","size","earnyld","resvol","growth","btop","leverage","liquidity","nlsize","moneyflow","quality","northbound","margin","chipcost" })
            {
                store.Register($"barra_{col}", new RBarraAdapter(col, barraRoot));
            }
            // Task 4: parquet factors -> RParquetAdapter
            store.Register("crowding", new RParquetAdapter("crowding-factor", "composite"));
            // Alpha101: 101 个独立 id (单点读, LLM/人工点名哪个读哪个)
            Alpha101FactorRegistration.Register(store);
        }
    }
}
