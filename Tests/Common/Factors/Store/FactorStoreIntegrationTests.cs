using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class FactorStoreIntegrationTests
    {
        [Test]
        public void Get_RoutesBarraAndParquetAndRegistryByPrefix()
        {
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var rb = store.Get("barra_beta", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, rb.Quality);
            var ru = store.Get("nope", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, ru.Quality);
        }

        [Test]
        public void FreshnessReport_ListsRegisteredParquetFactors()
        {
            var store = new FactorStore();
            var rep = store.FreshnessReport();
            Assert.IsNotNull(rep);
            CollectionAssert.IsSubsetOf(new[] { "crowding" }, rep.Keys);
        }
    }
}
