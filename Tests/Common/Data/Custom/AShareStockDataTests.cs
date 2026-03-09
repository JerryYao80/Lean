using System;
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Data.Custom;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Data.Custom
{
    [TestFixture]
    public class AShareStockDataTests
    {
        [TestCase("600000", global::QuantConnect.Market.SSE, "600000.SH")]
        [TestCase("000001", global::QuantConnect.Market.SZSE, "000001.SZ")]
        public void ToTsCodeMapsMarketSuffix(string ticker, string market, string expected)
        {
            var symbol = Symbol.Create(ticker, SecurityType.Base, market);
            Assert.AreEqual(expected, AShareStockData.ToTsCode(symbol));
        }

        [Test]
        public void LeanPathUsesEquityDailyHierarchy()
        {
            var symbol = Symbol.Create("600000", SecurityType.Base, global::QuantConnect.Market.SSE);
            var path = AShareStockData.GetLeanDataPath("/tmp/data", symbol, new DateTime(2024, 3, 5), Resolution.Daily);
            var normalized = path.Replace('\\', '/');

            Assert.That(normalized, Does.EndWith("/equity/china/daily/600000.zip").Or.EndWith("/equity/sse/daily/600000.zip"));
        }

        [Test]
        public void ReaderParsesLeanDailyBarAndPreservesTsCode()
        {
            var symbol = Symbol.Create("600000", SecurityType.Base, global::QuantConnect.Market.SSE);
            var config = new SubscriptionDataConfig(typeof(AShareStockData), symbol, Resolution.Daily, TimeZones.Shanghai, TimeZones.Shanghai, true, false, false);
            var data = (AShareStockData)new AShareStockData().Reader(
                config,
                "20240305 00:00,100000,101000,99000,100500,123400",
                new DateTime(2024, 3, 5),
                false);

            Assert.IsNotNull(data);
            Assert.AreEqual(symbol, data.Symbol);
            Assert.AreEqual(new DateTime(2024, 3, 5), data.Time);
            Assert.AreEqual(new DateTime(2024, 3, 6), data.EndTime);
            Assert.AreEqual(10.0m, data.Open);
            Assert.AreEqual(10.1m, data.High);
            Assert.AreEqual(9.9m, data.Low);
            Assert.AreEqual(10.05m, data.Close);
            Assert.AreEqual(123400m, data.Volume);
            Assert.AreEqual("600000.SH", data.TsCode);
        }
    }
}
