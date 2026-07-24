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
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareT1MeanReversionSignalModelTests
    {
        [Test]
        public void ComputeZScoreReturnsNegativeValueForOversoldClose()
        {
            var zScore = AShareT1MeanReversionSignalModel.ComputeZScore(new List<decimal> { 10m, 10.2m, 10.1m, 10.3m, 9.4m });
            Assert.Less(zScore, 0m);
        }

        [Test]
        public void RankCandidatesOrdersMostOversoldSymbolsFirst()
        {
            var ranked = AShareT1MeanReversionSignalModel.RankEntryCandidates(
                new Dictionary<string, decimal>
                {
                    ["600000.SH"] = -2.4m,
                    ["000001.SZ"] = -1.6m,
                    ["300750.SZ"] = -0.4m,
                },
                -1.0m,
                2)
                .ToList();

            CollectionAssert.AreEqual(new[] { "600000.SH", "000001.SZ" }, ranked);
        }
    }
}
