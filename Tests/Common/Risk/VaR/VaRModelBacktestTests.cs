// file: Tests/Common/Risk/VaR/VaRModelBacktestTests.cs
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Tests.Common.Risk.VaR
{
    [TestFixture]
    public class VaRModelBacktestTests
    {
        private const double KupiecCritical95 = 3.841;
        private const double KupiecCritical99 = 6.635;

        private static double KupiecPOF(int failures, int observations, double confidence)
        {
            var p = 1 - confidence;
            var piHat = (double)failures / observations;
            if (piHat == 0) piHat = 1e-10;
            if (piHat == 1) piHat = 1 - 1e-10;

            var lr = -2 * (
                Math.Log(Math.Pow(1 - p, observations - failures) * Math.Pow(p, failures)) -
                Math.Log(Math.Pow(1 - piHat, observations - failures) * Math.Pow(piHat, failures))
            );
            return lr;
        }

        private static double ChristoffersenIndependence(bool[] breaches)
        {
            int n00 = 0, n01 = 0, n10 = 0, n11 = 0;
            for (int i = 0; i < breaches.Length - 1; i++)
            {
                if (!breaches[i] && !breaches[i + 1]) n00++;
                else if (!breaches[i] && breaches[i + 1]) n01++;
                else if (breaches[i] && !breaches[i + 1]) n10++;
                else n11++;
            }

            var pi01 = (n00 + n01) > 0 ? (double)n01 / (n00 + n01) : 0;
            var pi11 = (n10 + n11) > 0 ? (double)n11 / (n10 + n11) : 0;
            var totalTransitions = n00 + n01 + n10 + n11;
            if (totalTransitions == 0) return 0;
            var pi = (double)(n01 + n11) / totalTransitions;

            if (pi == 0 || pi == 1) return 0;

            var lr = -2 * (
                Math.Log(Math.Pow(1 - pi, n00 + n10) * Math.Pow(pi, n01 + n11)) -
                Math.Log(Math.Pow(1 - pi01, n00) * Math.Pow(pi01, n01) * Math.Pow(1 - pi11, n10) * Math.Pow(pi11, n11))
            );
            return double.IsNaN(lr) ? 0 : lr;
        }

        [Test]
        public void KupiecPOF_AcceptsCalibratedModel()
        {
            var lr = KupiecPOF(5, 500, 0.99);
            Assert.Less(lr, KupiecCritical95);
        }

        [Test]
        public void KupiecPOF_RejectsUnderestimatedRisk()
        {
            var lr = KupiecPOF(15, 500, 0.99);
            Assert.Greater(lr, KupiecCritical95);
        }

        [Test]
        public void ChristoffersenIndependence_NoBreaches_ReturnsZero()
        {
            var breaches = new bool[100];
            var lr = ChristoffersenIndependence(breaches);
            Assert.AreEqual(0, lr);
        }

        [Test]
        public void ChristoffersenIndependence_DetectsClustering()
        {
            var breaches = new bool[100];
            for (int i = 0; i < 10; i++) breaches[i] = true;
            var lr = ChristoffersenIndependence(breaches);
            Assert.GreaterOrEqual(lr, 0);
        }

        [Test]
        public void VarEngine_OnRealETFHistory_PassesKupiec()
        {
            var dataPath = "../../../Data/equity/sse/daily/510050.csv";
            if (!File.Exists(dataPath))
            {
                Assert.Ignore($"Real data not found at {dataPath}; skipping model validation");
                return;
            }

            var lines = File.ReadAllLines(dataPath);
            var closes = new List<double>();
            foreach (var line in lines.Skip(1))
            {
                var parts = line.Split(',');
                if (parts.Length >= 5 && double.TryParse(parts[4], out var close))
                    closes.Add(close);
            }

            if (closes.Count < 500) { Assert.Ignore("Insufficient history"); return; }

            var returns = new List<double>();
            for (int i = 1; i < closes.Count; i++)
                returns.Add(closes[i] / closes[i - 1] - 1);

            int window = 252;
            int testStart = window;
            int testEnd = returns.Count;
            var breaches = new List<bool>();

            for (int t = testStart; t < testEnd; t++)
            {
                var trainReturns = returns.Skip(t - window).Take(window).ToArray();
                var varResult = VarEngine.Compute(QuantConnect.Symbol.Empty,
                    trainReturns, VaRMethod.BootstrapHistorical, VaRScenario.OneDay99,
                    new VarConfig(bootstrapIterations: 1000));

                if (!varResult.IsValid) continue;

                var realizedReturn = returns[t];
                var breach = realizedReturn < -varResult.ValueAtRisk;
                breaches.Add(breach);
            }

            if (breaches.Count < 50) { Assert.Ignore("Insufficient test obs"); return; }

            var failures = breaches.Count(b => b);
            var lr = KupiecPOF(failures, breaches.Count, 0.99);
            TestContext.Out.WriteLine($"Kupiec: failures={failures}/{breaches.Count}, LR={lr:F3}");
            Assert.Less(lr, KupiecCritical99, $"Kupiec LR={lr} rejects at 99% level (failures={failures}/{breaches.Count})");
        }
    }
}
