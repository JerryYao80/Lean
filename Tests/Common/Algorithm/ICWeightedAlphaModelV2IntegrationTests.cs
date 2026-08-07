/*
 * Integration test for ICWeightedAlphaModelV2 — proves the full Python→C# link:
 * real IC report JSON (from export_ic_reports.py) + injected fake FactorStore
 * → ComputeAlphaScores → selected=N insights. Isolates the V2 chain from the
 * slow pythonnet parquet reads (which make the live backtest intractable in CI).
 *
 * Mirrors the AlgorithmStub + MockDataFeed setup from
 * Alpha101CompositeAlphaModelTests so ActiveSecurities returns real symbols.
 */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Securities;
using QuantConnect.Tests.Engine.DataFeeds;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture, Category("RequiresRealICReports")]
    public class ICWeightedAlphaModelV2IntegrationTests
    {
        private const string RealIcDir = "/home/project/hope/Lean/result/ic-reports";
        private static readonly string[] Tickers =
            {"600519", "600036", "000001", "000002", "300750"};

        /// <summary>Adapter returning a per-ticker deterministic value so
        /// ComputeAlphaScores produces non-zero composite scores without
        /// touching pythonnet/pandas (keeps the test fast + deterministic).</summary>
        private class TickerValueAdapter : IFactorAdapter
        {
            private readonly Dictionary<string, decimal> _perTicker;
            public TickerValueAdapter(Dictionary<string, decimal> perTicker)
            {
                _perTicker = perTicker;
            }
            public bool TryGet(Symbol symbol, DateTime date,
                IEnumerable<BaseData> history, out FactorResult result)
            {
                var ticker = symbol.ID.Symbol;
                decimal v = _perTicker.TryGetValue(ticker, out var d) ? d : 0m;
                result = new FactorResult
                {
                    Value = v, RawValue = v, Time = date,
                    Symbol = symbol, FactorId = "fake", Quality = FactorDataQuality.Valid
                };
                return true;
            }
        }

        private static QCAlgorithm MakeAlgorithmWithSecurities(DateTime time)
        {
            var alg = new AlgorithmStub(new MockDataFeed());
            alg.SetStartDate(2024, 1, 1);
            alg.SetEndDate(2024, 12, 31);
            alg.SetDateTime(time);

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
                var universe = market == Market.SSE ? sseUniverse : szseUniverse;
                universe.AddMember(time, alg.Securities[symbol], false);
            }
            alg.OnEndOfTimeStep();
            return alg;
        }

        [Test]
        public void Update_RealIcReportJson_FakeStore_ProducesSelectedInsights()
        {
            if (!Directory.Exists(RealIcDir) ||
                Directory.GetFiles(RealIcDir, "ic_report_*.json").Length == 0)
            {
                Assert.Ignore("No IC reports at " + RealIcDir);
                return;
            }

            // Verify the real JSON: latest report <= 2024-06-30 loads + weights sum to 1.
            var loader = new ICWeightedAlphaModelV2(RealIcDir);
            var report = loader.LoadLatestReport(new DateTime(2024, 6, 30));
            Assert.IsNotNull(report, "expected at least one IC report <= 2024-06-30");
            Assert.GreaterOrEqual(report.Factors.Count, 1, "report should have >=1 factor");
            double wsum = report.Factors.Sum(f => f.Weight);
            Assert.AreEqual(1.0, wsum, 1e-6, "factor weights should sum to 1.0");
            Console.WriteLine($"Loaded report {report.ReportDate}: {report.Factors.Count} factors, wsum={wsum}");

            // Fake store: every factor in the report returns a per-ticker value.
            var perTicker = new Dictionary<string, decimal>
            {
                {"600519", 0.10m}, {"600036", 0.20m}, {"000001", 0.30m},
                {"000002", 0.40m}, {"300750", 0.50m},
            };
            var store = new FactorStore();
            foreach (var f in report.Factors)
            {
                store.Register(f.FactorId, new TickerValueAdapter(perTicker));
            }

            var model = new ICWeightedAlphaModelV2(
                icReportDir: RealIcDir,
                rebalanceMonths: 1,
                insightPeriodDays: 21,
                topQuantile: 0.20m,
                store: store);

            // 2024-02-01: after the 2024-01-31 report, so LoadLatestReport picks it.
            var asOf = new DateTime(2024, 2, 1, 15, 0, 0);
            var alg = MakeAlgorithmWithSecurities(asOf);
            var slice = new Slice(asOf, new List<BaseData>(), asOf);

            var insights = model.Update(alg, slice).ToList();

            // topQuantile=0.20 over 5 securities => floor(5*0.20)=1 selected.
            Assert.GreaterOrEqual(insights.Count, 1, "expected >=1 selected insight");
            Assert.AreEqual(InsightDirection.Up, insights[0].Direction);
            Assert.Greater(insights[0].Weight, 0, "weight should be positive");
            Console.WriteLine($"Integration OK: {insights.Count} insights, " +
                              $"first={insights[0].Symbol.ID.Symbol} weight={insights[0].Weight}");
        }
    }
}
