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
using System.Threading;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Packets;
using QuantConnect.Util;
using Timer = System.Timers.Timer;

namespace QuantConnect.Lean.Engine.DataFeeds.Queues
{
    /// <summary>
    /// Data queue handler for Tushare data (live-paper simulation)
    /// </summary>
    public class TushareDataQueue : IDataQueueHandler
    {
        private const int DefaultRefreshIntervalSeconds = 5;
        private TushareDataConverter _converter;
        private string _dataPath;
        private string _livePriceSnapshotPath;
        private readonly IDataAggregator _aggregator;
        private readonly HashSet<Symbol> _subscribedSymbols = new HashSet<Symbol>();
        private readonly Dictionary<Symbol, string> _lastEmissionSignatureBySymbol = new Dictionary<Symbol, string>();
        private readonly Timer _pollTimer;
        private readonly object _lock = new object();
        private long _emissionSequence;
        private int _refreshIntervalSeconds = DefaultRefreshIntervalSeconds;
        private int _pollCycle;
        private int _publishInProgress;
        private bool _disposed;

        public TushareDataQueue()
            : this(Composer.Instance.GetExportedValueByTypeName<IDataAggregator>(nameof(AggregationManager)))
        {
        }

        internal TushareDataQueue(IDataAggregator aggregator)
        {
            _aggregator = aggregator;
            _pollTimer = new Timer
            {
                AutoReset = false,
                Enabled = false,
                Interval = TimeSpan.FromSeconds(DefaultRefreshIntervalSeconds).TotalMilliseconds
            };
            _pollTimer.Elapsed += (_, __) =>
            {
                try
                {
                    PublishAllLatestBars("timer");
                }
                finally
                {
                    ScheduleNextPoll();
                }
            };
        }

        /// <summary>
        /// Subscribe to the specified configuration
        /// </summary>
        public IEnumerator<BaseData> Subscribe(SubscriptionDataConfig dataConfig, EventHandler newDataAvailableHandler)
        {
            var enumerator = _aggregator.Add(dataConfig, newDataAvailableHandler);
            lock (_lock)
            {
                _subscribedSymbols.Add(dataConfig.Symbol);
                EnsurePolling_NoLock();
            }

            Log.Trace($"TushareDataQueue.Subscribe(): Subscribed to {dataConfig.Symbol} active={GetSubscriptionCount()}");
            PublishLatestBar(dataConfig.Symbol, "subscribe");
            return enumerator;
        }

        /// <summary>
        /// Unsubscribe from the specified configuration
        /// </summary>
        public void Unsubscribe(SubscriptionDataConfig dataConfig)
        {
            _aggregator.Remove(dataConfig);
            lock (_lock)
            {
                _subscribedSymbols.Remove(dataConfig.Symbol);
                _lastEmissionSignatureBySymbol.Remove(dataConfig.Symbol);
                if (_subscribedSymbols.Count == 0)
                {
                    _pollTimer.Stop();
                }
            }

            Log.Trace($"TushareDataQueue.Unsubscribe(): Unsubscribed from {dataConfig.Symbol} active={GetSubscriptionCount()}");
        }

        /// <summary>
        /// Sets the job we're subscribing for
        /// </summary>
        public void SetJob(LiveNodePacket job)
        {
            if (job?.Parameters != null && job.Parameters.TryGetValue("tushare-data-path", out var configuredPath)
                && !string.IsNullOrWhiteSpace(configuredPath))
            {
                _dataPath = configuredPath;
            }
            else
            {
                _dataPath = Globals.DataFolder;
            }

            if (job?.Parameters != null && job.Parameters.TryGetValue("live-price-snapshot-file", out var configuredSnapshotPath)
                && !string.IsNullOrWhiteSpace(configuredSnapshotPath))
            {
                _livePriceSnapshotPath = Path.IsPathRooted(configuredSnapshotPath)
                    ? configuredSnapshotPath
                    : Path.GetFullPath(Path.Combine(Globals.ResultsDestinationFolder, configuredSnapshotPath));
            }
            else
            {
                _livePriceSnapshotPath = null;
            }

            if (job?.Parameters != null && job.Parameters.TryGetValue("live-price-refresh-interval-seconds", out var configuredRefreshInterval)
                && int.TryParse(configuredRefreshInterval, out var parsedRefreshInterval))
            {
                _refreshIntervalSeconds = Math.Max(1, parsedRefreshInterval);
            }
            else
            {
                _refreshIntervalSeconds = DefaultRefreshIntervalSeconds;
            }

            _pollTimer.Interval = TimeSpan.FromSeconds(_refreshIntervalSeconds).TotalMilliseconds;

            _converter = new TushareDataConverter(_dataPath, _livePriceSnapshotPath);
            Log.Trace(
                $"TushareDataQueue.SetJob(): Initialized with data path: {_dataPath} " +
                $"live_price_snapshot={_livePriceSnapshotPath ?? "-"} refresh_interval_seconds={_refreshIntervalSeconds}");
        }

