using System;
using System.IO;
using NodaTime;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Data;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class GdeltNewsSentimentDataTests
    {
        [SetUp]
        public void SetUp()
        {
            GdeltNewsSentimentData.SetBaseDirectory("/tmp/gdelt-news-sentiment");
        }

        [Test]
        public void GetSourceBuildsLocalCsvPathFromUnderlyingMarket()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var source = new GdeltNewsSentimentData().GetSource(config, new DateTime(2024, 1, 31), false);
            var expected = Path.Combine("/tmp/gdelt-news-sentiment", "sse", "daily", "600000.csv");

            Assert.AreEqual(expected, source.Source);
            Assert.AreEqual(SubscriptionTransportMedium.LocalFile, source.TransportMedium);
            Assert.AreEqual(FileFormat.Csv, source.Format);
        }

        [Test]
        public void ReaderParsesDailyGdeltSnapshot()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            const string line = "20240131,Shanghai Pudong Development Bank,12,-1.250000,2,7,3,5,reuters.com";

            var data = new GdeltNewsSentimentData().Reader(config, line, new DateTime(2024, 1, 31), false) as GdeltNewsSentimentData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(underlying, data.UnderlyingSymbol);
            Assert.AreEqual(new DateTime(2024, 1, 31), data.EndTime);
            Assert.AreEqual(-1.25m, data.Value);
            Assert.AreEqual("Shanghai Pudong Development Bank", data.Query);
            Assert.AreEqual(12, data.ArticleCount);
            Assert.AreEqual(2, data.PositiveCount);
            Assert.AreEqual(7, data.NegativeCount);
            Assert.AreEqual(3, data.NeutralCount);
            Assert.AreEqual(5, data.SourceCount);
            Assert.AreEqual("reuters.com", data.TopDomain);
        }

        private static SubscriptionDataConfig CreateConfig(Symbol underlying)
        {
            var customSymbol = Symbol.CreateBase(typeof(GdeltNewsSentimentData), underlying, underlying.ID.Market);
            return new SubscriptionDataConfig(
                typeof(GdeltNewsSentimentData),
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
