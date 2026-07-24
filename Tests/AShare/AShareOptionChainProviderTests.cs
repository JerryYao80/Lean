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
using System.Linq;
using NUnit.Framework;
using QuantConnect.Data.AShare;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareOptionChainProviderTests
    {
        [Test]
        public void GetOptionContractList_ReturnsEuropeanOptions()
        {
            var provider = new AShareOptionChainProvider("Data");
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.China);

            var contracts = provider.GetOptionContractList(underlying, new DateTime(2024, 6, 28)).ToList();

            Assert.Greater(contracts.Count, 20);
            Assert.IsTrue(contracts.All(c => c.ID.OptionStyle == OptionStyle.European));
            Assert.IsTrue(contracts.All(c => c.ID.Market == Market.China));
        }
    }
}
