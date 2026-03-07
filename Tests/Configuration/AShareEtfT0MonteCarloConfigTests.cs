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

using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace QuantConnect.Tests.Configuration
{
    [TestFixture]
    public class AShareEtfT0MonteCarloConfigTests
    {
        [Test]
        public void MonteCarloConfigSupportsLeanLauncherAndResearchScript()
        {
            var configPath = Path.GetFullPath(Path.Combine(
                TestContext.CurrentContext.TestDirectory,
                "../../../Launcher/config/config-ashare-etf-t0-monte-carlo.json"));

            Assert.That(File.Exists(configPath), Is.True, $"Missing config: {configPath}");

            var config = JObject.Parse(File.ReadAllText(configPath));
            var parameters = (JObject)config["parameters"];

            Assert.That((string)config["environment"], Is.EqualTo("backtesting"));
            Assert.That((string)config["algorithm-type-name"], Is.EqualTo("AShareEtfT0FeatureIntradayAlgorithm"));
            Assert.That((string)config["algorithm-language"], Is.EqualTo("CSharp"));
            Assert.That((string)config["algorithm-location"], Does.Contain("QuantConnect.Algorithm.CSharp.dll"));
            Assert.That((string)config["results-destination-folder"], Does.Contain("Results"));

            Assert.That((string)config["registry-file"], Does.Contain("AShareETFMetadata.cs"));
            Assert.That((string)config["dataset-catalog"], Does.Contain("config-ashare-dataset-catalog.json"));
            Assert.That((string)config["report-file"], Does.Contain("monte-carlo.log"));

            Assert.That(parameters, Is.Not.Null);
            Assert.That((string)parameters["tushare-data-path"], Does.Contain("tushare_data"));
            Assert.That((string)parameters["feature-data-path"], Does.Contain("ashare-etf-t0-features"));
            Assert.That((string)parameters["monte-carlo-enabled"], Is.EqualTo("true"));
            Assert.That((string)parameters["monte-carlo-trials"], Is.EqualTo("500"));
            Assert.That((string)parameters["monte-carlo-horizon-days"], Is.EqualTo("63"));
            Assert.That((string)parameters["monte-carlo-block-size"], Is.EqualTo("5"));
            Assert.That((string)parameters["monte-carlo-seed"], Is.EqualTo("42"));
        }
    }
}
