/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0. You may obtain a copy
 * License at http://www.apache.org/licenses/LICENSE-2.0.
 *
 * TDD test for Alpha101CompositeAlphaModel — the FIRST real FactorStore (Phase-2
 * pull API) consumer. The model ctor auto-registers 101 alpha adapters via
 * FactorStoreConfig.RegisterDefaults; here we override the 8 used alphas with
 * fake RParquetAdapters whose readScalar delegate returns deterministic values,
 * so the test exercises the entire cross-sectional z-score + top-quantile
 * selection path without touching disk or Python.
 */

using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Securities;
using QuantConnect.Tests.Engine.DataFeeds;

namespace QuantConnect.Tests.Common.Algorithm.Models.Alpha
{
    [TestFixture]
    public class Alpha101CompositeAlphaModelTests
    {
        // Five A-share equities — used as the active universe. Mix of 6-prefix (SH)
        // and 0-prefix (SZ) so SymbolToTsCode resolves both .SH and .SZ branches.
        private static readonly string[] Tickers =
        {
            "600519", "600036", "000001", "000002", "300750",
        };

        /// <summary>
        /// Builds a fake FactorStore where each of the 8 alpha ids routes to an
        /// RParquetAdapter whose readScalar delegate extracts the ts_code from the
        /// parquet path and looks up a per-(alpha, ticker) value. This exercises
        /// the entire cross-sectional z-score + sign + top-quantile path with no
        /// disk or Python.
        /// </summary>
        private static FactorStore MakeFakeStore(Dictionary<(string alpha, string ticker), decimal> values)
        {
            var store = new FactorStore();
            var alphaIds = new[]
            {
                "alpha001", "alpha006", "alpha030", "alpha040",
                "alpha042", "alpha055", "alpha058", "alpha101",
            };
            foreach (var aid in alphaIds)
            {
                var localAid = aid;
                store.Register(aid, new RParquetAdapter(
                    factorRoot: $"factor-zoo/{aid}", valueColumn: aid,
                    readScalar: (path, column) =>
                    {
                        // path = .../factor-zoo/alphaNNN/<date>/<ts_code>.parquet
                        var fileName = System.IO.Path.GetFileNameWithoutExtension(path);
                        var ticker = fileName.Split('.')[0];
                        if (values.TryGetValue((localAid, ticker), out var v)) return v;
                        return (decimal?)null;
                    }));
            }
            return store;
        }

        private static QCAlgorithm MakeAlgorithmWithSecurities(DateTime time)
        {
            // AlgorithmStub wires up a DataManagerStub + UniverseManager + Securities so
            // AddEquity actually populates the user-defined universe and ActiveSecurities
            // returns them (this is the canonical minimal-algorithm setup used across the
            // test harness; mirror Tests/Algorithm/AlgorithmAddDataTests.cs).
            // Pass MockDataFeed so OnEndOfTimeStep's subscription registration doesn't
            // hit NullDataFeed.CreateSubscription's NotImplementedException.
            var alg = new AlgorithmStub(new MockDataFeed());
            alg.SetStartDate(2026, 1, 1);
            alg.SetEndDate(2026, 12, 31);
            alg.SetDateTime(time);

            // Build symbols up-front so we can manually attach them to the
            // UserDefinedUniverse's Securities (Universe.Members) — AddEquity's
            // _pendingUserDefinedUniverseSecurityChanges path only seeds the
            // Members dictionary after ApplyUniverseSelection runs in the real
            // data feed, which a test-bench AlgorithmStub never invokes. Without
            // this, ActiveSecurities (which derives from Universe.Members)
            // returns 0 entries and Update yields nothing.
            var universeSymbol = UserDefinedUniverse.CreateSymbol(SecurityType.Equity, Market.SSE);
            var szUniverseSymbol = UserDefinedUniverse.CreateSymbol(SecurityType.Equity, Market.SZSE);
            var sseUniverse = new UserDefinedUniverse(
                new SubscriptionDataConfig(typeof(TradeBar), universeSymbol, Resolution.Daily,
                    TimeZones.Shanghai, TimeZones.Shanghai, false, false, true),
                new UniverseSettings(Resolution.Daily, 1, false, false, TimeSpan.Zero),
                Time.MaxTimeSpan, new List<Symbol>());
            var szseUniverse = new UserDefinedUniverse(
                new SubscriptionDataConfig(typeof(TradeBar), szUniverseSymbol, Resolution.Daily,
                    TimeZones.Shanghai, TimeZones.Shanghai, false, false, true),
                new UniverseSettings(Resolution.Daily, 1, false, false, TimeSpan.Zero),
                Time.MaxTimeSpan, new List<Symbol>());
            alg.UniverseManager.Add(universeSymbol, sseUniverse);
            alg.UniverseManager.Add(szUniverseSymbol, szseUniverse);

            foreach (var ticker in Tickers)
            {
                var market = ticker.StartsWith("6") ? Market.SSE : Market.SZSE;
                var symbol = Symbol.Create(ticker, SecurityType.Equity, market);
                alg.AddEquity(ticker, Resolution.Daily, market);
                // Manually add as a universe Member so ActiveSecurities returns it.
                var universe = market == Market.SSE ? sseUniverse : szseUniverse;
                universe.AddMember(time, alg.Securities[symbol], false);
            }

            // OnEndOfTimeStep flushes pending subscription-data-config additions
            // (needed for SubscriptionManager state consistency).
            alg.OnEndOfTimeStep();
            return alg;
        }

