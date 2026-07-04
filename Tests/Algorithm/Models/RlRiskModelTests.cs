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
            var model = new RlRiskModel(new RlRiskConfig { FallbackAlpha = 0.5m });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var targets = new[] { new PortfolioTarget(sym, 0m) };
            var algo = new TestAlgo();
            var result = model.ManageRisk(algo, targets).ToList();
            Assert.AreEqual(0m, result[0].Quantity);
        }

        [Test]
        public void Request_ReturnsFallback_WhenServerDown()
        {
            var model = new RlRiskModel(new RlRiskConfig {
                Endpoint = "tcp://127.0.0.1:59999", FallbackAlpha = 0.3m, TimeoutMs = 50 });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var targets = new[] { new PortfolioTarget(sym, 100m) };
            var result = model.ManageRisk(new TestAlgo(), targets).ToList();
            Assert.AreEqual(30m, result[0].Quantity); // 100 * 0.3 fallback
        }

        [Test]
        public void FallbackAlpha_NeverThrows()
        {
            var model = new RlRiskModel(new RlRiskConfig {
                Endpoint = "tcp://127.0.0.1:59999", FallbackAlpha = 0.5m, TimeoutMs = 10 });
            var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            for (int i = 0; i < 11; i++)
            {
                var result = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                Assert.AreEqual(50m, result[0].Quantity);
            }
        }

        private class TestAlgo : QCAlgorithm { }
    }
}
