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

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Helper methods for the A-share T+1 mean reversion strategy.
    /// </summary>
    public static class AShareT1MeanReversionSignalModel
    {
        /// <summary>
        /// Computes a simple rolling z-score for the last close in the supplied window.
        /// </summary>
        public static decimal ComputeZScore(IReadOnlyList<decimal> closes)
        {
            if (closes == null || closes.Count <= 1)
            {
                return 0m;
            }

            var mean = closes.Average();
            var variance = closes
                .Select(value => (value - mean) * (value - mean))
                .Average();
            var std = (decimal)Math.Sqrt((double)variance);
            if (std == 0m)
            {
                return 0m;
            }

            return (closes[closes.Count - 1] - mean) / std;
        }

        /// <summary>
        /// Ranks entry candidates from most oversold upwards.
        /// </summary>
        public static IEnumerable<string> RankEntryCandidates(IReadOnlyDictionary<string, decimal> zScores, decimal entryThreshold, int maxPositions)
        {
            if (zScores == null || maxPositions <= 0)
            {
                return Enumerable.Empty<string>();
            }

            return zScores
                .Where(pair => pair.Value <= entryThreshold)
                .OrderBy(pair => pair.Value)
                .ThenBy(pair => pair.Key, StringComparer.Ordinal)
                .Take(maxPositions)
                .Select(pair => pair.Key)
                .ToList();
        }

        /// <summary>
        /// Determines whether an existing position can be exited under T+1 rules.
        /// </summary>
        public static bool ShouldExit(decimal zScore, decimal exitThreshold, int holdingDays)
        {
            return holdingDays >= 1 && zScore >= exitThreshold;
        }
    }
}
