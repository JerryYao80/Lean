using System;
using System.IO;
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

        [Test]
        public void TryGet_ResolvesTsCode_SZ_Branch()
        {
            // 000001 (PingAn Bank, SZSE) -> 000001.SZ (not .SH). Covers the SZ branch.
            string seenPath = null;
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                resultRoot: "/tmp/fz_parquet_test_root",
                readScalar: (path, column) => { seenPath = path; return 0.1m; });
            var sym = Symbol.Create("000001", SecurityType.Equity, Market.SZSE);
            adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out _);
            Assert.IsTrue(seenPath.EndsWith("/000001.SZ.parquet"), $"resolved {seenPath}");
        }

        [Test]
        public void TryGet_ResolvesTsCode_51Prefix_SH_ETF()
        {
            // 518880 (gold ETF, SSE) -> 518880.SH (regression: the old 6/9-only rule
            // misclassified 5-prefix SH ETFs as .SZ, so their parquet was never found).
            string seenPath = null;
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                resultRoot: "/tmp/fz_parquet_test_root",
                readScalar: (path, column) => { seenPath = path; return 0.1m; });
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out _);
            Assert.IsTrue(seenPath.EndsWith("/518880.SH.parquet"), $"resolved {seenPath}");
        }

        [Test]
        public void TryGet_ReaderThrows_ReturnsMissingNotThrow()
        {
            // The adapter must swallow a throwing reader delegate (corrupt parquet / python
            // runtime error) and return Missing rather than propagating the exception.
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => throw new InvalidOperationException("boom"));
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        [Test]
        public void TryGet_DefaultResultRoot_ResolvesToRealLeanResultDir()
        {
            // Regression for the off-by-one (".." count) bug in DefaultResultRoot. A
            // default-constructed crowding adapter (no resultRoot) must resolve to
            // .../Lean/result/crowding-factor (real, 117 date subdirs on disk), NOT
            // .../hope/result (the broken two-".." resolution).
            string seenPath = null;
            var adapter = new RParquetAdapter("crowding-factor", "composite",
                readScalar: (path, column) => { seenPath = path; return (decimal?)null; });
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            adapter.TryGet(sym, new DateTime(2024, 1, 2), null, out _);
            Assert.IsNotNull(seenPath, "reader was not invoked");
            Assert.IsTrue(seenPath.Contains("/Lean/result/"),
                $"default root resolved to {seenPath} (expected .../Lean/result/...)");
            var dateDir = Path.GetDirectoryName(seenPath);
            var rootDir = Path.GetDirectoryName(dateDir);
            Assert.IsTrue(Directory.Exists(rootDir),
                $"default result root {rootDir} does not exist on disk (off-by-one bug)");
        }
    }
}
