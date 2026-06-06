using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareMarketSentimentData : BaseData
    {
        private static string _baseDirectory;

        public decimal? BasisComposite { get; set; }
        public decimal? BasisIf { get; set; }
        public decimal? BasisIc { get; set; }
        public decimal? BasisIh { get; set; }
        public decimal? BasisIm { get; set; }

        public decimal? PcrHoVol { get; set; }
        public decimal? PcrHoOi { get; set; }
        public decimal? PcrIoVol { get; set; }
        public decimal? PcrIoOi { get; set; }
        public decimal? PcrMoVol { get; set; }
        public decimal? PcrMoOi { get; set; }
        public decimal? PcrComposite { get; set; }

        public decimal? Vix50 { get; set; }
        public decimal? Vix300 { get; set; }
        public decimal? Vix500 { get; set; }
        public decimal? VixComposite { get; set; }

        public decimal? MarginSseLong { get; set; }
        public decimal? MarginSseShort { get; set; }
        public decimal? MarginSseRatio { get; set; }
        public decimal? MarginSzseLong { get; set; }
        public decimal? MarginSzseShort { get; set; }
        public decimal? MarginSzseRatio { get; set; }
        public decimal? MarginComposite { get; set; }

        public bool IsExtremeFear { get; set; }
        public bool IsExtremeGreed { get; set; }

        public static void SetBaseDirectory(string baseDirectory)
        {
            _baseDirectory = string.IsNullOrWhiteSpace(baseDirectory) ? null : Path.GetFullPath(baseDirectory);
        }

        public static string ResolveSourcePath(Symbol symbol, string baseDirectory = null)
        {
            var root = string.IsNullOrWhiteSpace(baseDirectory)
                ? string.IsNullOrWhiteSpace(_baseDirectory)
                    ? Path.Combine(Globals.DataFolder, "alternative", "ashare-market-sentiment")
                    : _baseDirectory
                : Path.GetFullPath(baseDirectory);

            return Path.Combine(root, "sse", "daily", "market_sentiment.csv");
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
            if (csv.Length < 26)
                return null;

            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
                return null;

            return new AShareMarketSentimentData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(csv[1]) ?? 0m,
                BasisComposite = ParseNullableDecimal(csv[1]),
                BasisIf = ParseNullableDecimal(csv[2]),
                BasisIc = ParseNullableDecimal(csv[3]),
                BasisIh = ParseNullableDecimal(csv[4]),
                BasisIm = ParseNullableDecimal(csv[5]),
                PcrHoVol = ParseNullableDecimal(csv[6]),
                PcrHoOi = ParseNullableDecimal(csv[7]),
                PcrIoVol = ParseNullableDecimal(csv[8]),
                PcrIoOi = ParseNullableDecimal(csv[9]),
                PcrMoVol = ParseNullableDecimal(csv[10]),
                PcrMoOi = ParseNullableDecimal(csv[11]),
                PcrComposite = ParseNullableDecimal(csv[12]),
                Vix50 = ParseNullableDecimal(csv[13]),
                Vix300 = ParseNullableDecimal(csv[14]),
                Vix500 = ParseNullableDecimal(csv[15]),
                VixComposite = ParseNullableDecimal(csv[16]),
                MarginSseLong = ParseNullableDecimal(csv[17]),
                MarginSseShort = ParseNullableDecimal(csv[18]),
                MarginSseRatio = ParseNullableDecimal(csv[19]),
                MarginSzseLong = ParseNullableDecimal(csv[20]),
                MarginSzseShort = ParseNullableDecimal(csv[21]),
                MarginSzseRatio = ParseNullableDecimal(csv[22]),
                MarginComposite = ParseNullableDecimal(csv[23]),
                IsExtremeFear = csv[24].Trim().Equals("True", StringComparison.OrdinalIgnoreCase),
                IsExtremeGreed = csv[25].Trim().Equals("True", StringComparison.OrdinalIgnoreCase),
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
    }
}
