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
using QuantConnect.ToolBox;

namespace QuantConnect.Tests.AShare
{
    /// <summary>
    /// Builds synthetic option chains for VIX helper unit tests.
    /// </summary>
    internal static class TestChainBuilder
    {
        public static List<OptionContractData> BuildAtmChain(decimal spot, decimal callPrice, decimal putPrice)
        {
            return new List<OptionContractData>
            {
                new() { Strike = spot, Right = OptionRight.Call, Price = callPrice },
                new() { Strike = spot, Right = OptionRight.Put, Price = putPrice }
            };
        }

        public static List<OptionContractData> BuildFullChain(decimal spot)
        {
            var data = new List<OptionContractData>();
            for (var k = spot - 0.3m; k <= spot + 0.3m; k += 0.05m)
            {
                var dist = System.Math.Abs(k - spot);
                var price = System.Math.Max(0.01m, 0.15m - dist);
                data.Add(new OptionContractData { Strike = k, Right = OptionRight.Call, Price = price });
                data.Add(new OptionContractData { Strike = k, Right = OptionRight.Put, Price = price });
            }
            return data;
        }
    }
}
