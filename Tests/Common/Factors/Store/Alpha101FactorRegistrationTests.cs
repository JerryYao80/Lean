using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class Alpha101FactorRegistrationTests
    {
        [Test]
        public void Register_RegistersAll101Ids()
        {
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            var rep = store.FreshnessReport();
            Assert.IsTrue(rep.ContainsKey("alpha001"), "alpha001 not registered");
            Assert.IsTrue(rep.ContainsKey("alpha042"), "alpha042 not registered");
            Assert.IsTrue(rep.ContainsKey("alpha101"), "alpha101 not registered");
        }

        [Test]
        public void Register_Alpha042_PathResolutionMatchesBuilder()
        {
            string seenPath = null, seenCol = null;
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            store.Register("alpha042", new RParquetAdapter(
                factorRoot: "factor-zoo/alpha042", valueColumn: "alpha042",
                readScalar: (p, c, tsCode) => { seenPath = p; seenCol = c; return 0.42m; }));
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha042", sym, new DateTime(2026, 7, 24));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.AreEqual(0.42m, r.Value);
            Assert.IsTrue(seenPath.Contains("factor-zoo/alpha042"), $"path={seenPath}");
            Assert.IsTrue(seenPath.EndsWith("/2026-07-24/600519.SH.parquet"), $"path={seenPath}");
            Assert.AreEqual("alpha042", seenCol);
        }

        [Test]
        public void Register_TsCode_SH_SZ_ETF()
        {
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            string shPath = null;
            store.Register("alpha001", new RParquetAdapter("factor-zoo/alpha001", "alpha001",
                readScalar: (p, c, tsCode) => { shPath = p; return 0.1m; }));
            store.Get("alpha001", Symbol.Create("600519", SecurityType.Equity, Market.SSE),
                new DateTime(2026, 7, 24));
            Assert.IsTrue(shPath.EndsWith("/600519.SH.parquet"), shPath);
            string szPath = null;
            store.Register("alpha002", new RParquetAdapter("factor-zoo/alpha002", "alpha002",
                readScalar: (p, c, tsCode) => { szPath = p; return 0.1m; }));
            store.Get("alpha002", Symbol.Create("000001", SecurityType.Equity, Market.SZSE),
                new DateTime(2026, 7, 24));
            Assert.IsTrue(szPath.EndsWith("/000001.SZ.parquet"), szPath);
            string etfPath = null;
            store.Register("alpha003", new RParquetAdapter("factor-zoo/alpha003", "alpha003",
                readScalar: (p, c, tsCode) => { etfPath = p; return 0.1m; }));
            store.Get("alpha003", Symbol.Create("518880", SecurityType.Equity, Market.SSE),
                new DateTime(2026, 7, 24));
            Assert.IsTrue(etfPath.EndsWith("/518880.SH.parquet"), etfPath);
        }

        [Test]
        public void Register_MissingParquet_ReturnsMissingNotThrow()
        {
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha042", sym, new DateTime(1900, 1, 1));
            Assert.AreEqual(FactorDataQuality.Missing, r.Quality);
            Assert.AreEqual(0m, r.Value);
        }

        [Test]
        [Explicit]
        public void Get_RealDisk_Alpha042_20260724()
        {
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            var sym = Symbol.Create("603799", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha042", sym, new DateTime(2026, 7, 24));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality,
                "alpha042 missing on disk — run factor_worker or dry-run first");
            Assert.AreEqual(1.06993, (double)r.Value, 0.01);
        }

        [Test]
        [Explicit]
        public void Get_RealDisk_Alpha001_20260724()
        {
            var store = new FactorStore();
            Alpha101FactorRegistration.Register(store);
            var sym = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var r = store.Get("alpha001", sym, new DateTime(2026, 7, 24));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality,
                "alpha001 missing on disk — run factor_worker or dry-run first");
            Assert.AreEqual(0.396667, (double)r.Value, 0.01);
        }
    }
}
