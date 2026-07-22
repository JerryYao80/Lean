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
using QuantConnect;

namespace QuantConnect.Data.AShare
{
    /// <summary>
    /// Lightweight option data carrier for CBOE VIX computation.
    /// </summary>
    public class OptionContractData
    {
        public decimal Strike { get; set; }
        public OptionRight Right { get; set; }
        public decimal Price { get; set; }  // real settlement price
    }

    /// <summary>
    /// CBOE VIX whitepaper helpers operating on LEAN-native option data.
    /// </summary>
    public static class AShareVixHelper
    {
        /// <summary>
        /// Forward price via put-call parity at the strike where |C-P| is smallest.
        /// F = K + e^(rT)(C - P)
        /// </summary>
        public static decimal ForwardPriceFromParity(IEnumerable<OptionContractData> options,
                                                      double r, double T)
        {
            var byStrike = options
                .GroupBy(o => o.Strike)
                .Select(g => new
                {
                    Strike = g.Key,
                    Call = g.FirstOrDefault(o => o.Right == OptionRight.Call)?.Price ?? 0m,
                    Put = g.FirstOrDefault(o => o.Right == OptionRight.Put)?.Price ?? 0m
                })
                .Where(x => x.Call > 0m && x.Put > 0m)
                .OrderBy(x => Math.Abs(x.Call - x.Put))
                .FirstOrDefault();

            if (byStrike == null) return 0m;
            var factor = (decimal)Math.Exp(r * T);
            return byStrike.Strike + factor * (byStrike.Call - byStrike.Put);
        }

        /// <summary>
        /// CBOE model-free variance:
        /// sigma^2 = (2/T) * Sum[ dK/K^2 * e^(rT) * Q(K) ] - (1/T) * [F/K0 - 1]^2
        /// Returns -1 on insufficient/invalid data (caller should skip).
        /// </summary>
        public static double ModelFreeVariance(IEnumerable<OptionContractData> options,
                                                decimal F, double T, double r)
        {
            if (T <= 0 || F <= 0) return -1;

            var otm = SelectOtmOptions(options, F).OrderBy(o => o.Strike).ToList();
            if (otm.Count < 2) return -1;

            var strikes = otm.Select(o => o.Strike).ToList();
            var k0 = strikes.Last(k => k <= F);

            double contribution = 0;
            for (int i = 0; i < otm.Count; i++)
            {
                var k = otm[i].Strike;
                double deltaK;
                if (i == 0) deltaK = (double)(strikes[1] - strikes[0]);
                else if (i == otm.Count - 1) deltaK = (double)(strikes[^1] - strikes[^2]);
                else deltaK = (double)(strikes[i + 1] - strikes[i - 1]) / 2.0;

                if (otm[i].Price <= 0m) continue;
                contribution += deltaK / ((double)k * (double)k) *
                                Math.Exp(r * T) * (double)otm[i].Price;
            }

            double variance = (2.0 / T) * contribution - (1.0 / T) *
                              Math.Pow((double)F / (double)k0 - 1.0, 2);
            return variance > 0 ? variance : -1;
        }

        /// <summary>
        /// Select OTM options (puts below F, calls above F, plus K0).
        /// </summary>
        private static IEnumerable<OptionContractData> SelectOtmOptions(
            IEnumerable<OptionContractData> options, decimal F)
        {
            var result = new List<OptionContractData>();
            foreach (var o in options)
            {
                if (o.Strike < F && o.Right == OptionRight.Put) result.Add(o);
                else if (o.Strike > F && o.Right == OptionRight.Call) result.Add(o);
                else if (o.Strike == F) result.Add(o);  // K0: use either (avg handled upstream)
            }
            return result;
        }
    }
}
