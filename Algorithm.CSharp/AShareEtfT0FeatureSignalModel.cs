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
    /// Cross-sectional scoring model aligned with the Python T+0 ETF feature strategy.
    /// </summary>
    public static class AShareEtfT0FeatureSignalModel
    {
        public static readonly IReadOnlyDictionary<string, decimal> FeatureWeights = new Dictionary<string, decimal>
        {
            [nameof(AShareEtfT0FeatureData.SignalMomentum20)] = -0.15m,
            [nameof(AShareEtfT0FeatureData.SignalMomentum5)] = -0.35m,
            [nameof(AShareEtfT0FeatureData.SignalLiquidity5)] = 0.20m,
            [nameof(AShareEtfT0FeatureData.SignalCloseLocation)] = -0.10m,
            [nameof(AShareEtfT0FeatureData.SignalVolatility10)] = 0.15m,
            [nameof(AShareEtfT0FeatureData.SignalGapAbs)] = -0.05m,
        };

        public static Dictionary<Symbol, decimal> ComputeScores(IReadOnlyDictionary<Symbol, AShareEtfT0FeatureData> features)
        {
            var eligible = features
                .Where(pair => pair.Value != null && pair.Value.HasSignals)
                .ToList();

            var scores = eligible.ToDictionary(pair => pair.Key, _ => 0m);
            if (eligible.Count == 0)
            {
                return scores;
            }

            foreach (var featureWeight in FeatureWeights)
            {
                var indicesWithValues = new List<int>();
                var values = new List<decimal>();
                for (var index = 0; index < eligible.Count; index++)
                {
                    var value = GetFeatureValue(eligible[index].Value, featureWeight.Key);
                    if (!value.HasValue)
                    {
                        continue;
                    }

                    indicesWithValues.Add(index);
                    values.Add(value.Value);
                }

                if (values.Count <= 1)
                {
                    continue;
                }

                var zScores = SafeZScores(values);
                for (var index = 0; index < indicesWithValues.Count; index++)
                {
                    scores[eligible[indicesWithValues[index]].Key] += zScores[index] * featureWeight.Value;
                }
            }

            return scores;
        }

        private static decimal? GetFeatureValue(AShareEtfT0FeatureData data, string propertyName)
        {
            return propertyName switch
            {
                nameof(AShareEtfT0FeatureData.SignalMomentum20) => data.SignalMomentum20,
                nameof(AShareEtfT0FeatureData.SignalMomentum5) => data.SignalMomentum5,
                nameof(AShareEtfT0FeatureData.SignalLiquidity5) => data.SignalLiquidity5,
                nameof(AShareEtfT0FeatureData.SignalCloseLocation) => data.SignalCloseLocation,
                nameof(AShareEtfT0FeatureData.SignalVolatility10) => data.SignalVolatility10,
                nameof(AShareEtfT0FeatureData.SignalGapAbs) => data.SignalGapAbs,
                nameof(AShareEtfT0FeatureData.SignalNavPremium1) => data.SignalNavPremium1,
                nameof(AShareEtfT0FeatureData.SignalNavPremiumZ20) => data.SignalNavPremiumZ20,
                nameof(AShareEtfT0FeatureData.SignalShareChange5) => data.SignalShareChange5,
                nameof(AShareEtfT0FeatureData.SignalSizeChange5) => data.SignalSizeChange5,
                nameof(AShareEtfT0FeatureData.SignalExcessGap) => data.SignalExcessGap,
                nameof(AShareEtfT0FeatureData.SignalExcessIntraday) => data.SignalExcessIntraday,
                nameof(AShareEtfT0FeatureData.SignalTrackingError10) => data.SignalTrackingError10,
                nameof(AShareEtfT0FeatureData.SignalIndexMomentum5) => data.SignalIndexMomentum5,
                _ => null,
            };
        }

        private static List<decimal> SafeZScores(IReadOnlyList<decimal> values)
        {
            if (values.Count <= 1)
            {
                return values.Select(_ => 0m).ToList();
            }

            var mean = values.Average();
            var variance = values.Select(value => Math.Pow((double)(value - mean), 2)).Average();
            var standardDeviation = Math.Sqrt(variance);
            if (standardDeviation <= double.Epsilon)
            {
                return values.Select(_ => 0m).ToList();
            }

            return values
                .Select(value => (decimal)(((double)value - (double)mean) / standardDeviation))
                .ToList();
        }
    }
}
