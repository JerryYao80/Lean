using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace QuantConnect.Tests.Configuration
{
    [TestFixture]
    public class BarraCNE5LeanLauncherConfigTests
    {
        [Test]
        public void BacktestConfigDeclaresBarraAlgorithmAndOutputs()
        {
            var configPath = Path.GetFullPath(Path.Combine(
                TestContext.CurrentContext.TestDirectory,
                "../../../Launcher/config/config-barra-cne5-backtest.json"));

            Assert.That(File.Exists(configPath), Is.True, $"Missing config: {configPath}");

            var config = JObject.Parse(File.ReadAllText(configPath));
            var parameters = (JObject)config["parameters"];

            Assert.That((string)config["environment"], Is.EqualTo("backtesting"));
            Assert.That((string)config["algorithm-type-name"], Is.EqualTo("AShareBarraCNE5Algorithm"));
            Assert.That((string)config["algorithm-language"], Is.EqualTo("CSharp"));
            Assert.That((string)config["algorithm-location"], Does.Contain("QuantConnect.Algorithm.CSharp.dll"));
            Assert.That((string)config["results-destination-folder"], Does.Contain("Results"));

            Assert.That(parameters, Is.Not.Null);
            Assert.That((string)parameters["factor-data-path"], Does.Contain("barra-cne5-factors"));
            Assert.That((string)parameters["rebalance-frequency"], Is.EqualTo("monthly"));
            Assert.That((string)parameters["top-n"], Is.EqualTo("30"));
            Assert.That((string)parameters["min-score-spread"], Is.EqualTo("0.50"));
            Assert.That((string)parameters["target-portfolio-exposure"], Is.EqualTo("0.90"));
            Assert.That((string)parameters["minimum-present-factors"], Is.EqualTo("6"));
            Assert.That((string)parameters["weighting-mode"], Is.EqualTo("black-litterman"));
            Assert.That((string)parameters["portfolio-kelly-fraction"], Is.EqualTo("0.50"));
            Assert.That((string)parameters["kelly-lookback-closed-trades"], Is.EqualTo("24"));
            Assert.That((string)parameters["kelly-min-closed-trades"], Is.EqualTo("8"));
            Assert.That((string)parameters["kelly-fallback-scale"], Is.EqualTo("0.60"));
            Assert.That((string)parameters["black-litterman-tau"], Is.EqualTo("0.05"));
            Assert.That((string)parameters["black-litterman-view-confidence"], Is.EqualTo("0.65"));
            Assert.That((string)parameters["max-single-weight"], Is.EqualTo("0.10"));
            Assert.That((string)parameters["stop-loss-pct"], Is.EqualTo("0.09"));
            Assert.That((string)parameters["trailing-stop-pct"], Is.EqualTo("0.06"));
            Assert.That((string)parameters["monte-carlo-enabled"], Is.EqualTo("true"));
            Assert.That((string)parameters["monte-carlo-trials"], Is.EqualTo("500"));
            Assert.That((string)parameters["monte-carlo-horizon-days"], Is.EqualTo("63"));
            Assert.That((string)parameters["monte-carlo-block-size"], Is.EqualTo("5"));
            Assert.That((string)parameters["monte-carlo-seed"], Is.EqualTo("42"));
            Assert.That((string)parameters["monte-carlo-factor-perturbation-scale"], Is.EqualTo("0.15"));
            Assert.That((string)parameters["trade-report-file"], Does.Contain("barra-cne5-trades.csv"));
            Assert.That((string)parameters["daily-summary-file"], Does.Contain("barra-cne5-daily-summary.csv"));
            Assert.That((string)parameters["allocation-report-file"], Does.Contain("barra-cne5-allocation.csv"));
            Assert.That((string)parameters["factor-exposure-file"], Does.Contain("barra-cne5-factor-exposure.csv"));
        }
    }
}
