using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Factors.Forward;
using QuantConnect.Tests.Engine.DataFeeds;

namespace QuantConnect.Tests.Algorithm.Gold2
{
    [TestFixture]
    public class Gold2ReviewStateExportTests
    {
        [Test]
        public void TrendAlphaModel_Exposes_LastDirCoef()
        {
            // LastDirCoef is the insight.Weight the model last emitted; review adapter reads it
            // to attribute the trend layer. Must be public + default 0 before first Update.
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, Symbol.Empty, 0.2m, false);
            Assert.AreEqual(0m, model.LastDirCoef);
        }

        [Test]
        public void VolTargetPortfolioModel_Exposes_LastActualWeight()
        {
            // LastActualWeight is the post-deadzone target; review adapter reads it for the
            // vol_target layer. Must be public + default 0 before first CreateTargets.
            var vol = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            var model = new Gold2VolTargetPortfolioModel(vol, Symbol.Empty, 0.05m);
            Assert.AreEqual(0m, model.LastActualWeight);
        }

        [Test]
        public void SerializeRlState_Method_Exists_And_Strategy_Holds_Review_Fields()
        {
            // Spec §3.3: dir_coef, w_after_vol, extreme_cap, trend_disabled must be emitted.
            // A live SerializeRlState call needs a fully-Initialized algorithm (Portfolio,
            // Securities, factor warmup) which is exercised end-to-end in Task 14 via a real
            // backtest with RL_TRACE_PATH set. Here we assert the method + fields are wired.
            var t = typeof(Gold2BetaVolTargetStrategy);
            Assert.IsNotNull(t.GetMethod("SerializeRlState"),
                "SerializeRlState method missing");
            // The 4 review fields are read off the held alpha/portfolio models + strategy fields;
            // confirm the strategy declares the private fields that feed them.
            Assert.IsNotNull(t.GetField("_trendAlpha",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_trendAlpha field missing");
            Assert.IsNotNull(t.GetField("_portfolio",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_portfolio field missing");
            Assert.IsNotNull(t.GetField("_extremeCap",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_extremeCap field missing");
            Assert.IsNotNull(t.GetField("_trendDisabled",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_trendDisabled field missing");
        }
    }
}
