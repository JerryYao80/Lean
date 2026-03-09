using System;
using System.Collections.Generic;
using System.IO;
using QuantConnect.Data.Market;
using QuantConnect.Util;

namespace QuantConnect.Data.Custom
{
    /// <summary>
    /// A-share daily stock bar custom data backed by LEAN equity zip files.
    /// This provides a lightweight custom-data surface for T+1 workflows without
    /// affecting the default equity subscriptions used by existing T+0 ETF logic.
    /// </summary>
    public class AShareStockData : TradeBar
    {
        /// <summary>
        /// Tushare ts_code representation, for example 600000.SH
        /// </summary>
        public string TsCode { get; set; }

        /// <summary>
        /// A-share stock custom data is daily by default.
        /// </summary>
        public override Resolution DefaultResolution() => Resolution.Daily;

        /// <summary>
        /// A-share stock custom data currently supports daily resolution only.
        /// </summary>
        public override List<Resolution> SupportedResolutions() => DailyResolution;

        /// <summary>
        /// The backing file is already keyed by the concrete A-share symbol, so
        /// additional mapping is not required for this custom data type.
        /// </summary>
        public override bool RequiresMapping() => false;

        /// <summary>
        /// Resolves the local LEAN equity zip file path for the specified A-share symbol.
        /// </summary>
        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var source = GetLeanDataPath(Globals.DataFolder, config.Symbol, date, config.Resolution, config.TickType);
            return new SubscriptionDataSource(source, SubscriptionTransportMedium.LocalFile);
        }

        /// <summary>
        /// Parses a single LEAN equity daily line into an <see cref="AShareStockData"/> instance.
        /// </summary>
        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("Date", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            var data = TradeBar.ParseEquity<AShareStockData>(config, line, date);
            data.TsCode = ToTsCode(config.Symbol);
            return data;
        }

        /// <summary>
        /// Reads the next line from the stream and delegates to the line reader.
        /// </summary>
        public override BaseData Reader(SubscriptionDataConfig config, StreamReader stream, DateTime date, bool isLiveMode)
        {
            return Reader(config, stream?.ReadLine(), date, isLiveMode);
        }

        /// <summary>
        /// Generates the LEAN daily zip path for an A-share stock symbol while keeping
        /// the file storage under the equity hierarchy even when subscribed as custom data.
        /// </summary>
        public static string GetLeanDataPath(string dataFolder, Symbol symbol, DateTime date, Resolution resolution, TickType tickType = TickType.Trade)
        {
            var market = symbol?.ID.Market ?? global::QuantConnect.Market.China;
            var ticker = symbol?.Value ?? string.Empty;
            var equitySymbol = Symbol.Create(ticker, SecurityType.Equity, market);
            return LeanData.GenerateZipFilePath(dataFolder, equitySymbol, date, resolution, tickType);
        }

        /// <summary>
        /// Converts a LEAN A-share symbol to a tushare ts_code.
        /// </summary>
        public static string ToTsCode(Symbol symbol)
        {
            if (symbol == null || symbol == Symbol.Empty)
            {
                return string.Empty;
            }

            var suffix = symbol.ID.Market == global::QuantConnect.Market.SSE ? "SH" : "SZ";
            return $"{symbol.Value}.{suffix}";
        }
    }
}
