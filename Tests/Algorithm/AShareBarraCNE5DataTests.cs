using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NodaTime;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Data;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareBarraCNE5DataTests
    {
        [SetUp]
        public void SetUp()
        {
            AShareBarraCNE5FactorData.SetBaseDirectory("/tmp/barra-cne5-factors");
        }

        [Test]
        public void GetSourceBuildsLocalCsvPathFromUnderlyingMarket()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var source = new AShareBarraCNE5FactorData().GetSource(config, new DateTime(2024, 1, 31), false);
            var expected = Path.Combine("/tmp/barra-cne5-factors", "sse", "daily", "600000.csv");

            Assert.AreEqual(expected, source.Source);
            Assert.AreEqual(SubscriptionTransportMedium.LocalFile, source.TransportMedium);
            Assert.AreEqual(FileFormat.Csv, source.Format);
        }

        [Test]
        public void ReaderParsesBarraSnapshot()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            const string line = "20240131,-0.500000,1.250000,-0.200000,0.800000,-0.150000,0.550000,0.330000,-0.120000,0.440000,0.010000,2500000.000000,1.250000,600,2,0";

            var data = new AShareBarraCNE5FactorData().Reader(config, line, new DateTime(2024, 1, 31), false) as AShareBarraCNE5FactorData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(underlying, data.UnderlyingSymbol);
            Assert.AreEqual(new DateTime(2024, 1, 31), data.EndTime);
            Assert.AreEqual(1.25m, data.Momentum);
            Assert.AreEqual(0.8m, data.EarningsYield);
            Assert.AreEqual(2500000m, data.TotalMv);
            Assert.AreEqual(600, data.ListedDays);
            Assert.AreEqual(2, data.MissingFactorCount);
            Assert.IsFalse(data.IsSt);
        }

        [Test]
        public void SignalModelRanksHigherMomentumAndValueHigher()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);

            var scores = AShareBarraCNE5SignalModel.ComputeScores(
                new Dictionary<Symbol, AShareBarraCNE5FactorData>
                {
                    [symbolA] = new AShareBarraCNE5FactorData
                    {
                        Beta = 1.2m, Momentum = -0.5m, Size = 1.0m, EarningsYield = -0.5m, ResidualVolatility = 1.0m,
                        Growth = -0.4m, BookToPrice = -0.4m, Leverage = 0.9m, Liquidity = 0.2m, NonLinearSize = 0m
                    },
                    [symbolB] = new AShareBarraCNE5FactorData
                    {
                        Beta = 0.9m, Momentum = 0.2m, Size = 0.1m, EarningsYield = 0.3m, ResidualVolatility = 0.2m,
                        Growth = 0.1m, BookToPrice = 0.2m, Leverage = 0.1m, Liquidity = 0.1m, NonLinearSize = 0m
                    },
                    [symbolC] = new AShareBarraCNE5FactorData
                    {
                        Beta = 0.6m, Momentum = 1.0m, Size = -0.8m, EarningsYield = 0.9m, ResidualVolatility = -0.4m,
                        Growth = 0.8m, BookToPrice = 0.7m, Leverage = -0.3m, Liquidity = 0.4m, NonLinearSize = 0m
                    },
                },
                new AShareBarraCNE5SignalSettings());

            var ranked = scores.OrderByDescending(pair => pair.Value).Select(pair => pair.Key.Value).ToList();
            CollectionAssert.AreEqual(new[] { "C", "B", "A" }, ranked);
        }

        [Test]
        public void ShouldRebalanceSupportsMonthlyAndBiweeklyFrequencies()
        {
            Assert.IsTrue(AShareBarraCNE5Algorithm.ShouldRebalance(new DateTime(2024, 2, 1), default, "monthly"));
            Assert.IsFalse(AShareBarraCNE5Algorithm.ShouldRebalance(new DateTime(2024, 2, 15), new DateTime(2024, 2, 1), "monthly"));
            Assert.IsTrue(AShareBarraCNE5Algorithm.ShouldRebalance(new DateTime(2024, 3, 1), new DateTime(2024, 2, 1), "monthly"));
            Assert.IsFalse(AShareBarraCNE5Algorithm.ShouldRebalance(new DateTime(2024, 2, 10), new DateTime(2024, 2, 1), "biweekly"));
            Assert.IsTrue(AShareBarraCNE5Algorithm.ShouldRebalance(new DateTime(2024, 2, 16), new DateTime(2024, 2, 1), "biweekly"));
        }

        [Test]
        public void ExposureComputationProducesWeightedAverage()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var exposure = AShareBarraCNE5SignalModel.ComputePortfolioExposure(
                new Dictionary<Symbol, decimal>
                {
                    [symbolA] = 0.6m,
                    [symbolB] = 0.4m,
                },
                new Dictionary<Symbol, AShareBarraCNE5FactorData>
                {
                    [symbolA] = new AShareBarraCNE5FactorData { Beta = -0.5m, Momentum = 1.0m },
                    [symbolB] = new AShareBarraCNE5FactorData { Beta = 0.5m, Momentum = 0.0m },
                });

            Assert.AreEqual(-0.1m, exposure["beta"]);
            Assert.AreEqual(0.6m, exposure["momentum"]);
        }

        private static SubscriptionDataConfig CreateConfig(Symbol underlying)
        {
            var customSymbol = Symbol.CreateBase(typeof(AShareBarraCNE5FactorData), underlying, underlying.ID.Market);
            return new SubscriptionDataConfig(
                typeof(AShareBarraCNE5FactorData),
                customSymbol,
                Resolution.Daily,
                TimeZones.Shanghai,
                TimeZones.Shanghai,
                false,
                false,
                false);
        }
    }
}
