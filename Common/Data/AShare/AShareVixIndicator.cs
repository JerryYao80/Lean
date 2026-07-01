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

namespace QuantConnect.ToolBox
{
    /// <summary>
    /// VIX computation result: 30-day interpolated index and component variances.
    /// </summary>
    public class VixResult
    {
        public double Vix { get; set; }          // 30-day, annualized %
        public double SigmaNear { get; set; }    // near-term %
        public double SigmaNext { get; set; }    // next-term %
        public double TNear { get; set; }
        public double TNext { get; set; }
    }

    /// <summary>
    /// CBOE VIX-style 30-day interpolated implied volatility index.
    /// Operates on OptionContractData (real settlement prices from LEAN chain).
    /// </summary>
    public class AShareVixIndicator
    {
        private readonly double _r;

        public AShareVixIndicator(double r) { _r = r; }

        /// <summary>
        /// Compute 30-day VIX from near and next term option chains.
        /// </summary>
        public VixResult Calculate(DateTime date,
                                    DateTime nearExpiry, DateTime nextExpiry,
                                    IEnumerable<OptionContractData> near,
                                    IEnumerable<OptionContractData> next)
        {
            var nearList = new List<OptionContractData>(near);
            var nextList = new List<OptionContractData>(next);

            var tNear = (nearExpiry - date).TotalDays / 365.0;
            var tNext = (nextExpiry - date).TotalDays / 365.0;

            var fNear = (double)AShareVixHelper.ForwardPriceFromParity(nearList, _r, tNear);
            var fNext = (double)AShareVixHelper.ForwardPriceFromParity(nextList, _r, tNext);

            var varNear = AShareVixHelper.ModelFreeVariance(nearList, (decimal)fNear, tNear, _r);
            var varNext = AShareVixHelper.ModelFreeVariance(nextList, (decimal)fNext, tNext, _r);

            if (varNear <= 0 || varNext <= 0 || tNear <= 0 || tNext <= 0)
                return new VixResult();  // empty = invalid

            // CBOE 30-day linear interpolation
            var t30 = 30.0 / 365.0;
            var w1 = (tNext - t30) / (tNext - tNear);
            var w2 = 1.0 - w1;
            var var30 = w1 * varNear * (tNear / t30) + w2 * varNext * (tNext / t30);

            return new VixResult
            {
                Vix = Math.Sqrt(var30) * 100.0,
                SigmaNear = Math.Sqrt(varNear) * 100.0,
                SigmaNext = Math.Sqrt(varNext) * 100.0,
                TNear = tNear,
                TNext = tNext
            };
        }
    }
}
