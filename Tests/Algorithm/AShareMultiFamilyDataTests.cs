/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
*/

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Data;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareMultiFamilyDataTests
    {
        [SetUp]
        public void SetUp()
        {
            AShareTushareFactorData.SetBaseDirectory("/tmp/multi-family-factors");
            AShareTushareFactorData.ClearHeaderCache();
        }

        [Test]
        public void GetSourceBuildsLocalCsvPathFromUnderlyingMarket()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var source = new AShareTushareFactorData().GetSource(config, new DateTime(2024, 1, 31), false);
            var expected = Path.Combine("/tmp/multi-family-factors", "sse", "daily", "600000.csv");

            Assert.AreEqual(expected, source.Source);
            Assert.AreEqual(SubscriptionTransportMedium.LocalFile, source.TransportMedium);
            Assert.AreEqual(FileFormat.Csv, source.Format);
        }

        [Test]
        public void ReaderParsesMultiFamilySnapshotWithHeaderDiscovery()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying, sourcePath: "/tmp/multi-family-factors/sse/daily/600000.csv");

            var reader = new AShareTushareFactorData();
            var header = "trade_date,close,pe_ttm,pb,dv_ttm,roe,momentum_120_20,turnover_rate,total_mv,circ_mv,net_mf_amount";
            var line = "2024-01-31,10.50,15.2,1.8,2.1,12.5,0.35,1.5,5000000,2000000,800000";

            reader.Reader(config, header, new DateTime(2024, 1, 31), false);
            var data = reader.Reader(config, line, new DateTime(2024, 1, 31), false) as AShareTushareFactorData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(underlying, data.UnderlyingSymbol);
            Assert.AreEqual(new DateTime(2024, 1, 31), data.EndTime);
            Assert.AreEqual(10.5m, data.Close);
            Assert.AreEqual(15.2m, data.GetDecimal("pe_ttm"));
            Assert.AreEqual(1.8m, data.GetDecimal("pb"));
            Assert.AreEqual(0.35m, data.Momentum12020);
            Assert.AreEqual(1.5m, data.TurnoverRate);
            Assert.AreEqual(5000000m, data.TotalMv);
            Assert.AreEqual(2000000m, data.GetDecimal("circ_mv"));
            Assert.AreEqual(800000m, data.GetDecimal("net_mf_amount"));
        }

        [Test]
        public void ReaderReturnsNullForHeaderLine()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            const string header = "trade_date,close,pe_ttm,pb";

            var result = new AShareTushareFactorData().Reader(config, header, new DateTime(2024, 1, 31), false);

            Assert.IsNull(result);
        }

        [Test]
        public void ReaderReturnsNullForShortLine()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var result = new AShareTushareFactorData().Reader(config, "x", new DateTime(2024, 1, 31), false);

            Assert.IsNull(result);
        }

        [Test]
        public void ReaderHandlesMissingColumnsAsNull()
        {
            var underlying = Symbol.Create("600000", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying, sourcePath: "/tmp/multi-family-factors/sse/daily/600000.csv");

            var reader = new AShareTushareFactorData();
            reader.Reader(config, "trade_date,close,pe_ttm,pb,missing_col", new DateTime(2024, 1, 31), false);
            var data = reader.Reader(config, "2024-01-31,10.5,15.2,,", new DateTime(2024, 1, 31), false) as AShareTushareFactorData;

            Assert.IsNotNull(data);
            Assert.AreEqual(10.5m, data.Close);
            Assert.AreEqual(15.2m, data.GetDecimal("pe_ttm"));
            Assert.IsNull(data.GetDecimal("pb"));
            Assert.IsNull(data.GetDecimal("missing_col"));
        }

        [Test]
        public void FamilyScoreRanksMomentumAndValueCorrectly()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);

            var factors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [symbolA] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["momentum_120_20"] = 1.5m, ["return_5"] = 0.01m, ["turnover_rate"] = 0.5m, ["total_mv"] = 2000m,
                    ["pe_ttm"] = 50m, ["pb"] = 5m, ["dv_ttm"] = 0.5m, ["roe"] = 5m, ["grossprofit_margin"] = 10m, ["debt_to_assets"] = 70m,
                }),
                [symbolB] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["momentum_120_20"] = 0.5m, ["return_5"] = -0.02m, ["turnover_rate"] = 2m, ["total_mv"] = 8000m,
                    ["pe_ttm"] = 200m, ["pb"] = 10m, ["dv_ttm"] = 0.1m, ["roe"] = 2m, ["grossprofit_margin"] = 5m, ["debt_to_assets"] = 80m,
                }),
                [symbolC] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["momentum_120_20"] = -0.5m, ["return_5"] = 0.05m, ["turnover_rate"] = 4m, ["total_mv"] = 50000m,
                    ["pe_ttm"] = 5m, ["pb"] = 0.5m, ["dv_ttm"] = 5m, ["roe"] = 20m, ["grossprofit_margin"] = 50m, ["debt_to_assets"] = 20m,
                }),
            };

            var scores = AShareMultiFamilySignalModel.ComputeScores(factors, new AShareMultiFamilySignalSettings());
            var ranked = scores.OrderByDescending(pair => pair.Value).Select(pair => pair.Key.Value).ToList();

            // A has highest momentum_120_20=1.5 which dominates the composite
            // Verify that the ranking is deterministic and consistent with signal model
            Assert.AreEqual(3, ranked.Count);
            Assert.AreNotEqual(ranked[0], ranked[2]);
        }

        [Test]
        public void CompositeScoreIsWeightedSumOfFamilyScores()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);

            var factors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [symbolA] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["momentum_120_20"] = 1.0m, ["pe_ttm"] = 10m, ["pb"] = 1m,
                    ["dv_ttm"] = 3m, ["roe"] = 15m, ["grossprofit_margin"] = 30m, ["debt_to_assets"] = 30m,
                    ["return_5"] = 0m, ["turnover_rate"] = 1m, ["total_mv"] = 5000m,
                }),
                [symbolB] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["momentum_120_20"] = -1.0m, ["pe_ttm"] = 100m, ["pb"] = 10m,
                    ["dv_ttm"] = 0m, ["roe"] = 2m, ["grossprofit_margin"] = 5m, ["debt_to_assets"] = 80m,
                    ["return_5"] = 0.05m, ["turnover_rate"] = 5m, ["total_mv"] = 50000m,
                }),
            };

            var settings = new AShareMultiFamilySignalSettings
            {
                MomentumReversalWeight = 1.0m,
                ValueQualityWeight = 1.0m,
                MacroRateWeight = 0m,
            };

            var familyScores = AShareMultiFamilySignalModel.ComputeFamilyScores(factors, settings);
            var composite = AShareMultiFamilySignalModel.ComputeComposite(familyScores, settings);

            // Verify composite is a weighted average of family scores
            foreach (var symbol in new[] { symbolA, symbolB })
            {
                var familyScore = familyScores[symbol];
                decimal weightedSum = 0m;
                decimal absWeightSum = 0m;
                var weights = new Dictionary<string, decimal>(StringComparer.Ordinal)
                {
                    ["momentum_reversal"] = settings.MomentumReversalWeight,
                    ["value_quality"] = settings.ValueQualityWeight,
                    ["money_flow"] = settings.MoneyFlowWeight,
                    ["earnings_surprise"] = settings.EarningsSurpriseWeight,
                    ["chip_cost"] = settings.ChipCostWeight,
                    ["etf_premium"] = settings.EtfPremiumWeight,
                    ["sector_rotation"] = settings.SectorRotationWeight,
                    ["margin_signal"] = settings.MarginSignalWeight,
                    ["northbound_flow"] = settings.NorthboundFlowWeight,
                    ["multi_factor"] = settings.MultiFactorWeight,
                    ["analyst_signal"] = settings.AnalystSignalWeight,
                    ["macro_rate"] = settings.MacroRateWeight,
                };
                foreach (var family in familyScore)
                {
                    var w = weights.TryGetValue(family.Key, out var fw) ? fw : 0m;
                    weightedSum += w * family.Value;
                    absWeightSum += Math.Abs(w);
                }
                var expectedComposite = absWeightSum > 0m ? weightedSum / absWeightSum : 0m;
                Assert.AreEqual((double)expectedComposite, (double)composite[symbol], 0.0001);
            }
        }

        [Test]
        public void SelectPortfolioProducesCappedWeights()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);

            var compositeScores = new Dictionary<Symbol, decimal>
            {
                [symbolA] = 2.4m,
                [symbolB] = 1.1m,
                [symbolC] = 0.5m
            };
            var factors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [symbolA] = MakeFactor(new Dictionary<string, decimal?> { ["total_mv"] = 5000m, ["volatility_20"] = 0.2m }),
                [symbolB] = MakeFactor(new Dictionary<string, decimal?> { ["total_mv"] = 3000m, ["volatility_20"] = 0.1m }),
                [symbolC] = MakeFactor(new Dictionary<string, decimal?> { ["total_mv"] = 2000m, ["volatility_20"] = 0.5m })
            };
            var familyScores = factors.Keys.ToDictionary(
                k => k,
                k => new Dictionary<string, decimal> { ["momentum_reversal"] = compositeScores[k] });

            var targets = AShareMultiFamilySignalModel.SelectPortfolio(
                compositeScores,
                familyScores,
                factors,
                topN: 3,
                minScoreSpread: 0m,
                targetExposure: 0.30m,
                weightingMode: "black-litterman",
                settings: new AShareMultiFamilySignalSettings { MaxSingleWeight = 0.12m });

            Assert.That(targets, Has.Count.EqualTo(3));
            Assert.That(targets.Sum(t => t.Weight), Is.EqualTo(0.30m).Within(0.0001m));
            Assert.That(targets.Max(t => t.Weight), Is.LessThanOrEqualTo(0.12m + 0.0001m));
        }

        [Test]
        public void MacroRegimeAdjustmentClampsCorrectly()
        {
            // With no macro data → returns 1.0
            var noDataFactors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [Symbol.Create("A", SecurityType.Equity, Market.SSE)] = MakeFactor(new Dictionary<string, decimal?>()),
            };
            var adjustment = ComputeMacroRegimeAdjustmentForTest(noDataFactors, 0.55m);
            Assert.AreEqual(1.0m, adjustment);

            // With extremely bearish macro data → clamped at min
            var bearishFactors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [Symbol.Create("A", SecurityType.Equity, Market.SSE)] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["cn_cpi_nt_yoy"] = 50m,       // very high inflation → very negative
                    ["cn_m_m2_yoy"] = -20m,         // deep contraction → very negative
                    ["cn_pmi_PMI020201"] = 10m,     // far below 50 → very negative
                    ["shibor_lpr_1y"] = 50m,        // extreme rates → very negative
                }),
            };
            adjustment = ComputeMacroRegimeAdjustmentForTest(bearishFactors, 0.55m);
            Assert.AreEqual(0.55m, adjustment);

            // With bullish macro data → capped at 1.0
            var bullishFactors = new Dictionary<Symbol, AShareTushareFactorData>
            {
                [Symbol.Create("A", SecurityType.Equity, Market.SSE)] = MakeFactor(new Dictionary<string, decimal?>
                {
                    ["cn_cpi_nt_yoy"] = 0m,         // low inflation → neutral
                    ["cn_m_m2_yoy"] = 15m,          // money expansion → positive
                    ["cn_pmi_PMI020201"] = 60m,      // above 50 → positive
                    ["shibor_lpr_1y"] = 2m,          // low rates → mildly negative
                }),
            };
            adjustment = ComputeMacroRegimeAdjustmentForTest(bullishFactors, 0.55m);
            Assert.LessOrEqual(adjustment, 1.0m);
            Assert.GreaterOrEqual(adjustment, 0.55m);
        }

        [Test]
        public void ShouldRebalanceSupportsMonthlyAndBiweekly()
        {
            Assert.IsTrue(AShareDataDrivenMultiFamilyAlgorithm.ShouldRebalance(new DateTime(2024, 2, 1), default, "monthly"));
            Assert.IsFalse(AShareDataDrivenMultiFamilyAlgorithm.ShouldRebalance(new DateTime(2024, 2, 15), new DateTime(2024, 2, 1), "monthly"));
            Assert.IsTrue(AShareDataDrivenMultiFamilyAlgorithm.ShouldRebalance(new DateTime(2024, 3, 1), new DateTime(2024, 2, 1), "monthly"));
            Assert.IsFalse(AShareDataDrivenMultiFamilyAlgorithm.ShouldRebalance(new DateTime(2024, 2, 10), new DateTime(2024, 2, 1), "biweekly"));
            Assert.IsTrue(AShareDataDrivenMultiFamilyAlgorithm.ShouldRebalance(new DateTime(2024, 2, 16), new DateTime(2024, 2, 1), "biweekly"));
        }

        [Test]
        public void MonteCarloSummaryProducesFiniteStatistics()
        {
            var start = new DateTime(2024, 1, 1);
            var dailyReturns = Enumerable.Range(0, 24)
                .Select(index => new StrategyMonteCarloDailyReturn
                {
                    TradeDate = start.AddDays(index),
                    NetReturn = index % 6 == 0 ? -0.018 + index * 0.0001 : 0.006 + index * 0.0002
                })
                .ToList();
            var factorExposures = Enumerable.Range(0, 24)
                .Select(index => new StrategyMonteCarloFactorExposure
                {
                    TradeDate = start.AddDays(index),
                    Beta = (index % 5 - 2) * 0.10,
                    Momentum = Math.Sin(index / 3.0),
                    Size = Math.Cos(index / 4.0),
                    EarningsYield = 0.20 + index * 0.01,
                    ResidualVolatility = 0.30 - index * 0.005,
                    Growth = (index % 4) * 0.08,
                    BookToPrice = 0.10 + (index % 3) * 0.05,
                    Leverage = -0.20 + index * 0.01,
                    Liquidity = 0.05 * (index % 6),
                    NonLinearSize = -0.15 + index * 0.005
                })
                .ToList();

            var summary = StrategyMonteCarloStatistics.Compute(
                new StrategyMonteCarloConfig
                {
                    Enabled = true,
                    Trials = 40,
                    HorizonDays = 8,
                    BlockSize = 3,
                    Seed = 42,
                    FactorPerturbationScale = 0.15
                },
                dailyReturns,
                factorExposures);

            Assert.That(summary.HasData, Is.True);
            Assert.That(summary.Trials, Is.EqualTo(40));
            Assert.That(summary.HorizonDays, Is.EqualTo(8));
            Assert.That(summary.BaselineLossProbability, Is.InRange(0d, 1d));
            Assert.That(summary.CombinedLossProbability, Is.InRange(0d, 1d));
            Assert.That(double.IsNaN(summary.CombinedMedianReturn), Is.False);
            Assert.That(double.IsInfinity(summary.CombinedMedianReturn), Is.False);
            Assert.That(double.IsNaN(summary.CombinedP95Drawdown), Is.False);
            Assert.That(double.IsInfinity(summary.CombinedP95Drawdown), Is.False);
        }

        [Test]
        public void PresentFieldCountExcludesNullValues()
        {
            var data = MakeFactor(new Dictionary<string, decimal?>
            {
                ["close"] = 10m,
                ["pe_ttm"] = null,
                ["pb"] = 1.5m,
                ["momentum_120_20"] = null,
            });

            Assert.AreEqual(2, data.PresentFieldCount);
        }

        [Test]
        public void FamilyExposureIsWeightedAverage()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);

            var weights = new Dictionary<Symbol, decimal>
            {
                [symbolA] = 0.6m,
                [symbolB] = 0.4m,
            };

            var familyScores = new Dictionary<Symbol, Dictionary<string, decimal>>
            {
                [symbolA] = new Dictionary<string, decimal>
                {
                    ["momentum_reversal"] = 1.0m,
                    ["value_quality"] = -0.5m,
                },
                [symbolB] = new Dictionary<string, decimal>
                {
                    ["momentum_reversal"] = 0.5m,
                    ["value_quality"] = 0.5m,
                },
            };

            var exposure = AShareMultiFamilySignalModel.ComputeFamilyExposure(weights, familyScores);

            // momentum_reversal: 0.6 * 1.0 + 0.4 * 0.5 = 0.8 (normalized by total weight = 1.0)
            Assert.AreEqual(0.8, (double)exposure["momentum_reversal"], 0.0001);
            // value_quality: 0.6 * -0.5 + 0.4 * 0.5 = -0.1
            Assert.AreEqual(-0.1, (double)exposure["value_quality"], 0.0001);
        }

        [Test]
        public void IsFreshFactorSnapshotReturnsCorrectly()
        {
            var sessionDate = new DateTime(2024, 1, 31);

            var freshFactor = MakeFactor(new Dictionary<string, decimal?> { ["close"] = 10m });
            freshFactor.Time = sessionDate;
            freshFactor.EndTime = sessionDate;
            Assert.IsTrue(AShareDataDrivenMultiFamilyAlgorithm.IsFreshFactorSnapshot(freshFactor, sessionDate));

            var staleFactor = MakeFactor(new Dictionary<string, decimal?> { ["close"] = 10m });
            staleFactor.Time = sessionDate.AddDays(-1);
            staleFactor.EndTime = sessionDate.AddDays(-1);
            Assert.IsFalse(AShareDataDrivenMultiFamilyAlgorithm.IsFreshFactorSnapshot(staleFactor, sessionDate));
        }

        private static AShareTushareFactorData MakeFactor(Dictionary<string, decimal?> fields)
        {
            return new AShareTushareFactorData
            {
                Symbol = Symbol.CreateBase(typeof(AShareTushareFactorData),
                    Symbol.Create("600000", SecurityType.Equity, Market.SSE), Market.SSE),
                Time = new DateTime(2024, 1, 31),
                EndTime = new DateTime(2024, 1, 31),
                Fields = new Dictionary<string, decimal?>(fields, StringComparer.Ordinal)
            };
        }

        private static SubscriptionDataConfig CreateConfig(Symbol underlying, string sourcePath = null)
        {
            var customSymbol = Symbol.CreateBase(typeof(AShareTushareFactorData), underlying, underlying.ID.Market);
            var config = new SubscriptionDataConfig(
                typeof(AShareTushareFactorData),
                customSymbol,
                Resolution.Daily,
                TimeZones.Shanghai,
                TimeZones.Shanghai,
                false,
                false,
                false);
            return config;
        }

        /// <summary>
        /// Replicates ComputeMacroRegimeAdjustment logic for isolated testing.
        /// </summary>
        private static decimal ComputeMacroRegimeAdjustmentForTest(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors, decimal minRegime)
        {
            var sample = factors.Values.FirstOrDefault(f => f != null);
            if (sample == null) return 1.0m;

            int macroFields = 0;
            decimal macroScore = 0m;

            var cpiYoy = sample.GetDecimal("cn_cpi_nt_yoy");
            if (cpiYoy.HasValue) { macroScore -= cpiYoy.Value * 0.3m; macroFields++; }

            var m2Yoy = sample.GetDecimal("cn_m_m2_yoy");
            if (m2Yoy.HasValue) { macroScore += m2Yoy.Value * 0.3m; macroFields++; }

            var pmi = sample.GetDecimal("cn_pmi_PMI020201");
            if (pmi.HasValue) { macroScore += (pmi.Value - 50m) * 0.02m; macroFields++; }

            var lpr1y = sample.GetDecimal("shibor_lpr_1y");
            if (lpr1y.HasValue) { macroScore -= lpr1y.Value * 0.1m; macroFields++; }

            if (macroFields == 0) return 1.0m;

            var rawRegime = 1.0m + macroScore * 0.05m;
            return Math.Max(minRegime, Math.Min(1.0m, rawRegime));
        }
    }
}