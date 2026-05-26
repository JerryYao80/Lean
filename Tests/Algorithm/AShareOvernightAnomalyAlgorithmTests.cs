using System;
using System.Collections.Generic;
using System.Globalization;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareOvernightAnomalyAlgorithmTests
    {
        [Test]
        public void ComputeMaxDrawdown_EmptyList_ReturnsZero()
        {
            var equity = new List<decimal> { 100m };
            Assert.AreEqual(0m, AShareOvernightAnomalyAlgorithm.ComputeMaxDrawdown(equity));
        }

        [Test]
        public void ComputeMaxDrawdown_NoDrawdown_ReturnsZero()
        {
            var equity = new List<decimal> { 100m, 110m, 120m };
            Assert.AreEqual(0m, AShareOvernightAnomalyAlgorithm.ComputeMaxDrawdown(equity));
        }

        [Test]
        public void ComputeMaxDrawdown_WithDrawdown_ReturnsCorrect()
        {
            var equity = new List<decimal> { 100m, 120m, 90m, 110m };
            // Peak=120, trough=90, dd = 30/120 = 0.25
            Assert.AreEqual(0.25m, AShareOvernightAnomalyAlgorithm.ComputeMaxDrawdown(equity));
        }

        [Test]
        public void ComputeSharpe_InsufficientData_ReturnsZero()
        {
            var equity = new List<decimal> { 100m, 101m };
            Assert.AreEqual(0m, AShareOvernightAnomalyAlgorithm.ComputeSharpe(equity));
        }

        [Test]
        public void ComputeSharpe_PositiveReturns_ReturnsPositive()
        {
            var equity = new List<decimal>();
            for (int i = 0; i < 50; i++)
                equity.Add(1000m + i * 10m);
            var sharpe = AShareOvernightAnomalyAlgorithm.ComputeSharpe(equity);
            Assert.Greater(sharpe, 0m);
        }

        [Test]
        public void ComputeAnnualizedVol_InsufficientData_ReturnsDefault()
        {
            var returns = new List<decimal> { 0.01m, 0.02m };
            Assert.AreEqual(0.2m, AShareOvernightAnomalyAlgorithm.ComputeAnnualizedVol(returns));
        }

        [Test]
        public void ComputeAnnualizedVol_KnownValues()
        {
            // Constant returns should give zero vol
            var returns = new List<decimal>();
            for (int i = 0; i < 20; i++)
                returns.Add(0.01m);
            var vol = AShareOvernightAnomalyAlgorithm.ComputeAnnualizedVol(returns);
            Assert.Less(Math.Abs((double)vol), 0.01);
        }

        [Test]
        public void ParseUniverse_Null_ReturnsEmpty()
        {
            var result = AShareOvernightAnomalyAlgorithm.ParseUniverse(null);
            Assert.IsEmpty(result);
        }

        [Test]
        public void ParseUniverse_CommaSeparated_ReturnsList()
        {
            var result = AShareOvernightAnomalyAlgorithm.ParseUniverse("518880.SH,510050.SH,510300.SH");
            Assert.AreEqual(3, result.Count);
            Assert.Contains("518880.SH", result);
            Assert.Contains("510050.SH", result);
            Assert.Contains("510300.SH", result);
        }

        [Test]
        public void ParseUniverse_Deduplicates()
        {
            var result = AShareOvernightAnomalyAlgorithm.ParseUniverse("510050.SH,510050.SH");
            Assert.AreEqual(1, result.Count);
        }

        [Test]
        public void ToTsCode_SSE()
        {
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            Assert.AreEqual("518880.SH", AShareOvernightAnomalyAlgorithm.ToTsCode(sym));
        }

        [Test]
        public void ToTsCode_SZSE()
        {
            var sym = Symbol.Create("159919", SecurityType.Equity, Market.SZSE);
            Assert.AreEqual("159919.SZ", AShareOvernightAnomalyAlgorithm.ToTsCode(sym));
        }

        [Test]
        public void GetLotQuantity_NormalCase()
        {
            // 10000 capital, 2.5 price, lot size 100
            var qty = AShareOvernightAnomalyAlgorithm.GetLotQuantity(10000m, 2.5m);
            Assert.AreEqual(4000, qty);  // floor(10000 / (2.5 * 100)) * 100 = 4000
        }

        [Test]
        public void GetLotQuantity_ZeroCapital_ReturnsZero()
        {
            Assert.AreEqual(0, AShareOvernightAnomalyAlgorithm.GetLotQuantity(0m, 2.5m));
        }

        [Test]
        public void GetLotQuantity_ZeroPrice_ReturnsZero()
        {
            Assert.AreEqual(0, AShareOvernightAnomalyAlgorithm.GetLotQuantity(10000m, 0m));
        }

        [Test]
        public void DailyReturns_ComputesCorrectly()
        {
            var equity = new List<decimal> { 100m, 101m, 103m, 100m };
            var returns = AShareOvernightAnomalyAlgorithm.DailyReturns(equity, 10);
            Assert.AreEqual(3, returns.Count);
            Assert.AreEqual(0.01m, returns[0]);
        }
    }
}
