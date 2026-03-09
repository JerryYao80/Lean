using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace QuantConnect.Tests.Configuration
{
    [TestFixture]
    public class AShareT1LeanLauncherConfigTests
    {
        [Test]
        public void MomentumLeanBacktestConfigDeclaresLauncherParameters()
        {
            var configPath = Path.GetFullPath(Path.Combine(
                TestContext.CurrentContext.TestDirectory,
                "../../../Launcher/config/config-ashare-t1-momentum-lean-backtest.json"));

            Assert.That(File.Exists(configPath), Is.True, $"Missing config: {configPath}");

            var config = JObject.Parse(File.ReadAllText(configPath));
            var parameters = (JObject)config["parameters"];

            Assert.That((string)config["environment"], Is.EqualTo("backtesting"));
            Assert.That((string)config["algorithm-type-name"], Is.EqualTo("AShareT1MomentumAlgorithm"));
            Assert.That((string)config["algorithm-language"], Is.EqualTo("CSharp"));
            Assert.That((string)config["algorithm-location"], Does.Contain("QuantConnect.Algorithm.CSharp.dll"));
            Assert.That((string)config["results-destination-folder"], Does.Contain("Results"));

            Assert.That(parameters, Is.Not.Null);
            Assert.That((string)parameters["tushare-data-path"], Does.Contain("tushare_data"));
            Assert.That((string)parameters["universe"], Does.Contain("000001.SZ"));
            Assert.That((string)parameters["lookback-period"], Is.EqualTo("20"));
            Assert.That((string)parameters["entry-threshold"], Is.EqualTo("0.08"));
            Assert.That((string)parameters["exit-threshold"], Is.EqualTo("0.00"));
            Assert.That((string)parameters["signal-file"], Does.Contain("ashare-t1-momentum-backtest-signals.json"));
            Assert.That((string)parameters["portfolio-snapshot-file"], Does.Contain("ashare-t1-momentum-backtest-portfolio.json"));
            Assert.That((string)parameters["daily-summary-file"], Does.Contain("ashare-t1-momentum-backtest-daily.csv"));
        }
    }
}
