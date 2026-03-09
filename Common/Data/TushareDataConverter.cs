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
using System.IO;
using System.Linq;
using NodaTime;
using Newtonsoft.Json;
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
        private readonly Dictionary<string, List<TradeBar>> _dailyDataBySymbol = new Dictionary<string, List<TradeBar>>();
        private readonly object _dailyDataLock = new object();
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
            try
            {
                var allBars = GetOrLoadDailyData(tsCode);
                var start = startDate.Date;
                var end = endDate.Date;

                var result = allBars
                    .Where(bar => bar.EndTime.Date >= start && bar.EndTime.Date <= end)
                    .Select(bar => new TradeBar(bar))
                    .ToList();

                Log.Trace($"TushareDataConverter.GetDailyData(): Loaded {result.Count} bars for {tsCode}");
                return result;
            }
            catch (Exception ex)
            {
                Log.Error($"TushareDataConverter.GetDailyData(): Error reading data for {tsCode}: {ex.Message}");
                return new List<TradeBar>();
            }
        }

        /// <summary>
        /// Gets the most recent trading data for a symbol (for live-paper simulation)
        /// </summary>
        public TradeBar GetLatestData(string tsCode)
        {
            var allBars = GetOrLoadDailyData(tsCode);
            if (allBars.Count == 0)
            {
                return null;
            }

            return new TradeBar(allBars[allBars.Count - 1]);
        }

        private List<TradeBar> GetOrLoadDailyData(string tsCode)
        {
            lock (_dailyDataLock)
            {
                if (_dailyDataBySymbol.TryGetValue(tsCode, out var cachedBars))
                {
                    return cachedBars;
                }
            }

            var loadedBars = LoadDailyData(tsCode);

            lock (_dailyDataLock)
            {
                if (!_dailyDataBySymbol.ContainsKey(tsCode))
                {
                    _dailyDataBySymbol[tsCode] = loadedBars;
                }

                return _dailyDataBySymbol[tsCode];
            }
        }

        private List<TradeBar> LoadDailyData(string tsCode)
        {
            var symbol = ConvertToSymbol(tsCode);
            var dailyPath = ResolveDailyDataPath(tsCode);

            if (dailyPath == null)
            {
                Log.Error($"TushareDataConverter.LoadDailyData(): Daily data file not found for {tsCode} in fund_daily or daily datasets under {_dataPath}");
                return new List<TradeBar>();
            }

            var pythonCode = $@"
import pandas as pd
import json

df = pd.read_parquet('{dailyPath}')
df = df.sort_values('trade_date')
print(df.to_json(orient='records'))
";

            var output = ExecutePython(pythonCode, $"TushareDataConverter.LoadDailyData({tsCode})");
            if (string.IsNullOrWhiteSpace(output))
            {
                return new List<TradeBar>();
            }

            var data = JsonConvert.DeserializeObject<List<Dictionary<string, object>>>(output)
                ?? new List<Dictionary<string, object>>();

            var result = new List<TradeBar>(data.Count);
            foreach (var row in data)
            {
                var tradeDate = row["trade_date"].ToString();
                var time = ConvertTradeDate(tradeDate);

                var tradeBar = new TradeBar
                {
                    Symbol = symbol,
                    Time = time.AddDays(-1),
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

            Log.Trace($"TushareDataConverter.LoadDailyData(): Cached {result.Count} bars for {tsCode}");
            return result;
        }

        private string ResolveDailyDataPath(string tsCode)
        {
            var fundDailyPath = Path.Combine(_dataPath, "fund_daily", $"ts_code={tsCode}", "data.parquet");
            if (File.Exists(fundDailyPath))
            {
                return fundDailyPath;
            }

            var stockDailyPath = Path.Combine(_dataPath, "daily", $"ts_code={tsCode}", "data.parquet");
            if (File.Exists(stockDailyPath))
            {
                return stockDailyPath;
            }

            return null;
        }

        private static string ExecutePython(string pythonCode, string context)
        {
            var pythonPath = "/root/miniconda3/envs/quant311/bin/python";
            var tempFile = Path.GetTempFileName();
            File.WriteAllText(tempFile, pythonCode);

            try
            {
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

                if (process.ExitCode != 0 || !string.IsNullOrWhiteSpace(error))
                {
                    Log.Error($"{context}: Python error: {error}");
                    return null;
                }

                return output;
            }
            finally
            {
                File.Delete(tempFile);
            }
        }
    }
}
