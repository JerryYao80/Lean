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
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using NodaTime;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Logging;
using QuantConnect.Packets;
using QuantConnect.Securities;

namespace QuantConnect.Lean.Engine.HistoricalData
{
    /// <summary>
    /// History provider that implements a 3-step fallback chain:
    /// 1. Read from local tushare parquet files
    /// 2. Download from Tushare API on demand
    /// 3. Generate GBM synthetic data
    /// Tracks per-symbol data provenance throughout the process.
    /// </summary>
    public class FallbackTushareHistoryProvider : SynchronizingHistoryProvider
    {
        private TushareDataConverter _converter;
        private string _dataPath;
        private bool _downloadEnabled;
        private bool _gbmEnabled;
        private int _downloadTimeoutSeconds;
        private double _gbmVolatilityScale;
        private double _gbmMinDailyVolatility;
        private double _gbmJumpProbability;
        private double _gbmJumpScale;
        private int _gbmRandomSeed;
        private int _gbmLookbackDays;

        private readonly DataProvenanceTracker _provenanceTracker = new();
        private readonly ConcurrentDictionary<string, Task<bool>> _downloadLocks = new(StringComparer.OrdinalIgnoreCase);

        /// <summary>
        /// Initializes this history provider with fallback configuration
        /// </summary>
        public override void Initialize(HistoryProviderInitializeParameters parameters)
        {
            _dataPath = GetDataPath(parameters.Job);
            _converter = new TushareDataConverter(_dataPath);

            var paramsDict = parameters.Job?.Parameters ?? new Dictionary<string, string>();

            _downloadEnabled = GetBoolParam(paramsDict, "fallback-download-enabled", true);
            _gbmEnabled = GetBoolParam(paramsDict, "fallback-gbm-enabled", true);
            _downloadTimeoutSeconds = GetIntParam(paramsDict, "fallback-download-timeout-seconds", 120);
            _gbmVolatilityScale = GetDoubleParam(paramsDict, "fallback-gbm-volatility-scale", 8.0);
            _gbmMinDailyVolatility = GetDoubleParam(paramsDict, "fallback-gbm-min-daily-volatility", 0.80);
            _gbmJumpProbability = GetDoubleParam(paramsDict, "fallback-gbm-jump-probability", 0.22);
            _gbmJumpScale = GetDoubleParam(paramsDict, "fallback-gbm-jump-scale", 0.10);
            _gbmRandomSeed = GetIntParam(paramsDict, "fallback-gbm-random-seed", 42);
            _gbmLookbackDays = GetIntParam(paramsDict, "fallback-gbm-lookback-days", 60);

            DataProvenanceTracker.Current = _provenanceTracker;

            Log.Trace($"FallbackTushareHistoryProvider.Initialize(): data path={_dataPath}, download={_downloadEnabled}, gbm={_gbmEnabled}");
        }

        private static string GetDataPath(AlgorithmNodePacket job)
        {
            if (job?.Parameters != null && job.Parameters.TryGetValue("tushare-data-path", out var configuredPath)
                && !string.IsNullOrWhiteSpace(configuredPath))
            {
                return configuredPath;
            }

            return Globals.DataFolder;
        }

        private static bool GetBoolParam(Dictionary<string, string> paramsDict, string key, bool defaultValue)
        {
            if (paramsDict != null && paramsDict.TryGetValue(key, out var value))
            {
                if (bool.TryParse(value, out var parsed)) return parsed;
            }
            return defaultValue;
        }

        private static int GetIntParam(Dictionary<string, string> paramsDict, string key, int defaultValue)
        {
            if (paramsDict != null && paramsDict.TryGetValue(key, out var value))
            {
                if (int.TryParse(value, out var parsed)) return parsed;
            }
            return defaultValue;
        }

        private static double GetDoubleParam(Dictionary<string, string> paramsDict, string key, double defaultValue)
        {
            if (paramsDict != null && paramsDict.TryGetValue(key, out var value))
            {
                if (double.TryParse(value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var parsed)) return parsed;
            }
            return defaultValue;
        }

        /// <summary>
        /// Gets history for the requested securities with fallback chain
        /// </summary>
        public override IEnumerable<Slice> GetHistory(IEnumerable<Data.HistoryRequest> requests, DateTimeZone sliceTimeZone)
        {
            var subscriptions = new List<Subscription>();

            foreach (var request in requests)
            {
                var history = GetHistoryForRequest(request);
                if (history != null && history.Any())
                {
                    var subscription = CreateSubscription(request, history);
                    subscriptions.Add(subscription);
                }
            }

            if (subscriptions.Count == 0)
            {
                return Enumerable.Empty<Slice>();
            }

            return CreateSliceEnumerableFromSubscriptions(subscriptions, sliceTimeZone);
        }

