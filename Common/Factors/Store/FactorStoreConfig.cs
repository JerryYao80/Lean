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
            // Manipulation detection factors
            store.Register("turnover_anomaly", new RParquetAdapter("factor-zoo/turnover_anomaly", "turnover_anomaly"));
            store.Register("amplitude_anomaly", new RParquetAdapter("factor-zoo/amplitude_anomaly", "amplitude_anomaly"));
            store.Register("limit_behavior", new RParquetAdapter("factor-zoo/limit_behavior", "limit_behavior"));
            store.Register("intraday_reversal", new RParquetAdapter("factor-zoo/intraday_reversal", "intraday_reversal"));

            // Phase-5 fundamental / anomaly factors (built to result/factor-zoo/<fid>/<date>.parquet,
            // value column == fid). Previously built but unregistered — catalog↔built↔registered gap.
            foreach (var fid in new[] {
                "accruals_sloan", "gross_profitability", "asset_growth", "roe_change",
                "ivol_20d", "max_ret_20d", "short_term_reversal"
            })
            {
                store.Register(fid, new RParquetAdapter($"factor-zoo/{fid}", fid));
            }

            // Margin / short-selling factors: one parquet dir (factor-zoo/margin_factors) holds
            // 5 value columns. Register each sub-fid as its own adapter pointing at the same dir.
            foreach (var col in new[] {
                "margin_balance_change", "short_balance_change", "margin_buy_ratio",
                "short_sell_ratio", "margin_short_ratio"
            })
            {
                store.Register(col, new RParquetAdapter("factor-zoo/margin_factors", col));
            }

            // Technical indicators (48 fids): dir == value column == fid (e.g. tech_macd).
            // Mirrors Alpha101FactorRegistration's per-fid RParquetAdapter pattern.
            TechnicalFactorRegistration.Register(store);

            // Alpha101: 101 个独立 id (单点读, LLM/人工点名哪个读哪个)
            Alpha101FactorRegistration.Register(store);
        }
    }
}
