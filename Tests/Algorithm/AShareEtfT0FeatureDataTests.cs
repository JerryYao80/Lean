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
using NodaTime;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Data;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareEtfT0FeatureDataTests
    {
        [SetUp]
        public void SetUp()
        {
            AShareEtfT0FeatureData.SetBaseDirectory("/tmp/ashare-etf-t0-features");
        }

        [Test]
        public void GetSourceBuildsLocalCsvPathFromUnderlyingMarket()
        {
            var underlying = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);

            var source = new AShareEtfT0FeatureData().GetSource(config, new DateTime(2024, 1, 22), false);
            var expected = Path.Combine("/tmp/ashare-etf-t0-features", "sse", "daily", "510300.csv");

            Assert.AreEqual(expected, source.Source);
            Assert.AreEqual(SubscriptionTransportMedium.LocalFile, source.TransportMedium);
            Assert.AreEqual(FileFormat.Csv, source.Format);
        }

        [Test]
        public void ReaderParsesFeatureSnapshotAndKeepsUnderlyingSymbol()
        {
            var underlying = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
            var config = CreateConfig(underlying);
            const string line = "20240122,1.000000,1.010000,1.030000,0.990000,1.020000,1.200000,1000000,500000,0.009901,0.010000,0.750000,0.040000,0.030000,0.080000,0.012000,1200000,0.010000,1.015000,1.016000,1500000,2500000,0.004926,1.250000,0.020000,0.030000,0.002000,0.001500,0.002500,0.015000,0.008000,0.006500,0.003000,-0.050000,-0.020000,1100000,0.650000,0.015000,0.008000,-0.003000,-0.400000,0.012000,0.020000,-0.001000,-0.002000,0.004000,-0.010000";

            var data = new AShareEtfT0FeatureData().Reader(config, line, new DateTime(2024, 1, 22), false) as AShareEtfT0FeatureData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(underlying, data.UnderlyingSymbol);
            Assert.AreEqual(new DateTime(2024, 1, 22), data.Time);
            Assert.AreEqual(1.02m, data.Close);
            Assert.AreEqual(0.75m, data.CloseLocation);
            Assert.AreEqual(-0.05m, data.SignalMomentum20);
            Assert.AreEqual(1100000m, data.SignalLiquidity5);
            Assert.AreEqual(1.015m, data.UnitNav);
            Assert.AreEqual(0.004926m, data.NavPremium1);
            Assert.AreEqual(-0.003m, data.SignalNavPremium1);
            Assert.AreEqual(-0.01m, data.SignalIndexMomentum5);
        }

        [Test]
        public void SignalModelRanksSymbolsUsingPythonWeights()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);

            var scores = AShareEtfT0FeatureSignalModel.ComputeScores(new Dictionary<Symbol, AShareEtfT0FeatureData>
            {
                [symbolA] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 0.10m,
                    SignalMomentum5 = 0.06m,
                    SignalLiquidity5 = 100m,
                    SignalCloseLocation = 0.90m,
                    SignalVolatility10 = 0.05m,
                    SignalGapAbs = 0.01m,
                },
                [symbolB] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 0.03m,
                    SignalMomentum5 = 0.01m,
                    SignalLiquidity5 = 80m,
                    SignalCloseLocation = 0.60m,
                    SignalVolatility10 = 0.10m,
                    SignalGapAbs = 0.03m,
                },
                [symbolC] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = -0.01m,
                    SignalMomentum5 = -0.03m,
                    SignalLiquidity5 = 70m,
                    SignalCloseLocation = 0.20m,
                    SignalVolatility10 = 0.15m,
                    SignalGapAbs = 0.05m,
                },
            });

            var ranked = scores
                .OrderByDescending(pair => pair.Value)
                .Select(pair => pair.Key.Value)
                .ToList();

            CollectionAssert.AreEqual(new[] { "C", "B", "A" }, ranked);
        }


        [Test]
        public void SignalModelCanDisableConditionalNavPremiumZ20OverlayInHighRiskBucket()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);
            var features = new Dictionary<Symbol, AShareEtfT0FeatureData>
            {
                [symbolA] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 0.01m,
                    SignalMomentum5 = -0.02m,
                    SignalLiquidity5 = 100m,
                    SignalCloseLocation = 0.50m,
                    SignalVolatility10 = 1.60m,
                    SignalGapAbs = 0.01m,
                    SignalNavPremiumZ20 = -1.0m,
                },
                [symbolB] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 0.01m,
                    SignalMomentum5 = -0.02m,
                    SignalLiquidity5 = 100m,
                    SignalCloseLocation = 0.50m,
                    SignalVolatility10 = 1.60m,
                    SignalGapAbs = 0.01m,
                    SignalNavPremiumZ20 = 0.0m,
                },
                [symbolC] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 0.01m,
                    SignalMomentum5 = -0.02m,
                    SignalLiquidity5 = 100m,
                    SignalCloseLocation = 0.50m,
                    SignalVolatility10 = 1.60m,
                    SignalGapAbs = 0.01m,
                    SignalNavPremiumZ20 = 1.0m,
                },
            };

            var scores = AShareEtfT0FeatureSignalModel.ComputeScores(
                features,
                new AShareEtfT0FeatureSignalSettings
                {
                    NavPremiumZ20Weight = -0.2m,
                    NavPremiumZ20Orthogonalize = false,
                    NavPremiumZ20NormalScale = 1.0m,
                    NavPremiumZ20MediumScale = 0.5m,
                    NavPremiumZ20HighScale = 0.0m
                },
                "high");

            Assert.That(scores.Values.All(score => Math.Abs(score) <= 1e-10m), Is.True);
        }

        [Test]
        public void SignalModelOrthogonalizedNavPremiumZ20LeavesBaseScoresUnchangedWhenPerfectlyCollinear()
        {
            var symbolA = Symbol.Create("A", SecurityType.Equity, Market.SSE);
            var symbolB = Symbol.Create("B", SecurityType.Equity, Market.SSE);
            var symbolC = Symbol.Create("C", SecurityType.Equity, Market.SSE);
            var features = new Dictionary<Symbol, AShareEtfT0FeatureData>
            {
                [symbolA] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 3.0m,
                    SignalMomentum5 = 3.0m,
                    SignalLiquidity5 = 3.0m,
                    SignalCloseLocation = 3.0m,
                    SignalVolatility10 = 3.0m,
                    SignalGapAbs = 3.0m,
                    SignalNavPremiumZ20 = 3.0m,
                },
                [symbolB] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 2.0m,
                    SignalMomentum5 = 2.0m,
                    SignalLiquidity5 = 2.0m,
                    SignalCloseLocation = 2.0m,
                    SignalVolatility10 = 2.0m,
                    SignalGapAbs = 2.0m,
                    SignalNavPremiumZ20 = 2.0m,
                },
                [symbolC] = new AShareEtfT0FeatureData
                {
                    SignalMomentum20 = 1.0m,
                    SignalMomentum5 = 1.0m,
                    SignalLiquidity5 = 1.0m,
                    SignalCloseLocation = 1.0m,
                    SignalVolatility10 = 1.0m,
                    SignalGapAbs = 1.0m,
                    SignalNavPremiumZ20 = 1.0m,
                },
            };

            var baseScores = AShareEtfT0FeatureSignalModel.ComputeScores(features);
            var overlayScores = AShareEtfT0FeatureSignalModel.ComputeScores(
                features,
                new AShareEtfT0FeatureSignalSettings
                {
                    NavPremiumZ20Weight = -0.2m,
                    NavPremiumZ20Orthogonalize = true,
                    NavPremiumZ20NormalScale = 1.0m,
                    NavPremiumZ20MediumScale = 1.0m,
                    NavPremiumZ20HighScale = 1.0m
                },
                "normal");

            foreach (var symbol in baseScores.Keys)
            {
                Assert.That(Math.Abs(baseScores[symbol] - overlayScores[symbol]) <= 1e-10m, Is.True, symbol.Value);
            }
        }

        [Test]
        public void VolTargetScaleReturnsOneWithoutEnoughHistory()
        {
            var scale = AShareEtfT0FeatureIntradayAlgorithm.ComputePortfolioVolTargetScale(
                new List<decimal> { 0.01m },
                0.012m,
                40,
                2,
                0.5m,
                1.0m);

            Assert.AreEqual(1.0m, scale);
        }

        [Test]
        public void FreshFeatureSnapshotRequiresMatchingSessionDate()
        {
            var feature = new AShareEtfT0FeatureData
            {
                EndTime = new DateTime(2024, 2, 7),
                SignalMomentum20 = 0.1m,
                SignalMomentum5 = 0.2m,
                SignalLiquidity5 = 0.3m,
                SignalCloseLocation = 0.4m,
                SignalVolatility10 = 0.5m,
                SignalGapAbs = 0.6m,
            };

            Assert.That(AShareEtfT0FeatureIntradayAlgorithm.IsFreshFeatureSnapshot(feature, new DateTime(2024, 2, 7)), Is.True);
            Assert.That(AShareEtfT0FeatureIntradayAlgorithm.IsFreshFeatureSnapshot(feature, new DateTime(2024, 2, 8)), Is.False);
        }

        [Test]
        public void FreshFeatureSnapshotRequiresAllCoreSignals()
        {
            var feature = new AShareEtfT0FeatureData
            {
                EndTime = new DateTime(2024, 2, 7),
                SignalMomentum20 = 0.1m,
                SignalMomentum5 = 0.2m,
                SignalLiquidity5 = 0.3m,
                SignalCloseLocation = 0.4m,
                SignalVolatility10 = 0.5m,
            };

            Assert.That(AShareEtfT0FeatureIntradayAlgorithm.IsFreshFeatureSnapshot(feature, new DateTime(2024, 2, 7)), Is.False);
        }

        [Test]
        public void VolTargetScaleShrinksWhenRealizedVolExceedsTarget()
        {
            var scale = AShareEtfT0FeatureIntradayAlgorithm.ComputePortfolioVolTargetScale(
                new List<decimal> { 0.04m, -0.02m, 0.03m, -0.01m },
                0.01m,
                4,
                2,
                0.25m,
                1.0m);

            Assert.That(scale, Is.LessThan(1.0m));
            Assert.That(scale, Is.EqualTo(0.3922m).Within(0.0002m));
        }

        private static SubscriptionDataConfig CreateConfig(Symbol underlying)
        {
            var customSymbol = Symbol.CreateBase(typeof(AShareEtfT0FeatureData), underlying, underlying.ID.Market);
            return new SubscriptionDataConfig(
                typeof(AShareEtfT0FeatureData),
                customSymbol,
                Resolution.Daily,
                TimeZones.Shanghai,
                TimeZones.Shanghai,
                false,
                false,
                false);
        }
    }
}
