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

using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// Trading mode for A-Share ETFs
    /// </summary>
    public enum ETFTradingMode
    {
        /// <summary>
        /// T+0: Can buy and sell on the same day
        /// </summary>
        T0,

        /// <summary>
        /// T+1: Must wait until next day to sell after buying
        /// </summary>
        T1
    }

    /// <summary>
    /// Metadata for A-Share ETF securities
    /// </summary>
    public class AShareETFMetadata
    {
        /// <summary>
        /// ETF ticker symbol
        /// </summary>
        public string Ticker { get; set; }

        /// <summary>
        /// ETF name
        /// </summary>
        public string Name { get; set; }

        /// <summary>
        /// Trading mode (T+0 or T+1)
        /// </summary>
        public ETFTradingMode TradingMode { get; set; }

        /// <summary>
        /// Market (SSE or SZSE)
        /// </summary>
        public string Market { get; set; }

        /// <summary>
        /// Whether this ETF supports T+0 trading
        /// </summary>
        public bool IsT0 => TradingMode == ETFTradingMode.T0;
    }

    /// <summary>
    /// Registry of A-Share ETF metadata
    /// </summary>
    public static class AShareETFRegistry
    {
        private static readonly Dictionary<string, AShareETFMetadata> _etfMetadata = new Dictionary<string, AShareETFMetadata>
        {
            // T+0 ETFs - Shanghai Stock Exchange
            { "510050", new AShareETFMetadata { Ticker = "510050", Name = "50ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "510300", new AShareETFMetadata { Ticker = "510300", Name = "300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "510500", new AShareETFMetadata { Ticker = "510500", Name = "500ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518880", new AShareETFMetadata { Ticker = "518880", Name = "黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511880", new AShareETFMetadata { Ticker = "511880", Name = "银华日利", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511990", new AShareETFMetadata { Ticker = "511990", Name = "华宝添益", TradingMode = ETFTradingMode.T0, Market = "SSE" } },

            // T+0 ETFs - Shenzhen Stock Exchange
            { "159915", new AShareETFMetadata { Ticker = "159915", Name = "创业板ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159919", new AShareETFMetadata { Ticker = "159919", Name = "300ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159949", new AShareETFMetadata { Ticker = "159949", Name = "创业板50", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },

            // T+1 ETFs - Examples (for filtering out)
            { "510180", new AShareETFMetadata { Ticker = "510180", Name = "180ETF", TradingMode = ETFTradingMode.T1, Market = "SSE" } },
            { "159901", new AShareETFMetadata { Ticker = "159901", Name = "深100ETF", TradingMode = ETFTradingMode.T1, Market = "SZSE" } },
        };

        /// <summary>
        /// Gets metadata for an ETF by ticker
        /// </summary>
        public static AShareETFMetadata GetMetadata(string ticker)
        {
            return _etfMetadata.TryGetValue(ticker, out var metadata) ? metadata : null;
        }

        /// <summary>
        /// Checks if an ETF supports T+0 trading
        /// </summary>
        public static bool IsT0ETF(string ticker)
        {
            var metadata = GetMetadata(ticker);
            return metadata?.IsT0 ?? false;
        }

        /// <summary>
        /// Gets all T+0 ETF tickers
        /// </summary>
        public static List<string> GetT0ETFs()
        {
            return _etfMetadata.Values
                .Where(m => m.IsT0)
                .Select(m => m.Ticker)
                .ToList();
        }

        /// <summary>
        /// Gets all T+0 ETF tickers for a specific market
        /// </summary>
        public static List<string> GetT0ETFs(string market)
        {
            return _etfMetadata.Values
                .Where(m => m.IsT0 && m.Market == market)
                .Select(m => m.Ticker)
                .ToList();
        }
    }
}

