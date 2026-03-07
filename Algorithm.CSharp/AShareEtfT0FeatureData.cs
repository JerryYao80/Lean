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
    /// Daily feature snapshot exported from Tushare fund_daily data for A-share T+0 ETFs.
    /// </summary>
    public class AShareEtfT0FeatureData : BaseData
    {
        private static string _baseDirectory;

        public decimal? PreClose { get; set; }
        public decimal? Open { get; set; }
        public decimal? High { get; set; }
        public decimal? Low { get; set; }
        public decimal? Close { get; set; }
        public decimal? PctChg { get; set; }
        public decimal? Amount { get; set; }
        public decimal? VolumeValue { get; set; }
        public decimal? TradeReturn { get; set; }
        public decimal? GapReturn { get; set; }
        public decimal? CloseLocation { get; set; }
        public decimal? RangePct { get; set; }
        public decimal? Momentum5 { get; set; }
        public decimal? Momentum20 { get; set; }
        public decimal? Volatility10 { get; set; }
        public decimal? Liquidity5 { get; set; }
        public decimal? GapAbs { get; set; }
        public decimal? SignalMomentum20 { get; set; }
        public decimal? SignalMomentum5 { get; set; }
        public decimal? SignalLiquidity5 { get; set; }
        public decimal? SignalCloseLocation { get; set; }
        public decimal? SignalVolatility10 { get; set; }
        public decimal? SignalGapAbs { get; set; }

        public Symbol UnderlyingSymbol => Symbol != null && Symbol.HasUnderlying ? Symbol.Underlying : Symbol;
        public bool HasSignals => SignalMomentum20.HasValue
            && SignalMomentum5.HasValue
            && SignalLiquidity5.HasValue
            && SignalCloseLocation.HasValue
            && SignalVolatility10.HasValue
            && SignalGapAbs.HasValue;

        public static void SetBaseDirectory(string baseDirectory)
        {
            _baseDirectory = string.IsNullOrWhiteSpace(baseDirectory) ? null : Path.GetFullPath(baseDirectory);
        }

        public static string ResolveSourcePath(Symbol symbol, string baseDirectory = null)
        {
            var underlying = symbol != null && symbol.HasUnderlying ? symbol.Underlying : symbol;
            var root = string.IsNullOrWhiteSpace(baseDirectory)
                ? string.IsNullOrWhiteSpace(_baseDirectory)
                    ? Path.Combine(Globals.DataFolder, "alternative", "ashare-etf-t0-features")
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
            if (csv.Length < 24)
            {
                return null;
            }

            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
            {
                return null;
            }

            return new AShareEtfT0FeatureData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(csv[5]) ?? 0m,
                PreClose = ParseNullableDecimal(csv[1]),
                Open = ParseNullableDecimal(csv[2]),
                High = ParseNullableDecimal(csv[3]),
                Low = ParseNullableDecimal(csv[4]),
                Close = ParseNullableDecimal(csv[5]),
                PctChg = ParseNullableDecimal(csv[6]),
                Amount = ParseNullableDecimal(csv[7]),
                VolumeValue = ParseNullableDecimal(csv[8]),
                TradeReturn = ParseNullableDecimal(csv[9]),
                GapReturn = ParseNullableDecimal(csv[10]),
                CloseLocation = ParseNullableDecimal(csv[11]),
                RangePct = ParseNullableDecimal(csv[12]),
                Momentum5 = ParseNullableDecimal(csv[13]),
                Momentum20 = ParseNullableDecimal(csv[14]),
                Volatility10 = ParseNullableDecimal(csv[15]),
                Liquidity5 = ParseNullableDecimal(csv[16]),
                GapAbs = ParseNullableDecimal(csv[17]),
                SignalMomentum20 = ParseNullableDecimal(csv[18]),
                SignalMomentum5 = ParseNullableDecimal(csv[19]),
                SignalLiquidity5 = ParseNullableDecimal(csv[20]),
                SignalCloseLocation = ParseNullableDecimal(csv[21]),
                SignalVolatility10 = ParseNullableDecimal(csv[22]),
                SignalGapAbs = ParseNullableDecimal(csv[23]),
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
