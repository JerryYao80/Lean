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
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Daily Barra CNE5 factor snapshot for an A-share stock.
    /// </summary>
    public class AShareBarraCNE5FactorData : BaseData
    {
        private static string _baseDirectory;

        public decimal? Beta { get; set; }
        public decimal? Momentum { get; set; }
        public decimal? Size { get; set; }
        public decimal? EarningsYield { get; set; }
        public decimal? ResidualVolatility { get; set; }
        public decimal? Growth { get; set; }
        public decimal? BookToPrice { get; set; }
        public decimal? Leverage { get; set; }
        public decimal? Liquidity { get; set; }
        public decimal? NonLinearSize { get; set; }
        public decimal? TotalMv { get; set; }
        public decimal? TurnoverRate { get; set; }
        public int? ListedDays { get; set; }
        public int? MissingFactorCount { get; set; }
        public bool IsSt { get; set; }

        public Symbol UnderlyingSymbol => Symbol != null && Symbol.HasUnderlying ? Symbol.Underlying : Symbol;

        public int PresentFactorCount
        {
            get
            {
                var count = 0;
                count += Beta.HasValue ? 1 : 0;
                count += Momentum.HasValue ? 1 : 0;
                count += Size.HasValue ? 1 : 0;
                count += EarningsYield.HasValue ? 1 : 0;
                count += ResidualVolatility.HasValue ? 1 : 0;
                count += Growth.HasValue ? 1 : 0;
                count += BookToPrice.HasValue ? 1 : 0;
                count += Leverage.HasValue ? 1 : 0;
                count += Liquidity.HasValue ? 1 : 0;
                count += NonLinearSize.HasValue ? 1 : 0;
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
                    ? Path.Combine(Globals.DataFolder, "alternative", "barra-cne5-factors")
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
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            var csv = line.Split(',');
            if (csv.Length < 16)
            {
                return null;
            }

            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
            {
                return null;
            }

            return new AShareBarraCNE5FactorData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(csv[2]) ?? 0m,
                Beta = ParseNullableDecimal(csv[1]),
                Momentum = ParseNullableDecimal(csv[2]),
                Size = ParseNullableDecimal(csv[3]),
                EarningsYield = ParseNullableDecimal(csv[4]),
                ResidualVolatility = ParseNullableDecimal(csv[5]),
                Growth = ParseNullableDecimal(csv[6]),
                BookToPrice = ParseNullableDecimal(csv[7]),
                Leverage = ParseNullableDecimal(csv[8]),
                Liquidity = ParseNullableDecimal(csv[9]),
                NonLinearSize = ParseNullableDecimal(csv[10]),
                TotalMv = ParseNullableDecimal(csv[11]),
                TurnoverRate = ParseNullableDecimal(csv[12]),
                ListedDays = ParseNullableInt(csv[13]),
                MissingFactorCount = ParseNullableInt(csv[14]),
                IsSt = ParseNullableInt(csv[15]).GetValueOrDefault() != 0
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

        private static int? ParseNullableInt(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return null;
            }

            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }
    }
}
