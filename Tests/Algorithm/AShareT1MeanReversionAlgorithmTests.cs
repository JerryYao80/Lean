using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareT1MeanReversionAlgorithmTests
    {
        [Test]
        public void LiveCatchUpEvaluationTriggersAfterCutoffWhenNotEvaluatedToday()
        {
            var shouldEvaluate = AShareT1MeanReversionAlgorithm.ShouldEvaluateLiveCatchUp(
                true,
                true,
                true,
                new DateTime(2026, 3, 9, 9, 40, 0),
                new DateTime(2026, 3, 8));

            Assert.IsTrue(shouldEvaluate);
        }

        [Test]
        public void LiveCatchUpEvaluationSkipsWhenAlreadyEvaluatedToday()
        {
            var shouldEvaluate = AShareT1MeanReversionAlgorithm.ShouldEvaluateLiveCatchUp(
                true,
                true,
                true,
                new DateTime(2026, 3, 9, 10, 0, 0),
                new DateTime(2026, 3, 9));

            Assert.IsFalse(shouldEvaluate);
        }

        [Test]
        public void AvailableQuantityRemainsLockedUntilNextDay()
        {
            Assert.AreEqual(0, AShareT1MeanReversionAlgorithm.GetAvailableQuantity(1000, 0));
            Assert.AreEqual(1000, AShareT1MeanReversionAlgorithm.GetAvailableQuantity(1000, 1));
        }

        [Test]
        public void SignalHistoryPathUsesDailyJsonlConvention()
        {
            var path = AShareT1MeanReversionAlgorithm.GetSignalHistoryFilePath(
                "/home/project/hope/Lean/Results/ashare-t1-signals.json",
                new DateTime(2026, 3, 9, 14, 30, 25));

            Assert.AreEqual("/home/project/hope/Lean/Results/signals/signals_20260309.jsonl", path);
        }

        [Test]
        public void SignalIdMatchesDesignConvention()
        {
            var signalId = AShareT1MeanReversionAlgorithm.BuildSignalId(
                new DateTime(2026, 3, 9, 14, 30, 25),
                "buy",
                "000001.SZ");

            Assert.AreEqual("20260309_143025_BUY_000001SZ", signalId);
        }
    }
}
