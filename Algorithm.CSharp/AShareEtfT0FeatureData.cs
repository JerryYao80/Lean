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
        public decimal? UnitNav { get; set; }
        public decimal? AdjNav { get; set; }
        public decimal? TotalShare { get; set; }
        public decimal? TotalSize { get; set; }
        public decimal? NavPremium1 { get; set; }
        public decimal? NavPremiumZ20 { get; set; }
        public decimal? ShareChange5 { get; set; }
        public decimal? SizeChange5 { get; set; }
        public decimal? IndexGapReturn { get; set; }
        public decimal? IndexTradeReturn { get; set; }
        public decimal? IndexCloseReturn1 { get; set; }
        public decimal? IndexMomentum5 { get; set; }
        public decimal? ExcessGap { get; set; }
        public decimal? ExcessIntraday { get; set; }
        public decimal? TrackingError10 { get; set; }
        public decimal? SignalMomentum20 { get; set; }
        public decimal? SignalMomentum5 { get; set; }
        public decimal? SignalLiquidity5 { get; set; }
        public decimal? SignalCloseLocation { get; set; }
        public decimal? SignalVolatility10 { get; set; }
        public decimal? SignalGapAbs { get; set; }
        public decimal? SignalNavPremium1 { get; set; }
        public decimal? SignalNavPremiumZ20 { get; set; }
        public decimal? SignalShareChange5 { get; set; }
        public decimal? SignalSizeChange5 { get; set; }
        public decimal? SignalExcessGap { get; set; }
        public decimal? SignalExcessIntraday { get; set; }
        public decimal? SignalTrackingError10 { get; set; }
        public decimal? SignalIndexMomentum5 { get; set; }

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

            var isExtended = csv.Length >= 47;
            return new AShareEtfT0FeatureData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(GetCsvValue(csv, 5)) ?? 0m,
                PreClose = ParseNullableDecimal(GetCsvValue(csv, 1)),
                Open = ParseNullableDecimal(GetCsvValue(csv, 2)),
                High = ParseNullableDecimal(GetCsvValue(csv, 3)),
                Low = ParseNullableDecimal(GetCsvValue(csv, 4)),
                Close = ParseNullableDecimal(GetCsvValue(csv, 5)),
                PctChg = ParseNullableDecimal(GetCsvValue(csv, 6)),
                Amount = ParseNullableDecimal(GetCsvValue(csv, 7)),
                VolumeValue = ParseNullableDecimal(GetCsvValue(csv, 8)),
                TradeReturn = ParseNullableDecimal(GetCsvValue(csv, 9)),
                GapReturn = ParseNullableDecimal(GetCsvValue(csv, 10)),
                CloseLocation = ParseNullableDecimal(GetCsvValue(csv, 11)),
                RangePct = ParseNullableDecimal(GetCsvValue(csv, 12)),
                Momentum5 = ParseNullableDecimal(GetCsvValue(csv, 13)),
                Momentum20 = ParseNullableDecimal(GetCsvValue(csv, 14)),
                Volatility10 = ParseNullableDecimal(GetCsvValue(csv, 15)),
                Liquidity5 = ParseNullableDecimal(GetCsvValue(csv, 16)),
                GapAbs = ParseNullableDecimal(GetCsvValue(csv, 17)),
                UnitNav = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 18)) : null,
                AdjNav = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 19)) : null,
                TotalShare = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 20)) : null,
                TotalSize = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 21)) : null,
                NavPremium1 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 22)) : null,
                NavPremiumZ20 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 23)) : null,
                ShareChange5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 24)) : null,
                SizeChange5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 25)) : null,
                IndexGapReturn = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 26)) : null,
                IndexTradeReturn = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 27)) : null,
                IndexCloseReturn1 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 28)) : null,
                IndexMomentum5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 29)) : null,
                ExcessGap = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 30)) : null,
                ExcessIntraday = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 31)) : null,
                TrackingError10 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 32)) : null,
                SignalMomentum20 = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 33 : 18)),
                SignalMomentum5 = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 34 : 19)),
                SignalLiquidity5 = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 35 : 20)),
                SignalCloseLocation = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 36 : 21)),
                SignalVolatility10 = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 37 : 22)),
                SignalGapAbs = ParseNullableDecimal(GetCsvValue(csv, isExtended ? 38 : 23)),
                SignalNavPremium1 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 39)) : null,
                SignalNavPremiumZ20 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 40)) : null,
                SignalShareChange5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 41)) : null,
                SignalSizeChange5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 42)) : null,
                SignalExcessGap = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 43)) : null,
                SignalExcessIntraday = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 44)) : null,
                SignalTrackingError10 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 45)) : null,
                SignalIndexMomentum5 = isExtended ? ParseNullableDecimal(GetCsvValue(csv, 46)) : null,
            };
        }

        private static string GetCsvValue(string[] csv, int index)
        {
            return index >= 0 && index < csv.Length ? csv[index] : null;
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
