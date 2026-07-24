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
    public class RRegistryAdapterTests
    {
        [Test]
        public void TryGet_RuntimeFactor_DelegatesToFactorRegistryCompute()
        {
            FactorRegistry.Initialize();
            var adapter = new RRegistryAdapter("hv_20d");
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var history = MakeHistory(sym, 25, 100m, 110m);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), history, out var result);
            Assert.IsTrue(ok);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
            Assert.Greater(result.Value, 0m); // realized vol of a rising series is positive
        }

        [Test]
        public void TryGet_UnknownIdInRegistry_ReturnsMissingNotThrow()
        {
            FactorRegistry.Initialize();
            var adapter = new RRegistryAdapter("not_a_registered_runtime_factor");
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
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
