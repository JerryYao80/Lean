using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareT1MomentumAlgorithmTests
    {
        [Test]
        public void ComputeMomentumReturnsPositiveValueForUptrend()
        {
            var momentum = AShareT1MomentumAlgorithm.ComputeMomentum(new List<decimal> { 10m, 10.4m, 10.8m, 11.2m });
            Assert.Greater(momentum, 0m);
        }

        [Test]
        public void RankCandidatesOrdersHighestMomentumFirst()
        {
            var ranked = AShareT1MomentumAlgorithm.RankEntryCandidates(
                new Dictionary<string, decimal>
                {
                    ["600000.SH"] = 0.12m,
                    ["000001.SZ"] = 0.08m,
                    ["300750.SZ"] = 0.03m,
                },
                0.05m,
                2)
                .ToList();

            CollectionAssert.AreEqual(new[] { "600000.SH", "000001.SZ" }, ranked);
        }

        [Test]
        public void ExitRequiresHoldingPeriodAndWeakMomentum()
        {
            Assert.IsFalse(AShareT1MomentumAlgorithm.ShouldExit(-0.02m, 0.0m, 0));
            Assert.IsTrue(AShareT1MomentumAlgorithm.ShouldExit(-0.02m, 0.0m, 1));
            Assert.IsFalse(AShareT1MomentumAlgorithm.ShouldExit(0.01m, 0.0m, 2));
        }
    }
}
