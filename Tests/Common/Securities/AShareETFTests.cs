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

using NUnit.Framework;
using QuantConnect;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Tests.Common.Securities
{
    [TestFixture]
    public class AShareETFTests
    {
        [TestCase("510050", 0.10)]
        [TestCase("159915", 0.20)]
        [TestCase("159949", 0.20)]
        [TestCase("UNKNOWN", 0.10)]
        public void ReturnsExpectedPriceLimitPercentage(string ticker, decimal expectedPriceLimit)
        {
            var actual = AShareETFRegistry.GetPriceLimitPercentage(ticker);
            Assert.AreEqual(expectedPriceLimit, actual);
        }

        [Test]
        public void GrowthBoardEtfUpperPriceLimitUsesTwentyPercentBand()
        {
            var symbol = Symbol.Create("159915", SecurityType.Equity, Market.SZSE);
            var upperLimit = AShareETF.GetUpperPriceLimit(symbol, 2.232m, 0.001m);
            Assert.AreEqual(2.678m, upperLimit);
        }

        [Test]
        public void PriceLimitBoundsRespectMinimumPriceVariation()
        {
            var symbol = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var upperLimit = AShareETF.GetUpperPriceLimit(symbol, 2.232m, 0.001m);
            var lowerLimit = AShareETF.GetLowerPriceLimit(symbol, 2.678m, 0.001m);
            Assert.AreEqual(2.455m, upperLimit);
            Assert.AreEqual(2.410m, lowerLimit);
        }

        [Test]
        public void PriceLimitBoundsUseEtfTickSizeWhenSymbolPropertiesAreTooCoarse()
        {
            var symbol = Symbol.Create("159949", SecurityType.Equity, Market.SZSE);
            var upperLimit = AShareETF.GetUpperPriceLimit(symbol, 1.009m, 0.01m);
            Assert.AreEqual(1.211m, upperLimit);
        }
    }
}
