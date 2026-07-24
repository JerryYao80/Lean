using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// AU.SHF 期货日线 custom data(辅助趋势确认,不交易)。
    /// 列: date,open,high,low,close,volume[,open_interest]。yyyy-MM-dd 格式。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §6.4。
    /// </summary>
    public class AuShfDailyBar : BaseData
    {
        public decimal Open { get; set; }
        public decimal High { get; set; }
        public decimal Low { get; set; }
        public decimal Close { get; set; }
        public decimal Volume { get; set; }
        public decimal OpenInterest { get; set; }
        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("date")) return null;
            var csv = line.Split(',');
            if (csv.Length < 5) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            var bar = new AuShfDailyBar { Symbol = config.Symbol, Time = d };
            bar.Open = decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var o) ? o : 0m;
            bar.High = decimal.TryParse(csv[2], NumberStyles.Any, CultureInfo.InvariantCulture, out var h) ? h : 0m;
            bar.Low = decimal.TryParse(csv[3], NumberStyles.Any, CultureInfo.InvariantCulture, out var l) ? l : 0m;
            bar.Close = decimal.TryParse(csv[4], NumberStyles.Any, CultureInfo.InvariantCulture, out var c) ? c : 0m;
            if (csv.Length > 5) bar.Volume = decimal.TryParse(csv[5], NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;
            if (csv.Length > 6) bar.OpenInterest = decimal.TryParse(csv[6], NumberStyles.Any, CultureInfo.InvariantCulture, out var oi) ? oi : 0m;
            bar.Value = bar.Close;
            return bar;
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var path = Path.Combine(Globals.DataFolder, "future", "shf", "daily", "AU.SHF.csv");
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
