using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Data;
using QuantConnect.Data.Market;
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

        [Test]
        public void Get_RoutesRuntimeFactorThroughRegistry()
        {
            // Exercises the FactorStore.Get -> RRegistryAdapter -> FactorRegistry.Compute branch
            // (the existing routing test only probed barra_/unknown, both Missing).
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var history = MakeHistory(sym, 25, 100m, 110m);
            var r = store.Get("hv_20d", sym, new DateTime(2026, 7, 22), history);
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.Greater(r.Value, 0m);
        }

        [Test]
        public void Get_RoutesParquetFactorThroughStoreWithInjectedReader()
        {
            // Exercises the FactorStore.Get -> RParquetAdapter.TryGet branch at the store level
            // by registering a parquet-backed factor with an injected fake reader (no Python).
            var store = new FactorStore();
            store.Register("fake_parquet", new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => 0.55m));
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("fake_parquet", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.AreEqual(0.55m, r.Value);
        }

        [Test]
        public void Get_Alpha101Id_ReturnsMissingWhenNoParquet()
        {
            // alpha042 已由 RegisterDefaults 注册 (真实 RParquetAdapter, 默认 reader)。
            // 不存在的日期 -> 文件不存在 -> TryGet 返回 false -> Missing, 不抛。
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha042", sym, new DateTime(1900, 1, 1));
            Assert.AreEqual(FactorDataQuality.Missing, r.Quality);
            Assert.AreEqual(0m, r.Value);
        }

        private static IEnumerable<BaseData> MakeHistory(Symbol sym, int bars, decimal start, decimal end)
        {
            for (int i = 0; i < bars; i++)
            {
                var close = start + (end - start) * i / (bars - 1);
                yield return new TradeBar(new DateTime(2026, 6, 25).AddDays(i), sym, close, close, close, close, 1000m);
            }
        }
    }
}
