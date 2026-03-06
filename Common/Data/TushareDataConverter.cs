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
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using NodaTime;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Logging;

namespace QuantConnect.Data
{
    /// <summary>
    /// Converts Tushare parquet data to LEAN format
    /// </summary>
    public class TushareDataConverter
    {
        private readonly string _dataPath;
        private readonly TushareDataCache _cache;
        private static readonly DateTimeZone ChinaTimeZone = DateTimeZoneProviders.Tzdb["Asia/Shanghai"];

        /// <summary>
        /// Creates a new instance of TushareDataConverter
        /// </summary>
        /// <param name="dataPath">Path to tushare data directory</param>
        public TushareDataConverter(string dataPath)
        {
            _dataPath = dataPath;
            _cache = new TushareDataCache(dataPath);
        }

        /// <summary>
        /// Gets the list of T+0 tradable ETF symbols
        /// </summary>
        public List<string> GetT0ETFSymbols()
        {
            return _cache.GetT0ETFSymbols();
        }

        /// <summary>
        /// Gets ETF metadata by ts_code
        /// </summary>
        public Dictionary<string, object> GetETFMetadata(string tsCode)
        {
            return _cache.GetETFMetadata(tsCode);
        }

        /// <summary>
        /// Converts a tushare ts_code to LEAN Symbol
        /// </summary>
        /// <param name="tsCode">Tushare ts_code (e.g., "510050.SH")</param>
        /// <returns>LEAN Symbol</returns>
        public static Symbol ConvertToSymbol(string tsCode)
        {
            if (string.IsNullOrEmpty(tsCode))
            {
                throw new ArgumentException("ts_code cannot be null or empty");
            }

            var parts = tsCode.Split('.');
            if (parts.Length != 2)
            {
                throw new ArgumentException($"Invalid ts_code format: {tsCode}");
            }

            var ticker = parts[0];
            var exchange = parts[1];

            // Determine market based on exchange
            var market = exchange.ToUpperInvariant() switch
            {
                "SH" => QuantConnect.Market.SSE,
                "SZ" => QuantConnect.Market.SZSE,
                _ => QuantConnect.Market.China
            };

            return Symbol.Create(ticker, SecurityType.Equity, market);
        }

        /// <summary>
        /// Converts tushare trade_date (YYYYMMDD) to DateTime in UTC
        /// </summary>
        /// <param name="tradeDate">Trade date in YYYYMMDD format</param>
        /// <returns>DateTime in UTC representing market close (15:00 CST)</returns>
        public static DateTime ConvertTradeDate(string tradeDate)
        {
            if (string.IsNullOrEmpty(tradeDate) || tradeDate.Length != 8)
            {
                throw new ArgumentException($"Invalid trade_date format: {tradeDate}");
            }

            var year = int.Parse(tradeDate.Substring(0, 4));
            var month = int.Parse(tradeDate.Substring(4, 2));
            var day = int.Parse(tradeDate.Substring(6, 2));

            // Market close time: 15:00 CST
            var localDateTime = new LocalDateTime(year, month, day, 15, 0);
            var zonedDateTime = ChinaTimeZone.AtLeniently(localDateTime);

            return zonedDateTime.ToDateTimeUtc();
        }

        /// <summary>
        /// Converts tushare volume (in 手, 100 shares per lot) to shares
        /// </summary>
        /// <param name="vol">Volume in 手 (lots)</param>
        /// <returns>Volume in shares</returns>
        public static decimal ConvertVolume(decimal vol)
        {
            return vol * 100m;
        }

        /// <summary>
        /// Reads daily data for a specific symbol from tushare parquet files
        /// </summary>
        /// <param name="tsCode">Tushare ts_code</param>
        /// <param name="startDate">Start date (inclusive)</param>
        /// <param name="endDate">End date (inclusive)</param>
        /// <returns>List of TradeBars</returns>
        public List<TradeBar> GetDailyData(string tsCode, DateTime startDate, DateTime endDate)
        {
            var result = new List<TradeBar>();
            var symbol = ConvertToSymbol(tsCode);

            // Path to fund daily data: fund_daily/ts_code={tsCode}/data.parquet
            var dailyPath = Path.Combine(_dataPath, "fund_daily", $"ts_code={tsCode}", "data.parquet");

            if (!File.Exists(dailyPath))
            {
                Log.Error($"TushareDataConverter.GetDailyData(): Daily data file not found: {dailyPath}");
                return result;
            }

            try
            {
                // Use Python to read parquet file
                var pythonCode = $@"
import pandas as pd
import json

df = pd.read_parquet('{dailyPath}')
df = df.sort_values('trade_date')

# Filter by date range
start_date = '{startDate:yyyyMMdd}'
end_date = '{endDate:yyyyMMdd}'
df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]

# Convert to JSON
result = df.to_json(orient='records')
print(result)
";

                var pythonPath = "/root/miniconda3/envs/quant311/bin/python";
                var tempFile = Path.GetTempFileName();
                File.WriteAllText(tempFile, pythonCode);

                var process = new System.Diagnostics.Process
                {
                    StartInfo = new System.Diagnostics.ProcessStartInfo
                    {
                        FileName = pythonPath,
                        Arguments = tempFile,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true,
                        UseShellExecute = false,
                        CreateNoWindow = true
                    }
                };

                process.Start();
                var output = process.StandardOutput.ReadToEnd();
                var error = process.StandardError.ReadToEnd();
                process.WaitForExit();

                File.Delete(tempFile);

                if (!string.IsNullOrEmpty(error))
                {
                    Log.Error($"TushareDataConverter.GetDailyData(): Python error: {error}");
                    return result;
                }

                // Parse JSON output
                var data = Newtonsoft.Json.JsonConvert.DeserializeObject<List<Dictionary<string, object>>>(output);

                foreach (var row in data)
                {
                    var tradeDate = row["trade_date"].ToString();
                    var time = ConvertTradeDate(tradeDate);

                    var tradeBar = new TradeBar
                    {
                        Symbol = symbol,
                        Time = time.AddDays(-1), // Start of bar (previous day 15:00)
                        EndTime = time,
                        Open = Convert.ToDecimal(row["open"]),
                        High = Convert.ToDecimal(row["high"]),
                        Low = Convert.ToDecimal(row["low"]),
                        Close = Convert.ToDecimal(row["close"]),
                        Volume = ConvertVolume(Convert.ToDecimal(row["vol"])),
                        Period = TimeSpan.FromDays(1)
                    };

                    result.Add(tradeBar);
                }

                Log.Trace($"TushareDataConverter.GetDailyData(): Loaded {result.Count} bars for {tsCode}");
            }
            catch (Exception ex)
            {
                Log.Error($"TushareDataConverter.GetDailyData(): Error reading data for {tsCode}: {ex.Message}");
            }

            return result;
        }

        /// <summary>
        /// Gets the most recent trading data for a symbol (for live-paper simulation)
        /// </summary>
        public TradeBar GetLatestData(string tsCode)
        {
            var endDate = DateTime.UtcNow;
            var startDate = endDate.AddDays(-10); // Get last 10 days to ensure we have data

            var bars = GetDailyData(tsCode, startDate, endDate);
            return bars.LastOrDefault();
        }
    }
}
