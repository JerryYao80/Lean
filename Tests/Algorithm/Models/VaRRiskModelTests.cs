// file: Tests/Algorithm/Models/VaRRiskModelTests.cs
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Tests.Algorithm.Models
{
    [TestFixture]
    public class VaRRiskModelTests
    {
        [Test]
        public void Constructor_SetsDefaults()
        {
            var model = new VaRRiskModel();
            Assert.AreEqual(1.0m, model.LastScaleApplied);
        }

        [Test]
        public void Constructor_AcceptsCustomBudget()
        {
            var model = new VaRRiskModel(varBudgetFraction: 0.05m, scenario: VaRScenario.TenDay99);
            Assert.AreEqual(1.0m, model.LastScaleApplied);
        }
    }
}