        [Test]
        public void Update_WithFakeReader_ProducesTopQuantileInsights()
        {
            // Each (alpha, ticker) returns a distinct per-ticker value so the
            // cross-sectional z-score is well-defined (std > 0) for every alpha.
            // With 6 of 8 alphas having sign -1, the lowest raw value yields the
            // highest signed z; 2 of 8 with sign +1 are not enough to flip it.
            // Per-ticker raw values are the same across alphas (sufficient for a
            // deterministic top selection — composite(600519) ≈ +0.707,
            // composite(300750) ≈ -0.707, so 600519 is the unique top).
            var vals = new Dictionary<(string, string), decimal>();
            var perTicker = new Dictionary<string, decimal>
            {
                {"600519", 0.10m}, {"600036", 0.20m}, {"000001", 0.30m},
                {"000002", 0.40m}, {"300750", 0.50m},
            };
            var alphaIds = new[]
            {
                "alpha001", "alpha006", "alpha030", "alpha040",
                "alpha042", "alpha055", "alpha058", "alpha101",
            };
            foreach (var aid in alphaIds)
            {
                foreach (var kv in perTicker)
                {
                    vals[(aid, kv.Key)] = kv.Value;
                }
            }
            var store = MakeFakeStore(vals);

            // topQuantile=0.20 over 5 securities => floor(5*0.2)=1 selected Insight.
            // minValidPerAlpha=2 (test override) so the 5-symbol universe still
            // produces z-scores; production default is 10 (spec §5.1).
            var model = new Alpha101CompositeAlphaModel(
                rebalanceMonths: 1,
                insightPeriodDays: 21,
                topQuantile: 0.20m,
                store: store,
                minValidPerAlpha: 2);

            // 2026-01-05 is a Monday — a real trading day for SSE/SZSE.
            var asOf = new DateTime(2026, 1, 5, 9, 30, 0);
            var alg = MakeAlgorithmWithSecurities(asOf);
            var slice = new Slice(asOf, new List<BaseData>(), asOf);

            var insights = model.Update(alg, slice).ToList();

            Assert.AreEqual(1, insights.Count, "expected exactly 1 top insight");
            Assert.AreEqual(InsightDirection.Up, insights[0].Direction);
            // 600519 has the lowest raw value → highest composite (6/8 alphas sign -1).
            Assert.AreEqual("600519", insights[0].Symbol.ID.Symbol,
                "lowest-raw-value symbol wins when 6/8 alphas have sign -1");
        }

        [Test]
        public void Update_AllMissing_HoldsNotThrows()
        {
            // Default FactorStore (no fake overrides): the 8 alpha ids route to real
            // RParquetAdapters whose DefaultPythonNetReader returns null on missing
            // parquet → Quality=Missing for every (alpha, symbol) pair. No real
            // factor-zoo date dir for 1900-01-01, so the model must hold (0 insights,
            // no throw). Use an early date to guarantee no on-disk factor-zoo dir.
            var model = new Alpha101CompositeAlphaModel(rebalanceMonths: 1);

            var asOf = new DateTime(1900, 1, 2, 9, 30, 0);
            var alg = MakeAlgorithmWithSecurities(asOf);
            var slice = new Slice(asOf, new List<BaseData>(), asOf);

            Assert.DoesNotThrow(() => model.Update(alg, slice));
            var insights = model.Update(alg, slice).ToList();
            Assert.AreEqual(0, insights.Count, "no insights when all alphas are Missing");
        }
    }
}
