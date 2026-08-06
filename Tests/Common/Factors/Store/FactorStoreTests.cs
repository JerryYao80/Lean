using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class FactorStoreTests
    {
        [Test]
        public void Get_UnknownFactorId_ReturnsMissing()
        {
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("does_not_exist", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, r.Quality);
            Assert.AreEqual(0m, r.Value);
        }

        [Test]
        public void Get_RoutesToRegisteredAdapter()
        {
            var store = new FactorStore();
            var fake = new FakeAdapter();
            store.Register("fake_factor", fake);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("fake_factor", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.AreEqual(1.23m, r.Value);
            Assert.IsTrue(fake.Called);
        }

        [Test]
        public void AllMetadata_AggregatesFromFactorRegistry()
        {
            FactorRegistry.Initialize();
            var store = new FactorStore();
            var meta = store.AllMetadata();
            Assert.IsTrue(meta.ContainsKey("crowding"));
            Assert.IsTrue(meta.ContainsKey("hv_20d"));
        }

        [Test]
        public void Get_Alpha101IndependentId_RoutesToParquetAdapter()
        {
            // alpha042 由 RegisterDefaults 注册。注入 fake reader 替换它, 验证端到端路由。
            var store = new FactorStore();
            store.Register("alpha042", new RParquetAdapter(
                factorRoot: "factor-zoo/alpha042", valueColumn: "alpha042",
                readScalar: (path, column, tsCode) => 0.55m));
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha042", sym, new DateTime(2026, 7, 24));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.AreEqual(0.55m, r.Value);
        }

        private class FakeAdapter : IFactorAdapter
        {
            public bool Called;
            public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
            {
                Called = true;
                result = new FactorResult { Value = 1.23m, RawValue = 1.23m, Time = date, Symbol = symbol, FactorId = "fake_factor", Quality = FactorDataQuality.Valid };
                return true;
            }
        }
    }
}
