using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace QuantConnect.Tests.Configuration
{
    [TestFixture]
    public class AShareT1WorkflowConfigTests
    {
        [TestCase("config-ashare-t1-backtest.json", "../../Results/ashare-t1-summary.json")]
        [TestCase("config-ashare-t1-optimize.json", "../../Results/ashare-t1-optimize-summary.json")]
        public void T1WorkflowConfigsIncludeSummaryOutputs(string fileName, string expectedSummaryFile)
        {
            var configPath = Path.GetFullPath(Path.Combine(
                TestContext.CurrentContext.TestDirectory,
                $"../../../Launcher/config/{fileName}"));

            Assert.That(File.Exists(configPath), Is.True, $"Missing config: {configPath}");

            var config = JObject.Parse(File.ReadAllText(configPath));

            Assert.That((string)config["tushare-data-path"], Does.Contain("tushare_data"));
            Assert.That((string)config["dataset-catalog"], Does.Contain("config-ashare-dataset-catalog.json"));
            Assert.That((string)config["report-file"], Does.Contain("Results"));
            Assert.That((string)config["trade-report-file"], Does.Contain("Results"));
            Assert.That((string)config["daily-summary-file"], Does.Contain("Results"));
            Assert.That((string)config["summary-file"], Is.EqualTo(expectedSummaryFile));
            Assert.That(config["universe"], Is.Not.Null);
        }
    }
}