        private IEnumerable<BaseData> GetHistoryForRequest(Data.HistoryRequest request)
        {
            var tsCode = ConvertSymbolToTsCode(request.Symbol);

            if (string.IsNullOrEmpty(tsCode))
            {
                Log.Error($"FallbackTushareHistoryProvider: Could not convert symbol {request.Symbol} to ts_code");
                _provenanceTracker.Record(request.Symbol.Value, "missing", 0, request.StartTimeUtc, request.EndTimeUtc);
                yield break;
            }

            if (request.Resolution != Resolution.Daily)
            {
                Log.Error($"FallbackTushareHistoryProvider: Only daily resolution is supported, requested: {request.Resolution}");
                _provenanceTracker.Record(tsCode, "missing", 0, request.StartTimeUtc, request.EndTimeUtc);
                yield break;
            }

            // Step 1: Try local tushare parquet data
            var bars = _converter.GetDailyData(tsCode, request.StartTimeUtc, request.EndTimeUtc);
            if (bars.Count > 0)
            {
                _provenanceTracker.Record(tsCode, "local-parquet", bars.Count, request.StartTimeUtc, request.EndTimeUtc);
                Log.Trace($"FallbackTushareHistoryProvider: {tsCode} → local-parquet ({bars.Count} bars)");
                foreach (var bar in bars)
                {
                    yield return bar;
                }
                yield break;
            }

            // Step 2: Try downloading from Tushare API
            if (_downloadEnabled)
            {
                Log.Trace($"FallbackTushareHistoryProvider: {tsCode} not found locally, attempting download...");
                var downloadSuccess = DownloadSymbolSynchronized(tsCode, request.StartTimeUtc, request.EndTimeUtc);

                if (downloadSuccess)
                {
                    // Clear cache for this symbol so converter re-reads from disk
                    _converter.ClearCacheForSymbol(tsCode);

                    bars = _converter.GetDailyData(tsCode, request.StartTimeUtc, request.EndTimeUtc);
                    if (bars.Count > 0)
                    {
                        _provenanceTracker.Record(tsCode, "tushare-download", bars.Count, request.StartTimeUtc, request.EndTimeUtc);
                        Log.Trace($"FallbackTushareHistoryProvider: {tsCode} → tushare-download ({bars.Count} bars)");
                        foreach (var bar in bars)
                        {
                            yield return bar;
                        }
                        yield break;
                    }
                }
            }

            // Step 3: Generate GBM synthetic data
            if (_gbmEnabled)
            {
                Log.Trace($"FallbackTushareHistoryProvider: {tsCode} download failed or disabled, generating GBM synthetic data...");

                // Try to load any available history for calibration (even partial/out-of-range)
                var anyHistory = _converter.GetDailyData(tsCode, DateTime.MinValue, DateTime.MaxValue);
                var calibration = GbmDailyBarGenerator.Calibrate(tsCode, anyHistory, _gbmLookbackDays);

                bars = GbmDailyBarGenerator.Generate(
                    request.Symbol,
                    request.StartTimeUtc,
                    request.EndTimeUtc,
                    calibration,
                    _gbmVolatilityScale,
                    _gbmMinDailyVolatility,
                    _gbmJumpProbability,
                    _gbmJumpScale,
                    _gbmRandomSeed);

                if (bars.Count > 0)
                {
                    _provenanceTracker.Record(tsCode, "gbm-synthetic", bars.Count, request.StartTimeUtc, request.EndTimeUtc);
                    Log.Trace($"FallbackTushareHistoryProvider: {tsCode} → gbm-synthetic ({bars.Count} bars)");
                    foreach (var bar in bars)
                    {
                        yield return bar;
                    }
                    yield break;
                }
            }

            // All steps failed
            _provenanceTracker.Record(tsCode, "missing", 0, request.StartTimeUtc, request.EndTimeUtc);
            Log.Error($"FallbackTushareHistoryProvider: {tsCode} — all fallback steps failed, no data available");
            yield break;
        }

        /// <summary>
        /// Synchronized download: prevents duplicate downloads for the same symbol
        /// </summary>
        private bool DownloadSymbolSynchronized(string tsCode, DateTime startDate, DateTime endDate)
        {
            var downloadTask = _downloadLocks.GetOrAdd(tsCode, _ =>
                Task.Run(() => TushareDownloaderInvoker.DownloadSymbol(tsCode, startDate, endDate, _dataPath, _downloadTimeoutSeconds)));

            try
            {
                return downloadTask.Result;
            }
            catch (Exception ex)
            {
                Log.Error($"FallbackTushareHistoryProvider: Download task failed for {tsCode}: {ex.Message}");
                return false;
            }
        }

        private string ConvertSymbolToTsCode(Symbol symbol)
        {
            if (symbol == null || symbol.SecurityType != SecurityType.Equity)
            {
                return null;
            }

            var ticker = symbol.Value;
            var market = symbol.ID.Market;

            var suffix = market switch
            {
                Market.SSE => "SH",
                Market.SZSE => "SZ",
                Market.China => "SH",
                _ => null
            };

            if (suffix == null)
            {
                return null;
            }

            return $"{ticker}.{suffix}";
        }
    }
}