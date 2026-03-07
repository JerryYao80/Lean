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
            const string line = "20240122,1.000000,1.010000,1.030000,0.990000,1.020000,1.200000,1000000,500000,0.009901,0.010000,0.750000,0.040000,0.030000,0.080000,0.012000,1200000,0.010000,-0.050000,-0.020000,1100000,0.650000,0.015000,0.008000";

            var data = new AShareEtfT0FeatureData().Reader(config, line, new DateTime(2024, 1, 22), false) as AShareEtfT0FeatureData;

            Assert.IsNotNull(data);
            Assert.AreEqual(config.Symbol, data.Symbol);
            Assert.AreEqual(underlying, data.UnderlyingSymbol);
            Assert.AreEqual(new DateTime(2024, 1, 22), data.Time);
            Assert.AreEqual(1.02m, data.Close);
            Assert.AreEqual(0.75m, data.CloseLocation);
            Assert.AreEqual(-0.05m, data.SignalMomentum20);
            Assert.AreEqual(1100000m, data.SignalLiquidity5);
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
