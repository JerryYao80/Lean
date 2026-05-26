using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Data;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareImpliedVolatilityDataTests
    {
        [SetUp]
        public void SetUp()
        {
            AShareImpliedVolatilityData.SetBaseDirectory("/tmp/ashare-iv-test");
        }

        [Test]
        public void GetSourceBuildsLocalCsvPath()
        {
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var source = new AShareImpliedVolatilityData().GetSource(config, new DateTime(2024, 6, 1), false);
            var expected = Path.Combine("/tmp/ashare-iv-test", "sse", "daily", "510050.csv");

            Assert.AreEqual(expected, source.Source);
            Assert.AreEqual(SubscriptionTransportMedium.LocalFile, source.TransportMedium);
            Assert.AreEqual(FileFormat.Csv, source.Format);
        }

        [Test]
        public void ReaderParsesFullIvSnapshot()
        {
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            const string line = "20240131,0.25100000,0.28100000,0.23100000,0.05000000,28,56,42,22.350000,24.100000,19.800000,0.11570248,0.23140496";

            var data = new AShareImpliedVolatilityData().Reader(config, line, new DateTime(2024, 1, 31), false) as AShareImpliedVolatilityData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(new DateTime(2024, 1, 31), data.EndTime);
            Assert.AreEqual(0.251m, data.AtmIv);
            Assert.AreEqual(0.281m, data.IvCall25Delta);
            Assert.AreEqual(0.231m, data.IvPut25Delta);
            Assert.AreEqual(0.05m, data.Skew);
            Assert.AreEqual(28, data.TermDaysNear);
            Assert.AreEqual(56, data.TermDaysNext);
            Assert.AreEqual(42, data.OptionCount);
            Assert.AreEqual(22.35m, data.Vix);
            Assert.AreEqual(24.1m, data.SigmaNear);
            Assert.AreEqual(19.8m, data.SigmaNext);
            Assert.AreEqual(0.11570248m, data.TNear);
            Assert.AreEqual(0.23140496m, data.TNext);
            Assert.AreEqual(0.251m, data.Value);
        }

        [Test]
        public void ReaderReturnsNullForHeaderLine()
        {
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var result = new AShareImpliedVolatilityData().Reader(config, "trade_date,atm_iv,iv_call_25delta", new DateTime(2024, 1, 31), false);

            Assert.IsNull(result);
        }

        [Test]
        public void ReaderReturnsNullForShortLine()
        {
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var result = new AShareImpliedVolatilityData().Reader(config, "20240131,0.25", new DateTime(2024, 1, 31), false);

            Assert.IsNull(result);
        }

        [Test]
        public void IvTermStructureRatioComputesCorrectly()
        {
            var data = new AShareImpliedVolatilityData
            {
                SigmaNear = 24.1m,
                SigmaNext = 19.8m,
            };

            Assert.AreEqual(24.1m / 19.8m, data.IvTermStructureRatio);
        }

        [Test]
        public void IvTermStructureRatioReturnsNullWhenMissing()
        {
            var data = new AShareImpliedVolatilityData
            {
                SigmaNear = null,
                SigmaNext = 19.8m,
            };

            Assert.IsNull(data.IvTermStructureRatio);
        }

        [Test]
        public void ReaderHandlesMissingVixColumns()
        {
            var underlying = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            // Only 8 columns (IV fields, no VIX)
            const string line = "20240131,0.25100000,0.28100000,0.23100000,0.05000000,28,56,42";

            var data = new AShareImpliedVolatilityData().Reader(config, line, new DateTime(2024, 1, 31), false) as AShareImpliedVolatilityData;

            Assert.IsNotNull(data);
            Assert.AreEqual(0.251m, data.AtmIv);
            Assert.IsNull(data.Vix);
            Assert.IsNull(data.SigmaNear);
            Assert.IsNull(data.SigmaNext);
        }

        [Test]
        public void ComputeSignalsAveragesAcrossUnderlyings()
        {
            var symbol1 = Symbol.Create("510050", SecurityType.Equity, Market.SSE);
            var symbol2 = Symbol.Create("510300", SecurityType.Equity, Market.SSE);

            var ivData = new Dictionary<string, AShareImpliedVolatilityData>
            {
                ["510050.SH"] = new AShareImpliedVolatilityData
                {
                    Skew = 0.04m,
                    AtmIv = 0.22m,
                    SigmaNear = 22.0m,
                    SigmaNext = 20.0m,
                    OptionCount = 30,
                },
                ["510300.SH"] = new AShareImpliedVolatilityData
                {
                    Skew = 0.06m,
                    AtmIv = 0.26m,
                    SigmaNear = 26.0m,
                    SigmaNext = 20.0m,
                    OptionCount = 50,
                },
            };

            var settings = new AShareImpliedVolatilitySignalSettings();
            var (skew, ivts, ivLevel) = AShareImpliedVolatilitySignalModel.ComputeSignals(ivData, settings);

            Assert.AreEqual(0.05m, skew);
            Assert.AreEqual(1.2m, ivts);  // (22/20 + 26/20) / 2 = (1.1 + 1.3) / 2 = 1.2
            Assert.AreEqual(0.24m, ivLevel);
        }

        [Test]
        public void DetermineRegimeReturnsFearWhenAllSignalsHigh()
        {
            var settings = new AShareImpliedVolatilitySignalSettings
            {
                SkewHighThreshold = 0.05m,
                SkewLowThreshold = -0.02m,
                IvtsHighThreshold = 1.2m,
                IvtsLowThreshold = 0.8m,
                IvLevelHighThreshold = 0.30m,
                IvLevelLowThreshold = 0.15m,
            };

            var regime = AShareImpliedVolatilitySignalModel.DetermineRegime(0.08m, 1.5m, 0.35m, settings);
            Assert.AreEqual("fear", regime);
        }

        [Test]
        public void DetermineRegimeReturnsGreedWhenAllSignalsLow()
        {
            var settings = new AShareImpliedVolatilitySignalSettings
            {
                SkewHighThreshold = 0.05m,
                SkewLowThreshold = -0.02m,
                IvtsHighThreshold = 1.2m,
                IvtsLowThreshold = 0.8m,
                IvLevelHighThreshold = 0.30m,
                IvLevelLowThreshold = 0.15m,
            };

            var regime = AShareImpliedVolatilitySignalModel.DetermineRegime(-0.01m, 0.7m, 0.10m, settings);
            Assert.AreEqual("greed", regime);
        }

        [Test]
        public void ComputeAllocationReturnsFearWeightsForFearRegime()
        {
            var (eqW, safeW) = AShareImpliedVolatilitySignalModel.ComputeAllocation(
                "fear", 0.10m, 0.60m, 0.70m, 0.20m);

            Assert.AreEqual(0.10m, eqW);
            Assert.AreEqual(0.70m, safeW);
        }

        [Test]
        public void ComputeAllocationReturnsGreedWeightsForGreedRegime()
        {
            var (eqW, safeW) = AShareImpliedVolatilitySignalModel.ComputeAllocation(
                "greed", 0.10m, 0.60m, 0.70m, 0.20m);

            Assert.AreEqual(0.60m, eqW);
            Assert.AreEqual(0.20m, safeW);
        }

        [Test]
        public void NormalizeSignalClampsToZeroOne()
        {
            Assert.AreEqual(0m, AShareImpliedVolatilitySignalModel.NormalizeSignal(-5m, 0m, 10m));
            Assert.AreEqual(1m, AShareImpliedVolatilitySignalModel.NormalizeSignal(20m, 0m, 10m));
            Assert.AreEqual(0.5m, AShareImpliedVolatilitySignalModel.NormalizeSignal(5m, 0m, 10m));
        }

        [Test]
        public void ComputeSignalsSkipsLowOptionCount()
        {
            var ivData = new Dictionary<string, AShareImpliedVolatilityData>
            {
                ["510050.SH"] = new AShareImpliedVolatilityData
                {
                    Skew = 0.04m,
                    AtmIv = 0.22m,
                    SigmaNear = 22.0m,
                    SigmaNext = 20.0m,
                    OptionCount = 2,  // below min
                },
            };

            var settings = new AShareImpliedVolatilitySignalSettings { MinOptionCount = 4 };
            var (skew, ivts, ivLevel) = AShareImpliedVolatilitySignalModel.ComputeSignals(ivData, settings);

            Assert.AreEqual(0m, skew);
            Assert.AreEqual(1.0m, ivts);
            Assert.AreEqual(0.20m, ivLevel);
        }

        private static SubscriptionDataConfig CreateConfig(Symbol underlying)
        {
            var customSymbol = Symbol.CreateBase(typeof(AShareImpliedVolatilityData), underlying, underlying.ID.Market);
            return new SubscriptionDataConfig(
                typeof(AShareImpliedVolatilityData),
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