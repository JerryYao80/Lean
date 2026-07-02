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
using QuantConnect.Data.AShare;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareVixHelperTests
    {
        [Test]
        public void ForwardPriceFromParity_ATM_ReturnsNearSpot()
        {
            var chain = TestChainBuilder.BuildAtmChain(spot: 2.5m, callPrice: 0.12m, putPrice: 0.12m);
            var F = AShareVixHelper.ForwardPriceFromParity(chain, r: 0.02, T: 0.1);
            // F = K + e^(rT)(C-P); ATM with C=P => F ~ K = 2.5
            Assert.That((double)F, Is.EqualTo(2.5).Within(0.05));  // F is decimal from put-call parity
        }

        [Test]
        public void ModelFreeVariance_ReturnsPositiveValue()
        {
            var chain = TestChainBuilder.BuildFullChain(spot: 2.5m);
            var F = AShareVixHelper.ForwardPriceFromParity(chain, 0.02, 0.1);
            var variance = AShareVixHelper.ModelFreeVariance(chain, F, T: 0.1, r: 0.02);

            Assert.Greater(variance, 0, "Variance must be positive");
            Assert.Less(variance, 0.2, "Variance out of expected range");
        }
    }
}
