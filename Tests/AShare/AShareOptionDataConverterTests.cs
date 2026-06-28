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

using NUnit.Framework;
using QuantConnect.ToolBox;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareOptionDataConverterTests
    {
        private const string TusharePath = "/home/project/tushare-downloader/tushare_data_v2";
        private const string LeanDataPath = "Data";

        [Test]
        public void Constructor_InitializesSuccessfully()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            Assert.IsNotNull(converter);
        }

        [Test]
        public void RunPythonScript_ReturnsValidJson()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            var output = converter.RunPythonScript("print('[1, 2, 3]')");
            Assert.AreEqual("[1, 2, 3]", output.Trim());
        }

        [Test]
        public void LoadOptBasic_ReturnsSSEContracts()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            var contracts = converter.LoadOptBasic("OP510050.SH");

            Assert.Greater(contracts.Count, 0);
            Assert.IsTrue(contracts.All(c => c.Exchange == "SSE"));
            Assert.IsTrue(contracts.All(c => c.OptCode == "OP510050.SH"));
            Assert.IsTrue(contracts.All(c => c.ExercisePrice > 0m));
        }

        [Test]
        public void ConvertAllContracts_GeneratesUniverseAndDailyFiles()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            converter.ConvertAllContracts("OP510050.SH", "20240628");

            var universePath = Path.Combine(LeanDataPath, "option", "china", "universes",
                                             "510050", "20240628.csv");
            Assert.IsTrue(File.Exists(universePath));

            var lines = File.ReadAllLines(universePath);
            Assert.AreEqual("symbol,expiration,strike,right,style", lines[0]);
            Assert.Greater(lines.Length, 20, "Universe should contain >20 contracts");

            // Verify all are European (A-share ETF options are European-style)
            foreach (var line in lines.Skip(1))
            {
                Assert.IsTrue(line.EndsWith(",European"), $"Non-European contract: {line}");
            }
        }
    }
}
