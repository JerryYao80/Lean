using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Daily multi-factor snapshot exported from Tushare A-share datasets.
    /// </summary>
    public class AShareLlmQuantFeatureData : BaseData
    {
        private static string _baseDirectory;

        public decimal? Close { get; set; }
        public decimal? PctChg { get; set; }
        public decimal? Pb { get; set; }
        public decimal? PsTtm { get; set; }
        public decimal? DvTtm { get; set; }
        public decimal? TurnoverRateF { get; set; }
        public decimal? CircMv { get; set; }
        public decimal? Momentum12020 { get; set; }
        public decimal? Return5 { get; set; }
        public decimal? Volatility20 { get; set; }
        public decimal? FlowRatio { get; set; }
        public decimal? BigFlowRatio { get; set; }
        public bool InUniverse { get; set; }

        public Symbol UnderlyingSymbol => Symbol != null && Symbol.HasUnderlying ? Symbol.Underlying : Symbol;

        public AShareLlmQuantFeatureData Copy()
        {
            return new AShareLlmQuantFeatureData
            {
                Symbol = Symbol,
                Time = Time,
                EndTime = EndTime,
                Value = Value,
                Close = Close,
                PctChg = PctChg,
                Pb = Pb,
                PsTtm = PsTtm,
                DvTtm = DvTtm,
                TurnoverRateF = TurnoverRateF,
                CircMv = CircMv,
                Momentum12020 = Momentum12020,
                Return5 = Return5,
                Volatility20 = Volatility20,
                FlowRatio = FlowRatio,
                BigFlowRatio = BigFlowRatio,
                InUniverse = InUniverse
            };
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
                    ? Path.Combine(Globals.DataFolder, "alternative", "ashare-llm-quant-features")
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
            if (csv.Length < 14)
            {
                return null;
            }

            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
            {
                return null;
            }

            return new AShareLlmQuantFeatureData
            {
                Symbol = config.Symbol,
                Time = tradeDate.Date,
                EndTime = tradeDate.Date,
                Value = ParseNullableDecimal(csv[1]) ?? 0m,
                Close = ParseNullableDecimal(csv[1]),
                PctChg = ParseNullableDecimal(csv[2]),
                Pb = ParseNullableDecimal(csv[3]),
                PsTtm = ParseNullableDecimal(csv[4]),
                DvTtm = ParseNullableDecimal(csv[5]),
                TurnoverRateF = ParseNullableDecimal(csv[6]),
                CircMv = ParseNullableDecimal(csv[7]),
                Momentum12020 = ParseNullableDecimal(csv[8]),
                Return5 = ParseNullableDecimal(csv[9]),
                Volatility20 = ParseNullableDecimal(csv[10]),
                FlowRatio = ParseNullableDecimal(csv[11]),
                BigFlowRatio = ParseNullableDecimal(csv[12]),
                InUniverse = ParseNullableInt(csv[13]).GetValueOrDefault() != 0
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
