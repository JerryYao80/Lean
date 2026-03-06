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
using NodaTime;
using QuantConnect.Data;
using QuantConnect.Interfaces;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Logging;
using QuantConnect.Securities;

namespace QuantConnect.Lean.Engine.HistoricalData
{
    /// <summary>
    /// History provider for Tushare data (A-Share ETFs)
    /// </summary>
    public class TushareHistoryProvider : SynchronizingHistoryProvider
    {
        private TushareDataConverter _converter;
        private string _dataPath;

        /// <summary>
        /// Initializes this history provider
        /// </summary>
        public override void Initialize(HistoryProviderInitializeParameters parameters)
        {
            _dataPath = Globals.DataFolder;
            _converter = new TushareDataConverter(_dataPath);
            Log.Trace($"TushareHistoryProvider.Initialize(): Initialized with data path: {_dataPath}");
        }

        /// <summary>
        /// Gets the history for the requested securities
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

        /// <summary>
        /// Gets history for a single request
        /// </summary>
        private IEnumerable<BaseData> GetHistoryForRequest(Data.HistoryRequest request)
        {
            // Convert LEAN Symbol to Tushare ts_code
            var tsCode = ConvertSymbolToTsCode(request.Symbol);

            if (string.IsNullOrEmpty(tsCode))
            {
                Log.Error($"TushareHistoryProvider.GetHistoryForRequest(): Could not convert symbol {request.Symbol} to ts_code");
                yield break;
            }

            // Only support daily resolution for now
            if (request.Resolution != Resolution.Daily)
            {
                Log.Error($"TushareHistoryProvider.GetHistoryForRequest(): Only daily resolution is supported, requested: {request.Resolution}");
                yield break;
            }

            // Get data from converter
            var bars = _converter.GetDailyData(tsCode, request.StartTimeUtc, request.EndTimeUtc);

            foreach (var bar in bars)
            {
                yield return bar;
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

            // Determine exchange suffix
            var suffix = market switch
            {
                Market.SSE => "SH",
                Market.SZSE => "SZ",
                Market.China => "SH", // Default to Shanghai
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
