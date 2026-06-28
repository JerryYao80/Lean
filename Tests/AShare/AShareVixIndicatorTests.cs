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
using NUnit.Framework;
using QuantConnect.ToolBox;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareVixIndicatorTests
    {
        [Test]
        public void Calculate_Returns30DayInterpolatedVix()
        {
            var indicator = new AShareVixIndicator(r: 0.02);
            var near = TestChainBuilder.BuildFullChain(2.5m);
            var next = TestChainBuilder.BuildFullChain(2.5m);

            var result = indicator.Calculate(
                date: new DateTime(2024, 6, 28),
                nearExpiry: new DateTime(2024, 7, 24),   // ~26 days
                nextExpiry: new DateTime(2024, 8, 28),   // ~61 days
                near, next);

            Assert.Greater(result.Vix, 5);
            Assert.Less(result.Vix, 60);
            Assert.Greater(result.SigmaNear, 0);
            Assert.Greater(result.SigmaNext, 0);
        }
    }
}
