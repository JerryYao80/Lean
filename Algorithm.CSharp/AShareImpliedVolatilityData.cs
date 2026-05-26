using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Daily implied volatility snapshot for an A-share ETF with listed options.
    /// Merged IV (ATM, 25-delta skew) and VIX-like model-free index.
    /// </summary>
    public class AShareImpliedVolatilityData : BaseData
    {
        private static string _baseDirectory;

        public decimal? AtmIv { get; set; }
        public decimal? IvCall25Delta { get; set; }
        public decimal? IvPut25Delta { get; set; }
        public decimal? Skew { get; set; }
        public int? TermDaysNear { get; set; }
        public int? TermDaysNext { get; set; }
        public int? OptionCount { get; set; }
        public decimal? Vix { get; set; }
        public decimal? SigmaNear { get; set; }
        public decimal? SigmaNext { get; set; }
        public decimal? TNear { get; set; }
        public decimal? TNext { get; set; }

        /// <summary>
        /// IV term structure ratio: near-term vol / next-term vol.
        /// Values > 1 indicate backwardation (fear), &lt; 1 indicate contango.
        /// Falls back to AtmIv / SigmaNext when SigmaNear/TNext unavailable.
        /// </summary>
        public decimal? IvTermStructureRatio
        {
            get
            {
                if (SigmaNear.HasValue && SigmaNext.HasValue && SigmaNext.Value != 0m)
                    return SigmaNear.Value / SigmaNext.Value;
                return null;
            }
        }

        public Symbol UnderlyingSymbol => Symbol != null && Symbol.HasUnderlying ? Symbol.Underlying : Symbol;

        public static void SetBaseDirectory(string baseDirectory)
        {
            _baseDirectory = string.IsNullOrWhiteSpace(baseDirectory) ? null : Path.GetFullPath(baseDirectory);
        }

        public static string ResolveSourcePath(Symbol symbol, string baseDirectory = null)
        {
            var underlying = symbol != null && symbol.HasUnderlying ? symbol.Underlying : symbol;
            var root = string.IsNullOrWhiteSpace(baseDirectory)
                ? string.IsNullOrWhiteSpace(_baseDirectory)
                    ? Path.Combine(Globals.DataFolder, "alternative", "ashare-implied-volatility")
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
                return null;

            var csv = line.Split(',');
            if (csv.Length < 8)
                return null;

            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
                return null;

            return new AShareImpliedVolatilityData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(csv[1]) ?? 0m,
                AtmIv = ParseNullableDecimal(csv[1]),
                IvCall25Delta = csv.Length > 2 ? ParseNullableDecimal(csv[2]) : null,
                IvPut25Delta = csv.Length > 3 ? ParseNullableDecimal(csv[3]) : null,
                Skew = csv.Length > 4 ? ParseNullableDecimal(csv[4]) : null,
                TermDaysNear = csv.Length > 5 ? ParseNullableInt(csv[5]) : null,
                TermDaysNext = csv.Length > 6 ? ParseNullableInt(csv[6]) : null,
                OptionCount = csv.Length > 7 ? ParseNullableInt(csv[7]) : null,
                Vix = csv.Length > 8 ? ParseNullableDecimal(csv[8]) : null,
                SigmaNear = csv.Length > 9 ? ParseNullableDecimal(csv[9]) : null,
                SigmaNext = csv.Length > 10 ? ParseNullableDecimal(csv[10]) : null,
                TNear = csv.Length > 11 ? ParseNullableDecimal(csv[11]) : null,
                TNext = csv.Length > 12 ? ParseNullableDecimal(csv[12]) : null,
            };
        }

        private static decimal? ParseNullableDecimal(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return null;
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }

        private static int? ParseNullableInt(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return null;
            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }
    }
}
