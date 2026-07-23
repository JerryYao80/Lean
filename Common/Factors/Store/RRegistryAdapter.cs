/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter wrapping FactorRegistry for Runtime factors.
 * Delegates Compute() to the registered IFactor; never mutates registry state.
 */
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Read-only wrapper for FactorRegistry Runtime factors (hv_20d, momentum_20d,
    /// ma_cross_5_20, rsi_14d, amihud_20d, iv_hv_spread). These Compute() from
    /// history with no InjectValue. Precomputed factors are owned by RParquetAdapter.
    /// </summary>
    public class RRegistryAdapter : IFactorAdapter
    {
        private readonly string _factorId;

        public RRegistryAdapter(string factorId) { _factorId = factorId; }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var factor = FactorRegistry.Get(_factorId);
            if (factor == null)
            {
                result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorId, Quality = FactorDataQuality.Missing };
                return false;
            }
            // Runtime factors only — guard against accidentally routing a Precomputed factor here.
            if (factor.ComputeMode != FactorComputeMode.Runtime)
            {
                result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorId, Quality = FactorDataQuality.Missing };
                return false;
            }
            result = factor.Compute(symbol, date, history);
            return result.Quality == FactorDataQuality.Valid;
        }
    }
}
