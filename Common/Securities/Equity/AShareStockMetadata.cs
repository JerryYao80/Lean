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
        /// Stock display name.
        /// </summary>
        public string Name { get; set; }

        /// <summary>
        /// Whether this stock is marked as special treatment.
        /// </summary>
        public bool IsSt { get; set; }

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
        private static readonly string[] SpecialTreatmentPrefixes = { "*ST", "ST", "S*ST", "SST" };
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
            var normalizedTicker = NormalizeTicker(ticker);
            return normalizedTicker != null && Overrides.TryGetValue(normalizedTicker, out var metadata) ? metadata : null;
        }

        /// <summary>
        /// Gets the daily price limit percentage for the specified ticker.
        /// </summary>
        public static decimal GetPriceLimitPercentage(string ticker, string description = null)
        {
            var normalizedTicker = NormalizeTicker(ticker);
            if (string.IsNullOrWhiteSpace(normalizedTicker))
            {
                return AShareStock.DefaultPriceLimitPercentage;
            }

            var metadata = GetMetadata(normalizedTicker);
            if (metadata?.IsSt == true || LooksLikeSpecialTreatment(metadata?.Name) || LooksLikeSpecialTreatment(description))
            {
                return AShareStock.SpecialTreatmentPriceLimitPercentage;
            }

            if (metadata != null)
            {
                return metadata.PriceLimitPercentage;
            }

            if (normalizedTicker.StartsWith("300", StringComparison.Ordinal)
                || normalizedTicker.StartsWith("301", StringComparison.Ordinal)
                || normalizedTicker.StartsWith("688", StringComparison.Ordinal))
            {
                return AShareStock.GrowthBoardPriceLimitPercentage;
            }

            if (normalizedTicker.StartsWith("43", StringComparison.Ordinal)
                || normalizedTicker.StartsWith("83", StringComparison.Ordinal)
                || normalizedTicker.StartsWith("87", StringComparison.Ordinal))
            {
                return AShareStock.BeijingExchangePriceLimitPercentage;
            }

            return AShareStock.DefaultPriceLimitPercentage;
        }

        /// <summary>
        /// Determines whether the specified ticker/name pair should be treated as ST.
        /// </summary>
        public static bool IsSpecialTreatment(string ticker, string description = null)
        {
            var metadata = GetMetadata(ticker);
            return metadata?.IsSt == true || LooksLikeSpecialTreatment(metadata?.Name) || LooksLikeSpecialTreatment(description);
        }

        private static string NormalizeTicker(string ticker)
        {
            if (string.IsNullOrWhiteSpace(ticker))
            {
                return null;
            }

            var normalizedTicker = ticker.Trim();
            var separatorIndex = normalizedTicker.IndexOf('.');
            if (separatorIndex >= 0)
            {
                normalizedTicker = normalizedTicker.Substring(0, separatorIndex);
            }

            return normalizedTicker;
        }

        private static bool LooksLikeSpecialTreatment(string nameOrDescription)
        {
            if (string.IsNullOrWhiteSpace(nameOrDescription))
            {
                return false;
            }

            var normalized = nameOrDescription
                .Trim()
                .Replace(" ", string.Empty)
                .Replace("　", string.Empty)
                .Replace("＊", "*", StringComparison.Ordinal)
                .ToUpperInvariant();

            foreach (var prefix in SpecialTreatmentPrefixes)
            {
                if (normalized.StartsWith(prefix, StringComparison.Ordinal))
                {
                    return true;
                }
            }

            return false;
        }
    }
}
