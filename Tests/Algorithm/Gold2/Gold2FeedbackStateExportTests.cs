using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.CSharp.Models.Gold2;

namespace QuantConnect.Tests.Algorithm.Gold2
{
    [TestFixture]
    public class Gold2FeedbackStateExportTests
    {
        [Test]
        public void SerializeRlState_Exists_And_Has_PeakPrevClose_Fields()
        {
            var t = typeof(Gold2BetaVolTargetStrategy);
            Assert.IsNotNull(t.GetMethod("SerializeRlState"), "SerializeRlState missing");
            Assert.IsNotNull(t.GetField("_peakTpv",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_peakTpv field missing");
            Assert.IsNotNull(t.GetField("_prevTpv",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_prevTpv field missing");
            Assert.IsNotNull(t.GetField("_lastClose",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_lastClose field missing");
        }

        [Test]
        public void TrendAlphaModel_Still_Exposes_LastDirCoef()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, Symbol.Empty, 0.2m, false);
            Assert.AreEqual(0m, model.LastDirCoef);
        }
    }
}
