using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// 黄金隔夜溢价信号 custom data。每 trade_date 一行，从导出器产出的 CSV 载入。
    /// 字段: trade_date, z_signal, regime, skip_reason, gap_expected, gap_actual, signal。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §2.2。
    /// </summary>
    public class GoldOvernightSignal : BaseData
    {
        public decimal ZSignal { get; set; }
        public string Regime { get; set; }
        public string SkipReason { get; set; }
        public decimal GapExpected { get; set; }
        public decimal GapActual { get; set; }
        public decimal Signal { get; set; }

        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date"))
                return null;
            var csv = line.Split(',');
            // CSV columns: trade_date, r_au_overnight, gap_expected, gap_actual, signal,
            //              z_signal, regime, skip_reason, freshness_flag, cross_check_alert, forward_return
            if (csv.Length < 8) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            var sig = new GoldOvernightSignal
            {
                Symbol = config.Symbol,
                Time = d,
                GapExpected = decimal.TryParse(csv[2], NumberStyles.Any, CultureInfo.InvariantCulture, out var ge) ? ge : 0m,
                GapActual = decimal.TryParse(csv[3], NumberStyles.Any, CultureInfo.InvariantCulture, out var ga) ? ga : 0m,
                Signal = decimal.TryParse(csv[4], NumberStyles.Any, CultureInfo.InvariantCulture, out var s) ? s : 0m,
                ZSignal = decimal.TryParse(csv[5], NumberStyles.Any, CultureInfo.InvariantCulture, out var z) ? z : 0m,
                Regime = csv[6],
                SkipReason = csv[7],
            };
            sig.Value = sig.ZSignal;
            return sig;
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var path = Path.Combine(Globals.DataFolder, "alternative", "gold-overnight-premium", "signals.csv");
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
