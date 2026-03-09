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

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// Metadata for A-share stocks.
    /// </summary>
    public class AShareStockMetadata
    {
        /// <summary>
        /// Stock ticker.
        /// </summary>
        public string Ticker { get; set; }

        /// <summary>
        /// Daily price limit percentage.
        /// </summary>
        public decimal PriceLimitPercentage { get; set; } = AShareStock.DefaultPriceLimitPercentage;
    }

    /// <summary>
    /// Metadata registry for A-share stocks.
    /// Supports explicit overrides and rule-based fallbacks.
    /// </summary>
    public static class AShareStockMetadataRegistry
    {
        private static readonly Dictionary<string, AShareStockMetadata> Overrides = new(StringComparer.OrdinalIgnoreCase)
        {
            { "300750", new AShareStockMetadata { Ticker = "300750", PriceLimitPercentage = AShareStock.GrowthBoardPriceLimitPercentage } },
            { "688981", new AShareStockMetadata { Ticker = "688981", PriceLimitPercentage = AShareStock.GrowthBoardPriceLimitPercentage } }
        };

        /// <summary>
        /// Gets metadata for the specified ticker, if any explicit override exists.
        /// </summary>
        public static AShareStockMetadata GetMetadata(string ticker)
        {
            return ticker != null && Overrides.TryGetValue(ticker, out var metadata) ? metadata : null;
        }

        /// <summary>
        /// Gets the daily price limit percentage for the specified ticker.
        /// </summary>
        public static decimal GetPriceLimitPercentage(string ticker)
        {
            if (string.IsNullOrWhiteSpace(ticker))
            {
                return AShareStock.DefaultPriceLimitPercentage;
            }

            var metadata = GetMetadata(ticker);
            if (metadata != null)
            {
                return metadata.PriceLimitPercentage;
            }

            if (ticker.StartsWith("300", StringComparison.Ordinal) || ticker.StartsWith("688", StringComparison.Ordinal))
            {
                return AShareStock.GrowthBoardPriceLimitPercentage;
            }

            return AShareStock.DefaultPriceLimitPercentage;
        }
    }
}
