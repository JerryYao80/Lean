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
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect;
using QuantConnect.Interfaces;

namespace QuantConnect.Data.AShare
{
    /// <summary>
    /// Option chain provider for A-share ETF options (global::QuantConnect.Market.China).
    /// Reads converted universe CSVs produced by AShareOptionDataConverter
    /// and returns European-style option Symbols for AddOption.
    /// </summary>
    public class AShareOptionChainProvider : IOptionChainProvider
    {
        private readonly string _dataFolder;

        public AShareOptionChainProvider(string dataFolder)
        {
            _dataFolder = dataFolder;
        }

        /// <summary>
        /// Get the list of option contracts for a given underlying and date.
        /// Falls back to the nearest prior trading day's universe on holidays.
        /// </summary>
        public IEnumerable<Symbol> GetOptionContractList(Symbol symbol, DateTime date)
        {
            var underlying = symbol.Underlying ?? symbol;
            var ticker = underlying.Value;
            var market = symbol.ID.Market;
            var marketDir = market == global::QuantConnect.Market.CFE ? "cfe" : "sse";
            var isIndexOption = symbol.SecurityType == SecurityType.IndexOption;

            var universePath = Path.Combine(_dataFolder, "option", marketDir, "universes",
                                             ticker, $"{date:yyyyMMdd}.csv");
            if (!File.Exists(universePath))
            {
                universePath = FindNearestUniverse(marketDir, ticker, date);
                if (universePath == null) return Enumerable.Empty<Symbol>();
            }

            var contracts = new List<Symbol>();
            foreach (var line in File.ReadAllLines(universePath).Skip(1))
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                var parts = line.Split(',');
                if (parts.Length < 4) continue;

                var expiry = DateTime.ParseExact(parts[1], "yyyyMMdd", CultureInfo.InvariantCulture);
                var strike = decimal.Parse(parts[2], CultureInfo.InvariantCulture);
                var right = parts[3] == "C" ? OptionRight.Call : OptionRight.Put;

                if (isIndexOption)
                {
                    contracts.Add(Symbol.CreateOption(underlying, null, market,
                        SecurityType.IndexOption.DefaultOptionStyle(), right, strike, expiry));
                }
                else
                {
                    contracts.Add(Symbol.CreateOption(underlying, market,
                        OptionStyle.European, right, strike, expiry));
                }
            }
            return contracts;
        }

        private string FindNearestUniverse(string marketDir, string ticker, DateTime targetDate)
        {
            var dir = Path.Combine(_dataFolder, "option", marketDir, "universes", ticker);
            if (!Directory.Exists(dir)) return null;

            return Directory.GetFiles(dir, "*.csv")
                .Select(f => new { Path = f, Date = TryParseDate(Path.GetFileNameWithoutExtension(f)) })
                .Where(x => x.Date.HasValue && x.Date.Value <= targetDate)
                .OrderByDescending(x => x.Date)
                .Select(x => x.Path)
                .FirstOrDefault();
        }

        private static DateTime? TryParseDate(string name)
        {
            return DateTime.TryParseExact(name, "yyyyMMdd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var d) ? d : (DateTime?)null;
        }
    }
}
