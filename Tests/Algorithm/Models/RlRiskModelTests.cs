using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Algorithm.Models
{
    [TestFixture]
    public class RlRiskModelTests
    {
        [Test]
        public void Alpha_ClampedToZeroOne()
        {
            Assert.AreEqual(0m, RlRiskModel.ClampAlpha(2.0m));
            Assert.AreEqual(0m, RlRiskModel.ClampAlpha(-0.5m));
            Assert.AreEqual(0.5m, RlRiskModel.ClampAlpha(0.5m));
            Assert.AreEqual(1m, RlRiskModel.ClampAlpha(1m));
        }

        [Test]
        public void ManageRisk_PreservesZeroTargets()
        {
            var model = new RlRiskModel(config: new RlRiskConfig { FallbackAlpha = 0.5m });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var targets = new[] { new PortfolioTarget(sym, 0m) };
            var algo = new TestAlgo();
            var result = model.ManageRisk(algo, targets).ToList();
            Assert.AreEqual(0m, result[0].Quantity);
        }

        [Test]
        public void Request_ReturnsFallback_WhenServerDown()
        {
            var model = new RlRiskModel(config: new RlRiskConfig {
                Endpoint = "tcp://127.0.0.1:59999", FallbackAlpha = 0.3m, TimeoutMs = 50 });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var targets = new[] { new PortfolioTarget(sym, 100m) };
            var result = model.ManageRisk(new TestAlgo(), targets).ToList();
            Assert.AreEqual(30m, result[0].Quantity); // 100 * 0.3 fallback
        }

        [Test]
        public void FallbackAlpha_NeverThrows()
        {
            var model = new RlRiskModel(config: new RlRiskConfig {
                Endpoint = "tcp://127.0.0.1:59999", FallbackAlpha = 0.5m, TimeoutMs = 10 });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            for (int i = 0; i < 11; i++)
            {
                var result = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                Assert.AreEqual(50m, result[0].Quantity);
            }
        }

        [Test]
        public void AlphaTrace_ReplayMode_ReturnsSequenceAlpha()
        {
            var tracePath = System.IO.Path.GetTempFileName();
            System.IO.File.WriteAllLines(tracePath, new[] {
                "{\"bar\":0,\"alpha\":0.3}",
                "{\"bar\":1,\"alpha\":0.7}",
            });
            try
            {
                var model = new RlRiskModel(algorithm: null, new RlRiskConfig {
                    AlphaTracePath = tracePath, FallbackAlpha = 0.5m });
                var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
                var r1 = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                var r2 = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                Assert.AreEqual(30m, r1[0].Quantity);  // 100 * 0.3
                Assert.AreEqual(70m, r2[0].Quantity);  // 100 * 0.7
                // A1.5: LastAppliedAlpha 须反映当期 trace alpha (供 SerializeRlState 写 trace)
                Assert.AreEqual(0.7m, model.LastAppliedAlpha);
            }
            finally { System.IO.File.Delete(tracePath); }
        }

        [Test]
        public void LastAppliedAlpha_DefaultsToOne_InCompositeMode()
        {
            // 无 RlRiskModel 时, 策略 SerializeRlState 用 1.0m (满仓) — 不影响 composite mode
            var model = new RlRiskModel(config: new RlRiskConfig { FallbackAlpha = 0.5m });
            Assert.AreEqual(1.0m, model.LastAppliedAlpha);
        }

        private class TestAlgo : QCAlgorithm { }
    }
}
