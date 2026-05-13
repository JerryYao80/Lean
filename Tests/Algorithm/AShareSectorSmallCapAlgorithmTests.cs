using System;
using System.Collections.Generic;
using System.Globalization;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareSectorSmallCapAlgorithmTests
    {
        [Test]
        public void ParseDecimalNullableReturnsValueForValidInput()
        {
            Assert.AreEqual(3.14m, AShareSectorSmallCapAlgorithm.ParseDecimalNullable("3.14"));
            Assert.AreEqual(-100m, AShareSectorSmallCapAlgorithm.ParseDecimalNullable("-100"));
        }

        [Test]
        public void ParseDecimalNullableReturnsNullForInvalidInput()
        {
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable("nan"));
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable("None"));
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable("NaN"));
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable(""));
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable(null));
            Assert.IsNull(AShareSectorSmallCapAlgorithm.ParseDecimalNullable("abc"));
        }

        [Test]
        public void GetLotQuantityReturnsMultipleOfHundred()
        {
            Assert.AreEqual(100, AShareSectorSmallCapAlgorithm.GetLotQuantity(15000m, 10m));
            Assert.AreEqual(200, AShareSectorSmallCapAlgorithm.GetLotQuantity(25000m, 10m));
            Assert.AreEqual(0, AShareSectorSmallCapAlgorithm.GetLotQuantity(50m, 10m));
        }

        [Test]
        public void GetLotQuantityReturnsZeroForInvalidInputs()
        {
            Assert.AreEqual(0, AShareSectorSmallCapAlgorithm.GetLotQuantity(0m, 10m));
            Assert.AreEqual(0, AShareSectorSmallCapAlgorithm.GetLotQuantity(1000m, 0m));
            Assert.AreEqual(0, AShareSectorSmallCapAlgorithm.GetLotQuantity(-1000m, 10m));
        }

        [Test]
        public void ComputeMaxDrawdownReturnsZeroForMonotonicIncrease()
        {
            var equity = new List<decimal> { 100m, 110m, 120m, 130m };
            Assert.AreEqual(0m, AShareSectorSmallCapAlgorithm.ComputeMaxDrawdown(equity));
        }

        [Test]
        public void ComputeMaxDrawdownMeasuresPeakToTrough()
        {
            var equity = new List<decimal> { 100m, 120m, 90m, 110m };
            Assert.AreEqual(0.25m, AShareSectorSmallCapAlgorithm.ComputeMaxDrawdown(equity));
        }

        [Test]
        public void ComputeMaxDrawdownReturnsZeroForShortSequence()
        {
            Assert.AreEqual(0m, AShareSectorSmallCapAlgorithm.ComputeMaxDrawdown(new List<decimal> { 100m }));
        }

        [Test]
        public void ComputeSharpeReturnsPositiveForTrend()
        {
            var equity = new List<decimal>();
            for (int i = 0; i < 60; i++)
                equity.Add(100m * (1m + 0.001m * i));
            var sharpe = AShareSectorSmallCapAlgorithm.ComputeSharpe(equity);
            Assert.Greater(sharpe, 0m);
        }

        [Test]
        public void ComputeSharpeReturnsZeroForShortSequence()
        {
            Assert.AreEqual(0m, AShareSectorSmallCapAlgorithm.ComputeSharpe(new List<decimal> { 100m, 101m }));
        }

        [Test]
        public void SelectTopSectorsByMomentumOrdersHighestFirst()
        {
            var factorData = new Dictionary<string, Dictionary<string, AShareSectorSmallCapAlgorithm.FactorRow>>
            {
                ["20200101"] = new()
                {
                    ["000001.SZ"] = new() { TsCode = "000001.SZ", Sector = "银行", Momentum20d = 0.10m },
                    ["000002.SZ"] = new() { TsCode = "000002.SZ", Sector = "银行", Momentum20d = 0.12m },
                    ["000003.SZ"] = new() { TsCode = "000003.SZ", Sector = "银行", Momentum20d = 0.08m },
                    ["600000.SH"] = new() { TsCode = "600000.SH", Sector = "食品饮料", Momentum20d = 0.20m },
                    ["600001.SH"] = new() { TsCode = "600001.SH", Sector = "食品饮料", Momentum20d = 0.22m },
                    ["600002.SH"] = new() { TsCode = "600002.SH", Sector = "食品饮料", Momentum20d = 0.18m },
                    ["300001.SZ"] = new() { TsCode = "300001.SZ", Sector = "汽车", Momentum20d = -0.05m },
                    ["300002.SZ"] = new() { TsCode = "300002.SZ", Sector = "汽车", Momentum20d = -0.03m },
                    ["300003.SZ"] = new() { TsCode = "300003.SZ", Sector = "汽车", Momentum20d = -0.01m },
                }
            };

            var top = AShareSectorSmallCapAlgorithm.SelectTopSectorsByMomentum(factorData, "20200101", 2);
            Assert.AreEqual(2, top.Count);
            Assert.AreEqual("食品饮料", top[0]);
            Assert.AreEqual("银行", top[1]);
        }

        [Test]
        public void SelectTopSectorsByMomentumSkipsSectorsWithTooFewStocks()
        {
            var factorData = new Dictionary<string, Dictionary<string, AShareSectorSmallCapAlgorithm.FactorRow>>
            {
                ["20200101"] = new()
                {
                    ["000001.SZ"] = new() { TsCode = "000001.SZ", Sector = "银行", Momentum20d = 0.50m },
                    ["600000.SH"] = new() { TsCode = "600000.SH", Sector = "食品饮料", Momentum20d = 0.10m },
                    ["600001.SH"] = new() { TsCode = "600001.SH", Sector = "食品饮料", Momentum20d = 0.12m },
                    ["600002.SH"] = new() { TsCode = "600002.SH", Sector = "食品饮料", Momentum20d = 0.08m },
                }
            };

            var top = AShareSectorSmallCapAlgorithm.SelectTopSectorsByMomentum(factorData, "20200101", 3, minStocksPerSector: 3);
            Assert.AreEqual(1, top.Count);
            Assert.AreEqual("食品饮料", top[0]);
        }

        [Test]
        public void SelectSmallCapStocksInSectorsPicksSmallestCapWithinCapAndPbFilter()
        {
            var factors = new Dictionary<string, AShareSectorSmallCapAlgorithm.FactorRow>
            {
                ["000001.SZ"] = new() { TsCode = "000001.SZ", Sector = "银行", TotalMvWan = 200_000m, Pb = 1.0m },
                ["000002.SZ"] = new() { TsCode = "000002.SZ", Sector = "银行", TotalMvWan = 100_000m, Pb = 2.0m },
                ["000003.SZ"] = new() { TsCode = "000003.SZ", Sector = "银行", TotalMvWan = 600_000m, Pb = 1.0m },
                ["000004.SZ"] = new() { TsCode = "000004.SZ", Sector = "银行", TotalMvWan = 150_000m, Pb = 8.0m },
            };

            var selected = AShareSectorSmallCapAlgorithm.SelectSmallCapStocksInSectors(
                factors, new HashSet<string> { "银行" }, stocksPerSector: 2, maxMarketCapWan: 500_000m, maxPb: 5.0m);

            Assert.AreEqual(2, selected.Count);
            Assert.AreEqual("000002.SZ", selected[0]);
            Assert.AreEqual("000001.SZ", selected[1]);
        }

        [Test]
        public void SelectSmallCapStocksExceedsMaxCapFiltered()
        {
            var factors = new Dictionary<string, AShareSectorSmallCapAlgorithm.FactorRow>
            {
                ["000001.SZ"] = new() { TsCode = "000001.SZ", Sector = "汽车", TotalMvWan = 600_000m, Pb = 1.0m },
            };

            var selected = AShareSectorSmallCapAlgorithm.SelectSmallCapStocksInSectors(
                factors, new HashSet<string> { "汽车" }, stocksPerSector: 5, maxMarketCapWan: 500_000m, maxPb: 5.0m);

            Assert.AreEqual(0, selected.Count);
        }
    }
}
