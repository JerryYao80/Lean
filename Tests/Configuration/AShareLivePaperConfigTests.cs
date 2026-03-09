using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace QuantConnect.Tests.Configuration
{
    [TestFixture]
    public class AShareLivePaperConfigTests
    {
        [TestCase("config-ashare-etf-live-paper.json", "ETFMomentumStrategy")]
        [TestCase("config-ashare-t1-live-paper.json", "AShareT1MeanReversionAlgorithm")]
        [TestCase("config-ashare-t1-momentum-live-paper.json", "AShareT1MomentumAlgorithm")]
        public void LivePaperConfigsDeclareExplicitLiveTradingHandlers(string fileName, string algorithmTypeName)
        {
            var configPath = Path.GetFullPath(Path.Combine(
                TestContext.CurrentContext.TestDirectory,
                $"../../../Launcher/config/{fileName}"));

            Assert.That(File.Exists(configPath), Is.True, $"Missing config: {configPath}");

            var config = JObject.Parse(File.ReadAllText(configPath));
            var dataQueueHandlers = config["data-queue-handler"]?.Values<string>().ToList();
            var historyProviders = config["history-provider"]?.Values<string>().ToList();

            Assert.That((string)config["environment"], Is.EqualTo("live-paper"));
            Assert.That((bool?)config["live-mode"], Is.True);
            Assert.That((string)config["algorithm-type-name"], Is.EqualTo(algorithmTypeName));
            Assert.That((string)config["live-mode-brokerage"], Is.EqualTo("PaperBrokerage"));
            Assert.That((string)config["setup-handler"], Is.EqualTo("QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler"));
            Assert.That((string)config["result-handler"], Is.EqualTo("QuantConnect.Lean.Engine.Results.LiveTradingResultHandler"));
            Assert.That((string)config["data-feed-handler"], Is.EqualTo("QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed"));
            Assert.That((string)config["real-time-handler"], Is.EqualTo("QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler"));
            Assert.That((string)config["transaction-handler"], Is.EqualTo("QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler"));

            Assert.That(dataQueueHandlers, Is.Not.Null);
            CollectionAssert.Contains(dataQueueHandlers, "TushareDataQueue");

            Assert.That(historyProviders, Is.Not.Null);
            CollectionAssert.Contains(historyProviders, "TushareHistoryProvider");
        }
    }
}
