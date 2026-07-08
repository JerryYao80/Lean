using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// FRED 宏观序列 custom data。Symbol.Value 决定文件: "VIX"->vix.csv, "DFII10"->dfii10.csv。
    /// 列: date,value。时区 UTC 日级对齐。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §6.1。
    /// </summary>
    public class FredMacroData : BaseData
    {
        public decimal MacroValue { get; set; }
        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("date")) return null;
            var csv = line.Split(',');
            if (csv.Length < 2) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            if (!decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var v)) return null;
            return new FredMacroData { Symbol = config.Symbol, Time = d, MacroValue = v, Value = v };
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var fname = config.Symbol.Value.ToLower() + ".csv";
            var path = Path.Combine(Globals.DataFolder, "macro", "fred", fname);
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
