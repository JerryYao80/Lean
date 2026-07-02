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
using QuantConnect.Data.AShare;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareShiborRateModelTests
    {
        private const string TusharePath = "/home/project/tushare-downloader/tushare_data_v2";

        [Test]
        public void GetInterestRate_ReturnsRealShibor_WithinRange()
        {
            var model = new AShareShiborRateModel(TusharePath);
            var rate = model.GetInterestRate(new DateTime(2024, 6, 28));

            // 1Y SHIBOR real value ~2.0% in 2024-06
            Assert.Greater(rate, 0.015m);
            Assert.Less(rate, 0.025m);
        }
    }
}
