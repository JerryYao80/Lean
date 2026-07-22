using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Data.Market;

namespace QuantConnect.Tests.Common.Data
{
    [TestFixture]
    public class TushareDataConverterTests
    {
        [Test]
        public void FilterAvailableDailyBarsExcludesSameDayCloseBeforeMarketClose()
        {
            var symbol = Symbol.Create("000001", SecurityType.Equity, QuantConnect.Market.SZSE);
            var bars = new List<TradeBar>
            {
                new TradeBar { Symbol = symbol, EndTime = new DateTime(2024, 1, 2, 7, 0, 0, DateTimeKind.Utc), Close = 10m },
                new TradeBar { Symbol = symbol, EndTime = new DateTime(2024, 1, 3, 7, 0, 0, DateTimeKind.Utc), Close = 11m },
            };

            var availableAtOpen = TushareDataConverter.FilterAvailableDailyBars(
                bars,
                new DateTime(2024, 1, 1, 0, 0, 0, DateTimeKind.Utc),
                new DateTime(2024, 1, 3, 1, 35, 0, DateTimeKind.Utc));

            var availableAfterClose = TushareDataConverter.FilterAvailableDailyBars(
                bars,
                new DateTime(2024, 1, 1, 0, 0, 0, DateTimeKind.Utc),
                new DateTime(2024, 1, 3, 7, 1, 0, DateTimeKind.Utc));

            CollectionAssert.AreEqual(new[] { 10m }, availableAtOpen.Select(bar => bar.Close).ToList());
            CollectionAssert.AreEqual(new[] { 10m, 11m }, availableAfterClose.Select(bar => bar.Close).ToList());
        }
    }
}