        /// <summary>
        /// Returns whether the data provider is connected
        /// </summary>
        public bool IsConnected => true;

        /// <summary>
        /// Performs application-defined tasks associated with freeing, releasing, or resetting unmanaged resources.
        /// </summary>
        public void Dispose()
        {
            lock (_lock)
            {
                _disposed = true;
                _subscribedSymbols.Clear();
                _lastEmissionSignatureBySymbol.Clear();
            }
            _pollTimer.Stop();
            _pollTimer.DisposeSafely();
        }

        private int GetSubscriptionCount()
        {
            lock (_lock)
            {
                return _subscribedSymbols.Count;
            }
        }

        private bool UpdateLastEmissionSignature(Symbol symbol, string signature)
        {
            lock (_lock)
            {
                var changed = !_lastEmissionSignatureBySymbol.TryGetValue(symbol, out var previousSignature)
                    || !string.Equals(previousSignature, signature, StringComparison.Ordinal);
                _lastEmissionSignatureBySymbol[symbol] = signature;
                return changed;
            }
        }

        private static string BuildBarSignature(TradeBar tradeBar)
        {
            if (tradeBar == null)
            {
                return string.Empty;
            }

            return $"{tradeBar.Open:F4}|{tradeBar.High:F4}|{tradeBar.Low:F4}|{tradeBar.Close:F4}|{tradeBar.Volume:F0}";
        }

        private void EnsurePolling_NoLock()
        {
            if (_disposed || _pollTimer.Enabled || _subscribedSymbols.Count == 0)
            {
                return;
            }
            _pollTimer.Start();
        }

        private void ScheduleNextPoll()
        {
            lock (_lock)
            {
                if (_disposed || _subscribedSymbols.Count == 0)
                {
                    return;
                }

                _pollTimer.Interval = TimeSpan.FromSeconds(_refreshIntervalSeconds).TotalMilliseconds;
                _pollTimer.Start();
            }
        }

        private void PublishAllLatestBars(string reason)
        {
            if (Interlocked.Exchange(ref _publishInProgress, 1) == 1)
            {
                return;
            }

            try
            {
                List<Symbol> symbols;
                lock (_lock)
                {
                    symbols = _subscribedSymbols.ToList();
                }

                if (symbols.Count == 0)
                {
                    return;
                }

                var cycle = Interlocked.Increment(ref _pollCycle);
                var publishedCount = 0;
                var missingCount = 0;
                var unchangedCount = 0;
                foreach (var symbol in symbols)
                {
                    switch (PublishLatestBar(symbol, reason, cycle))
                    {
                        case PublishStatus.Published:
                            publishedCount += 1;
                            break;
                        case PublishStatus.Missing:
                            missingCount += 1;
                            break;
                        default:
                            unchangedCount += 1;
                            break;
                    }
                }
                Log.Trace(
                    $"TushareDataQueue.Refresh(): cycle={cycle} reason={reason} subscribed={symbols.Count} " +
                    $"published={publishedCount} missing={missingCount} unchanged={unchangedCount}");
            }
            finally
            {
                Interlocked.Exchange(ref _publishInProgress, 0);
            }
        }

        private PublishStatus PublishLatestBar(Symbol symbol, string reason, int cycle = 0)
        {
            var tsCode = ConvertSymbolToTsCode(symbol);
            if (string.IsNullOrEmpty(tsCode))
            {
                return PublishStatus.Unchanged;
            }

            var latestBar = _converter.GetLatestData(tsCode);
            if (latestBar == null)
            {
                return PublishStatus.Missing;
            }

            var changed = UpdateLastEmissionSignature(symbol, BuildBarSignature(latestBar));
            if (!changed && !string.Equals(reason, "subscribe", StringComparison.Ordinal))
            {
                return PublishStatus.Unchanged;
            }

            var clone = new TradeBar(latestBar);
            Interlocked.Increment(ref _emissionSequence);
            _aggregator.Update(clone);
            return PublishStatus.Published;
        }

        /// <summary>
        /// Converts LEAN Symbol to Tushare ts_code
        /// </summary>
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

        private enum PublishStatus
        {
            Unchanged = 0,
            Missing = 1,
            Published = 2
        }
    }
}
