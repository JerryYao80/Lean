using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class RParquetAdapterTests
    {
        [Test]
        public void TryGet_InjectedReader_ReturnsScalar()
        {
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => 0.42m);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsTrue(ok);
            Assert.AreEqual(0.42m, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void TryGet_InjectedReaderReturnsNull_Missing()
        {
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => (decimal?)null);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        [Test]
        public void TryGet_ResolvesParquetPathAndTsCode()
        {
            string seenPath = null, seenCol = null;
            var adapter = new RParquetAdapter(
                factorRoot: "crowding-factor", valueColumn: "composite",
                resultRoot: "/tmp/fz_parquet_test_root",
                readScalar: (path, column) => { seenPath = path; seenCol = column; return 0.1m; });
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out _);
            Assert.AreEqual("/tmp/fz_parquet_test_root/crowding-factor/2026-07-22/600519.SH.parquet", seenPath);
            Assert.AreEqual("composite", seenCol);
        }
    }
}
