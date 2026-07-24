/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter interface.
 * See docs/superpowers/specs/2026-07-22-tushare-factor-zoo-design.md §2.
 */
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Read-only adapter contract: resolve one factor's value for (symbol, date).
    /// Adapters NEVER write, NEVER mutate existing FactorRegistry/Barra state.
    /// </summary>
    public interface IFactorAdapter
    {
        /// <summary>Return false (with Quality=Missing result) if data unavailable; never throw on missing.</summary>
        bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result);
    }
}
