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
using NUnit.Framework;
using QuantConnect.Statistics;

namespace QuantConnect.Tests.Common.Statistics
{
    [TestFixture]
    public class StatisticsResultsTests
    {
        [Test]
        public void CustomSummaryStatisticsOverrideGeneratedSummaryValues()
        {
            var results = new StatisticsResults(
                new AlgorithmPerformance(),
                new Dictionary<string, AlgorithmPerformance>(),
                new Dictionary<string, string>
                {
                    [PerformanceMetrics.TotalFees] = "¥0.00",
                    ["Synthetic Trades"] = "0"
                });

            results.AddCustomSummaryStatistics(new Dictionary<string, string>
            {
                [PerformanceMetrics.TotalFees] = "¥1,234.56",
                ["Synthetic Trades"] = "545"
            });

            Assert.AreEqual("¥1,234.56", results.Summary[PerformanceMetrics.TotalFees]);
            Assert.AreEqual("545", results.Summary["Synthetic Trades"]);
        }
    }
}
