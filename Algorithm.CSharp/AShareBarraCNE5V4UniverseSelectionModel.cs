/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
*/

using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.Fundamental;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Universe selection model for Barra CNE5 V4 strategy.
    /// Selects from a pre-defined set of A-share equities that have Barra CNE5 factor data.
    /// Uses LEAN native FundamentalUniverseSelectionModel base class.
    /// </summary>
    public class AShareBarraCNE5V4UniverseSelectionModel : FundamentalUniverseSelectionModel
    {
        private readonly HashSet<Symbol> _eligibleSymbols;
        private readonly int _refreshMonths;
        private DateTime _nextRefreshUtc = DateTime.MinValue;

        public AShareBarraCNE5V4UniverseSelectionModel(
            IEnumerable<Symbol> eligibleSymbols,
            int refreshMonths = 1)
            : base("china", null)
        {
            _eligibleSymbols = new HashSet<Symbol>(eligibleSymbols);
            _refreshMonths = Math.Max(1, refreshMonths);
        }

        public override IEnumerable<Symbol> Select(QCAlgorithm algorithm, IEnumerable<Fundamental> fundamental)
        {
            var available = fundamental
                .Where(f => _eligibleSymbols.Contains(f.Symbol) && f.Price > 0)
                .Select(f => f.Symbol)
                .ToList();

            if (available.Count == 0)
            {
                return _eligibleSymbols;
            }

            return available;
        }

        public override DateTime GetNextRefreshTimeUtc()
        {
            if (_nextRefreshUtc == DateTime.MinValue)
            {
                _nextRefreshUtc = DateTime.UtcNow.AddMonths(_refreshMonths);
            }
            return _nextRefreshUtc;
        }
    }
}
