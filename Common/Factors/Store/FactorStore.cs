/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 unified read interface (pull API).
 * Routes factor_id -> owning adapter. Read-only; does not modify FactorRegistry.
 */
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Single pull-based entry point for factor values: Get(factorId, symbol, date, history).
    /// Routes to the one adapter that owns the factor. Unknown ids -> Quality=Missing.
    /// </summary>
    public class FactorStore
    {
        private readonly Dictionary<string, IFactorAdapter> _adapters = new();
        private readonly object _lock = new();

        public FactorStore()
        {
            FactorStoreConfig.RegisterDefaults(this);
        }

        public void Register(string factorId, IFactorAdapter adapter)
        {
            lock (_lock) { _adapters[factorId] = adapter; }
        }

        public FactorResult Get(string factorId, Symbol symbol, DateTime date, IEnumerable<BaseData> history = null)
        {
            IFactorAdapter adapter;
            lock (_lock) { _adapters.TryGetValue(factorId, out adapter); }
            if (adapter == null)
            {
                return new FactorResult { Value = 0m, RawValue = 0m, Time = date, Symbol = symbol, FactorId = factorId, Quality = FactorDataQuality.Missing };
            }
            if (adapter.TryGet(symbol, date, history, out var result))
            {
                return result;
            }
            return new FactorResult { Value = 0m, RawValue = 0m, Time = date, Symbol = symbol, FactorId = factorId, Quality = FactorDataQuality.Missing };
        }

        /// <summary>Aggregate metadata from FactorRegistry (read-only view).</summary>
        public IReadOnlyDictionary<string, FactorMetadata> AllMetadata()
        {
            return FactorRegistry.AllMetadata();
        }
    }
}
