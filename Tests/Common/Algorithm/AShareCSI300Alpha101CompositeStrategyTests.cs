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
 *
 */

/*
 * TDD test for AShareCSI300Alpha101CompositeStrategy — Task 3 (5-layer strategy
 *主体). Verifies the optimizer-injected alpha blend weights (w_alphaNNN) are
 * read from parameters and normalized to sum=1, with an all-zero fallback to
 * equal 1/8 weighting.
 *
 * The test calls ReadAndNormalizeWeights() directly (NOT Initialize()) to keep
 * the unit test deterministic and isolated from pythonnet/CSI300 universe
 * loading. Initialize() calls ReadAndNormalizeWeights() internally in
 * production — the weight-normalization contract is identical.
 */

using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture]
    public class AShareCSI300Alpha101CompositeStrategyTests
    {
        [Test]
        public void ReadAndNormalizeWeights_ReadsAlphaWeightsFromParameters()
        {
            var algo = new AShareCSI300Alpha101CompositeStrategy();
            algo.SetParameters(new Dictionary<string, string>
            {
                {"w_alpha001", "0.20"}, {"w_alpha006", "0.10"}, {"w_alpha030", "0.15"},
                {"w_alpha040", "0.05"}, {"w_alpha042", "0.10"}, {"w_alpha055", "0.10"},
                {"w_alpha058", "0.20"}, {"w_alpha101", "0.10"},
                {"zscore-threshold", "1.0"}, {"rebalance-days", "21"},
            });
            Assert.DoesNotThrow(() => algo.ReadAndNormalizeWeights());
            Assert.AreEqual(1.0, algo.NormalizedWeights.Sum(), 1e-6);
        }

        [Test]
        public void ReadAndNormalizeWeights_AllZeroWeights_FallsBackToEqualWeight()
        {
            var algo = new AShareCSI300Alpha101CompositeStrategy();
            algo.SetParameters(new Dictionary<string, string>
            {
                {"w_alpha001", "0"}, {"w_alpha006", "0"}, {"w_alpha030", "0"},
                {"w_alpha040", "0"}, {"w_alpha042", "0"}, {"w_alpha055", "0"},
                {"w_alpha058", "0"}, {"w_alpha101", "0"},
            });
            algo.ReadAndNormalizeWeights();
            Assert.AreEqual(0.125, algo.NormalizedWeights[0], 1e-6);
            Assert.AreEqual(8, algo.NormalizedWeights.Length);
        }
    }
}
