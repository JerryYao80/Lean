using System;
using System.IO;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class RBarraAdapterTests
    {
        private static string WriteBarraCsv(string dir, string ticker, string market)
        {
            Directory.CreateDirectory(Path.Combine(dir, market, "daily"));
            var path = Path.Combine(dir, market, "daily", $"{ticker}.csv");
            File.WriteAllText(path,
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,moneyflow,quality,northbound,margin,chipcost,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n" +
                "20260721,-0.04,0.12,12.3,0.01,0.02,0.03,0.4,1.2,0.5,11.0,0.0,0.15,0.0,0.0,0.1,1e10,0.2,2000,0,0\n" +
                "20260722,-0.05,0.13,12.4,0.011,0.021,0.031,0.41,1.21,0.51,11.1,0.0,0.16,0.0,0.0,0.11,1.05e10,0.21,2001,0,0\n");
            return path;
        }

        [Test]
        public void TryGet_ReadsBarraColumnForDate()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            WriteBarraCsv(dir, "600519", "sse");
            try
            {
                var adapter = new RBarraAdapter("beta", dataRoot: dir);
                var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
                var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
                Assert.IsTrue(ok);
                Assert.AreEqual(-0.05m, result.Value);
                Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        }

        [Test]
        public void TryGet_MissingDate_ReturnsMissingNotThrow()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test2");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            WriteBarraCsv(dir, "600519", "sse");
            try
            {
                var adapter = new RBarraAdapter("beta", dataRoot: dir);
                var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
                var ok = adapter.TryGet(sym, new DateTime(2099, 1, 1), null, out var result);
                Assert.IsFalse(ok);
                Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        }

        [Test]
        public void TryGet_MissingCsvFile_ReturnsMissing()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test3");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            var adapter = new RBarraAdapter("beta", dataRoot: dir);
            var sym = Symbol.Create("999999", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }
    }
}
