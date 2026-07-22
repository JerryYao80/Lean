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
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Generic tushare factor data reader with header-discovery and dictionary storage.
    /// Reads per-stock CSV files with dynamic columns produced by the multi-family export pipeline.
    /// </summary>
    public class AShareTushareFactorData : BaseData
    {
        private static string _baseDirectory;
        private static readonly Dictionary<string, string[]> HeaderByPath = new(StringComparer.Ordinal);

        /// <summary>
        /// Clears the cached header map. Call between test runs or when data files change schema.
        /// </summary>
        public static void ClearHeaderCache() => HeaderByPath.Clear();

        /// <summary>
        /// Factor field values parsed from the CSV row. Key = column name, Value = parsed decimal or null.
        /// </summary>
        public Dictionary<string, decimal?> Fields { get; set; } = new(StringComparer.Ordinal);

        /// <summary>
        /// String field values parsed from the CSV row (e.g. sector_code).
        /// Key = column name, Value = raw string.
        /// </summary>
        public Dictionary<string, string> StringFields { get; set; } = new(StringComparer.Ordinal);

        public Symbol UnderlyingSymbol => Symbol != null && Symbol.HasUnderlying ? Symbol.Underlying : Symbol;

        public decimal? GetDecimal(string name) => Fields.TryGetValue(name, out var v) ? v : null;

        public int? GetInt(string name)
        {
            var d = GetDecimal(name);
            return d.HasValue ? (int?)decimal.ToInt32(d.Value) : null;
        }

        public string GetString(string name) => StringFields.TryGetValue(name, out var v) ? v : null;

        // Convenience properties for most-used fields
        public decimal? Close => GetDecimal("close");
        public decimal? Pe => GetDecimal("pe");
        public decimal? Pb => GetDecimal("pb");
        public decimal? TotalMv => GetDecimal("total_mv");
        public decimal? CircMv => GetDecimal("circ_mv");
        public decimal? TurnoverRate => GetDecimal("turnover_rate");
        public decimal? PctChg => GetDecimal("pct_chg");
        public decimal? Momentum12020 => GetDecimal("momentum_120_20");
        public decimal? Return5 => GetDecimal("return_5");
        public decimal? Volatility20 => GetDecimal("volatility_20");

        /// <summary>
        /// Count of non-null factor fields (excluding trade_date and sector_code).
        /// Used for eligibility filtering — stocks must have data from enough families.
        /// </summary>
        public int PresentFieldCount
        {
            get
            {
                var count = 0;
                foreach (var pair in Fields)
                {
                    if (pair.Value.HasValue)
                    {
                        count++;
                    }
                }
                return count;
            }
        }

        public static void SetBaseDirectory(string baseDirectory)
        {
            _baseDirectory = string.IsNullOrWhiteSpace(baseDirectory) ? null : Path.GetFullPath(baseDirectory);
        }

        public static string ResolveSourcePath(Symbol symbol, string baseDirectory = null)
        {
            var underlying = symbol != null && symbol.HasUnderlying ? symbol.Underlying : symbol;
            var root = string.IsNullOrWhiteSpace(baseDirectory)
                ? string.IsNullOrWhiteSpace(_baseDirectory)
                    ? Path.Combine(Globals.DataFolder, "alternative", "ashare-multi-family-features")
                    : _baseDirectory
                : Path.GetFullPath(baseDirectory);

            return Path.Combine(root, underlying.ID.Market.ToLowerInvariant(), "daily", $"{underlying.Value}.csv");
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            return new SubscriptionDataSource(ResolveSourcePath(config.Symbol), SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                return null;
            }

            // Detect and cache the header line
            if (line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
            {
                var key = ResolveSourcePath(config.Symbol);
                if (!string.IsNullOrEmpty(key) && !HeaderByPath.ContainsKey(key))
                {
                    HeaderByPath[key] = line.Split(',');
                }
                return null;
            }

            var csv = line.Split(',');
            if (csv.Length < 2)
            {
                return null;
            }

            if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
            {
                // Try YYYYMMDD format
                if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out tradeDate))
                {
                    return null;
                }
            }

            // Find the header for this file
            var sourcePath = ResolveSourcePath(config.Symbol);
            string[] header = null;
            if (!string.IsNullOrEmpty(sourcePath) && HeaderByPath.TryGetValue(sourcePath, out var cached))
            {
                header = cached;
            }

            var fields = new Dictionary<string, decimal?>(StringComparer.Ordinal);
            var stringFields = new Dictionary<string, string>(StringComparer.Ordinal);
            var closeValue = 0m;

            for (var i = 1; i < csv.Length && (header == null || i < header.Length); i++)
            {
                var colName = header != null ? header[i] : $"col{i}";
                if (string.Equals(colName, "trade_date", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (string.Equals(colName, "sector_code", StringComparison.OrdinalIgnoreCase))
                {
                    stringFields[colName] = csv[i];
                    continue;
                }

                var parsed = ParseNullableDecimal(csv[i]);
                fields[colName] = parsed;

                if (string.Equals(colName, "close", StringComparison.OrdinalIgnoreCase) && parsed.HasValue)
                {
                    closeValue = parsed.Value;
                }
            }

            // Shift EndTime by 1 trading day to avoid look-ahead bias:
            // Factor data for trade_date T should be delivered on T+1,
            // so the algorithm uses T's signals to trade at T+1's price.
            // Without this shift, the algorithm sees T's close/pe/pb/etc.
            // and trades at T's close — using same-day data that isn't
            // available until after the market closes.
            var nextTradingDay = tradeDate.Date.AddDays(1);

            return new AShareTushareFactorData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = nextTradingDay,
                Value = closeValue,
                Fields = fields,
                StringFields = stringFields
            };
        }

        public override BaseData Clone()
        {
            return new AShareTushareFactorData
            {
                Symbol = Symbol,
                Time = Time,
                EndTime = EndTime,
                Value = Value,
                Fields = new Dictionary<string, decimal?>(Fields, StringComparer.Ordinal),
                StringFields = new Dictionary<string, string>(StringFields, StringComparer.Ordinal)
            };
        }

        private static decimal? ParseNullableDecimal(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return null;
            }

            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }
    }
}
