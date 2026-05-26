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
        private readonly string _livePriceSnapshotPath;
        private readonly TushareDataCache _cache;
        private readonly Dictionary<string, List<TradeBar>> _dailyDataBySymbol = new Dictionary<string, List<TradeBar>>();
        private readonly object _dailyDataLock = new object();
        private readonly Dictionary<string, LatestDailyBarCacheEntry> _latestDailyBarBySymbol = new Dictionary<string, LatestDailyBarCacheEntry>();
        private readonly object _latestDailyBarLock = new object();
        private readonly Dictionary<string, TradeBar> _liveSnapshotBarsBySymbol = new Dictionary<string, TradeBar>(StringComparer.OrdinalIgnoreCase);
        private readonly object _liveSnapshotLock = new object();
        private DateTime _liveSnapshotLastWriteTimeUtc;
        private static readonly DateTimeZone ChinaTimeZone = DateTimeZoneProviders.Tzdb["Asia/Shanghai"];

        /// <summary>
        /// Creates a new instance of TushareDataConverter
        /// </summary>
        /// <param name="dataPath">Path to tushare data directory</param>
        public TushareDataConverter(string dataPath)
            : this(dataPath, null)
        {
        }

        public TushareDataConverter(string dataPath, string livePriceSnapshotPath)
        {
            _dataPath = dataPath;
            _livePriceSnapshotPath = livePriceSnapshotPath;
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
                var result = FilterAvailableDailyBars(allBars, startDate, endDate);

                Log.Trace($"TushareDataConverter.GetDailyData(): Loaded {result.Count} bars for {tsCode}");
                return result;
            }
            catch (Exception ex)
            {
                Log.Error($"TushareDataConverter.GetDailyData(): Error reading data for {tsCode}: {ex.Message}");
                return new List<TradeBar>();
            }
        }

        public static List<TradeBar> FilterAvailableDailyBars(IEnumerable<TradeBar> bars, DateTime startDate, DateTime endDate)
        {
            if (bars == null)
            {
                return new List<TradeBar>();
            }

            var startUtc = NormalizeUtc(startDate);
            var endUtc = NormalizeUtc(endDate);
            return bars
                .Where(bar => NormalizeUtc(bar.EndTime) >= startUtc && NormalizeUtc(bar.EndTime) <= endUtc)
                .Select(bar => new TradeBar(bar))
                .ToList();
        }

        private static DateTime NormalizeUtc(DateTime value)
        {
            return value.Kind == DateTimeKind.Utc ? value : DateTime.SpecifyKind(value, DateTimeKind.Utc);
        }

        /// <summary>
        /// Gets the most recent trading data for a symbol (for live-paper simulation)
        /// </summary>
        public TradeBar GetLatestData(string tsCode)
        {
            if (TryGetLatestLiveSnapshotData(tsCode, out var liveBar, out var liveSnapshotAvailable))
            {
                return liveBar;
            }
            if (liveSnapshotAvailable)
            {
                return null;
            }

            var dailyPath = ResolveDailyDataPath(tsCode);
            if (dailyPath == null)
            {
                Log.Error($"TushareDataConverter.GetLatestData(): Daily data file not found for {tsCode} in fund_daily or daily datasets under {_dataPath}");
                return null;
            }

            var lastWriteTimeUtc = File.GetLastWriteTimeUtc(dailyPath);
            lock (_latestDailyBarLock)
            {
                if (_latestDailyBarBySymbol.TryGetValue(tsCode, out var cachedEntry)
                    && string.Equals(cachedEntry.SourcePath, dailyPath, StringComparison.Ordinal)
                    && cachedEntry.LastWriteTimeUtc == lastWriteTimeUtc)
                {
                    return cachedEntry.Bar == null ? null : new TradeBar(cachedEntry.Bar);
                }
            }

            var latestBar = LoadLatestDailyBar(tsCode, dailyPath);

            lock (_latestDailyBarLock)
            {
                _latestDailyBarBySymbol[tsCode] = new LatestDailyBarCacheEntry
                {
                    SourcePath = dailyPath,
                    LastWriteTimeUtc = lastWriteTimeUtc,
                    Bar = latestBar == null ? null : new TradeBar(latestBar)
                };
            }

            return latestBar == null ? null : new TradeBar(latestBar);
        }

        private bool TryGetLatestLiveSnapshotData(string tsCode, out TradeBar tradeBar, out bool liveSnapshotAvailable)
        {
            tradeBar = null;
            liveSnapshotAvailable = false;
            if (string.IsNullOrWhiteSpace(_livePriceSnapshotPath) || !File.Exists(_livePriceSnapshotPath))
            {
                return false;
            }

            liveSnapshotAvailable = true;
            LoadLiveSnapshotIfNeeded();

            lock (_liveSnapshotLock)
            {
                if (_liveSnapshotBarsBySymbol.TryGetValue(tsCode, out var cachedBar))
                {
                    tradeBar = new TradeBar(cachedBar);
                    return true;
                }
            }
            return false;
        }

        private void LoadLiveSnapshotIfNeeded()
        {
            if (string.IsNullOrWhiteSpace(_livePriceSnapshotPath) || !File.Exists(_livePriceSnapshotPath))
            {
                return;
            }

            var lastWriteTimeUtc = File.GetLastWriteTimeUtc(_livePriceSnapshotPath);
            lock (_liveSnapshotLock)
            {
                if (_liveSnapshotLastWriteTimeUtc == lastWriteTimeUtc)
                {
                    return;
                }
            }

            try
            {
                var payloadText = File.ReadAllText(_livePriceSnapshotPath);
                var payload = JsonConvert.DeserializeObject<LivePriceSnapshotPayload>(payloadText) ?? new LivePriceSnapshotPayload();
                var snapshotBars = new Dictionary<string, TradeBar>(StringComparer.OrdinalIgnoreCase);
                foreach (var row in payload.Quotes ?? new List<LivePriceSnapshotRow>())
                {
                    var liveTradeBar = CreateLiveTradeBar(row, payload.GeneratedAt);
                    if (liveTradeBar == null || string.IsNullOrWhiteSpace(row?.TsCode))
                    {
                        continue;
                    }

                    snapshotBars[row.TsCode] = liveTradeBar;
                }

                lock (_liveSnapshotLock)
                {
                    _liveSnapshotBarsBySymbol.Clear();
                    foreach (var pair in snapshotBars)
                    {
                        _liveSnapshotBarsBySymbol[pair.Key] = pair.Value;
                    }

                    _liveSnapshotLastWriteTimeUtc = lastWriteTimeUtc;
                }

                Log.Trace($"TushareDataConverter.LoadLiveSnapshotIfNeeded(): Loaded {snapshotBars.Count} realtime bars from {_livePriceSnapshotPath} write_time_utc={lastWriteTimeUtc:O}");
            }
            catch (Exception ex)
            {
                Log.Error($"TushareDataConverter.LoadLiveSnapshotIfNeeded(): Failed to parse {_livePriceSnapshotPath}: {ex.Message}");
            }
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

        /// <summary>
        /// Clear cached data for a symbol so it will be re-read from disk on next access
        /// </summary>
        public void ClearCacheForSymbol(string tsCode)
        {
            lock (_dailyDataLock)
            {
                _dailyDataBySymbol.Remove(tsCode);
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

            var escapedPath = EscapePythonString(dailyPath);
            var pythonCode = $@"
import pandas as pd
import json

df = pd.read_parquet('{escapedPath}')
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
                var tradeBar = CreateTradeBar(symbol, row);
                if (tradeBar != null)
                {
                    result.Add(tradeBar);
                }
            }

            Log.Trace($"TushareDataConverter.LoadDailyData(): Cached {result.Count} bars for {tsCode}");
            return result;
        }

        private TradeBar LoadLatestDailyBar(string tsCode, string dailyPath)
        {
            var symbol = ConvertToSymbol(tsCode);
            var escapedPath = EscapePythonString(dailyPath);
            var pythonCode = $@"
import pandas as pd
import json

df = pd.read_parquet('{escapedPath}')
if df.empty:
    print('[]')
else:
    df = df.sort_values('trade_date').tail(1)
    print(df.to_json(orient='records'))
";

            var output = ExecutePython(pythonCode, $"TushareDataConverter.LoadLatestDailyBar({tsCode})");
            if (string.IsNullOrWhiteSpace(output))
            {
                return null;
            }

            var data = JsonConvert.DeserializeObject<List<Dictionary<string, object>>>(output)
                ?? new List<Dictionary<string, object>>();
            if (data.Count == 0)
            {
                return null;
            }

            return CreateTradeBar(symbol, data[0]);
        }

        private static TradeBar CreateTradeBar(Symbol symbol, IReadOnlyDictionary<string, object> row)
        {
            if (row == null || !row.TryGetValue("trade_date", out var tradeDateValue) || tradeDateValue == null)
            {
                return null;
            }

            var tradeDate = tradeDateValue.ToString();
            var time = ConvertTradeDate(tradeDate);

            return new TradeBar
            {
                Symbol = symbol,
                Time = time.AddDays(-1),
                EndTime = time,
                Open = ConvertToDecimal(row, "open"),
                High = ConvertToDecimal(row, "high"),
                Low = ConvertToDecimal(row, "low"),
                Close = ConvertToDecimal(row, "close"),
                Volume = ConvertVolume(ConvertToDecimal(row, "vol")),
                Period = TimeSpan.FromDays(1)
            };
        }

        private static TradeBar CreateLiveTradeBar(LivePriceSnapshotRow row, string generatedAt)
        {
            if (row == null || string.IsNullOrWhiteSpace(row.TsCode))
            {
                return null;
            }

            var symbol = ConvertToSymbol(row.TsCode);
            var eventTimeUtc = ParseLiveTimestampUtc(row.FetchTimestamp)
                ?? ParseLiveTimestampUtc(generatedAt)
                ?? (!string.IsNullOrWhiteSpace(row.TradeDate) ? ConvertTradeDate(row.TradeDate) : DateTime.UtcNow);
            var barPeriod = TimeSpan.FromDays(1);
            var barStartTimeUtc = eventTimeUtc - barPeriod;

            var open = row.Open ?? row.PreClose ?? row.Close ?? row.Price ?? 0m;
            var close = row.Close ?? row.Price ?? row.Open ?? row.PreClose ?? 0m;
            var high = row.High ?? Math.Max(open, close);
            var low = row.Low ?? Math.Min(open, close);

            return new TradeBar
            {
                Symbol = symbol,
                Time = barStartTimeUtc,
                EndTime = eventTimeUtc,
                Open = open,
                High = high,
                Low = low,
                Close = close,
                Volume = ConvertVolume(row.Vol ?? 0m),
                Period = barPeriod
            };
        }

        private static decimal ConvertToDecimal(IReadOnlyDictionary<string, object> row, string fieldName)
        {
            if (row == null || !row.TryGetValue(fieldName, out var value) || value == null)
            {
                return 0m;
            }

            return Convert.ToDecimal(value);
        }

        private static string EscapePythonString(string value)
        {
            return value
                ?.Replace("\\", "\\\\")
                .Replace("'", "\\'")
                ?? string.Empty;
        }

        private static DateTime? ParseLiveTimestampUtc(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return null;
            }

            if (DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsedOffset))
            {
                return parsedOffset.UtcDateTime;
            }

            if (DateTime.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsedDateTime))
            {
                return parsedDateTime;
            }

            return null;
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

        private sealed class LatestDailyBarCacheEntry
        {
            public string SourcePath { get; set; }
            public DateTime LastWriteTimeUtc { get; set; }
            public TradeBar Bar { get; set; }
        }

        private sealed class LivePriceSnapshotPayload
        {
            [JsonProperty("generated_at")]
            public string GeneratedAt { get; set; }

            [JsonProperty("quotes")]
            public List<LivePriceSnapshotRow> Quotes { get; set; } = new List<LivePriceSnapshotRow>();
        }

        private sealed class LivePriceSnapshotRow
        {
            [JsonProperty("ts_code")]
            public string TsCode { get; set; }

            [JsonProperty("trade_date")]
            public string TradeDate { get; set; }

            [JsonProperty("open")]
            public decimal? Open { get; set; }

            [JsonProperty("high")]
            public decimal? High { get; set; }

            [JsonProperty("low")]
            public decimal? Low { get; set; }

            [JsonProperty("close")]
            public decimal? Close { get; set; }

            [JsonProperty("price")]
            public decimal? Price { get; set; }

            [JsonProperty("pre_close")]
            public decimal? PreClose { get; set; }

            [JsonProperty("pct_chg")]
            public decimal? PctChg { get; set; }

            [JsonProperty("vol")]
            public decimal? Vol { get; set; }

            [JsonProperty("amount")]
            public decimal? Amount { get; set; }

            [JsonProperty("fetch_timestamp")]
            public string FetchTimestamp { get; set; }
        }
    }
}
