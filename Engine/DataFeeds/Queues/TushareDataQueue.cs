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
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Packets;

namespace QuantConnect.Lean.Engine.DataFeeds.Queues
{
    /// <summary>
    /// Data queue handler for Tushare data (live-paper simulation)
    /// </summary>
    public class TushareDataQueue : IDataQueueHandler
    {
        private TushareDataConverter _converter;
        private string _dataPath;
        private readonly HashSet<Symbol> _subscribedSymbols = new HashSet<Symbol>();
        private readonly object _lock = new object();

        /// <summary>
        /// Subscribe to the specified configuration
        /// </summary>
        public IEnumerator<BaseData> Subscribe(SubscriptionDataConfig dataConfig, EventHandler newDataAvailableHandler)
        {
            lock (_lock)
            {
                _subscribedSymbols.Add(dataConfig.Symbol);
            }

            Log.Trace($"TushareDataQueue.Subscribe(): Subscribed to {dataConfig.Symbol}");

            // For live-paper, we simulate real-time data by yielding the latest available data
            while (true)
            {
                var tsCode = ConvertSymbolToTsCode(dataConfig.Symbol);
                if (!string.IsNullOrEmpty(tsCode))
                {
                    var latestBar = _converter.GetLatestData(tsCode);
                    if (latestBar != null)
                    {
                        yield return latestBar;
                    }
                }

                // Wait before next update (simulate real-time delay)
                System.Threading.Thread.Sleep(TimeSpan.FromSeconds(60));
            }
        }

        /// <summary>
        /// Unsubscribe from the specified configuration
        /// </summary>
        public void Unsubscribe(SubscriptionDataConfig dataConfig)
        {
            lock (_lock)
            {
                _subscribedSymbols.Remove(dataConfig.Symbol);
            }

            Log.Trace($"TushareDataQueue.Unsubscribe(): Unsubscribed from {dataConfig.Symbol}");
        }

        /// <summary>
        /// Sets the job we're subscribing for
        /// </summary>
        public void SetJob(LiveNodePacket job)
        {
            _dataPath = Globals.DataFolder;
            _converter = new TushareDataConverter(_dataPath);
            Log.Trace($"TushareDataQueue.SetJob(): Initialized with data path: {_dataPath}");
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
                _subscribedSymbols.Clear();
            }
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
    }
}
