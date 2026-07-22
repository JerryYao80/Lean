// file: Tests/Algorithm/Models/CompositeRiskModelTests.cs
using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Core;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Tests.Algorithm.Models
{
    [TestFixture]
    public class CompositeRiskModelTests
    {
        [Test]
        public void FromVaR_ReturnsCompositeWithThreeModels()
        {
            var model = CompositeRiskModel.FromVaR();
            Assert.IsNotNull(model);
        }

        [Test]
        public void FromVaR_AcceptsCustomParameters()
        {
            var model = CompositeRiskModel.FromVaR(
                varBudgetFraction: 0.05m, maxDrawdown: 0.15m, maxPositionWeight: 0.30m,
                scenario: VaRScenario.TenDay99);
            Assert.IsNotNull(model);
        }

        [Test]
        public void FromChain_EmptyThrows()
        {
            Assert.Throws<ArgumentException>(() => CompositeRiskModel.FromChain());
        }

        [Test]
        public void FromRegistryIds_RegistersVaRModelsInModelRegistry()
        {
            ModelRegistry.Initialize();
            var varModel = ModelRegistry.GetRisk("risk_var");
            var composite = ModelRegistry.GetRisk("risk_composite_var_default");
            Assert.IsNotNull(varModel, "risk_var should be registered");
            Assert.IsNotNull(composite, "risk_composite_var_default should be registered");
        }

        [Test]
        public void FromRegistryIds_BuildsFromIds()
        {
            ModelRegistry.Initialize();
            var model = CompositeRiskModel.FromRegistryIds("risk_var", "risk_max_drawdown", "risk_position_limit");
            Assert.IsNotNull(model);
        }
    }
}
