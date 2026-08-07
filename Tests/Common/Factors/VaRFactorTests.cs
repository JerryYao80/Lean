// file: Tests/Common/Factors/VaRFactorTests.cs
using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Risk;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class VaRFactorTests
    {
        [Test]
        public void Register_AddsVaRFactorToRegistry()
        {
            VaRFactors.Register();
            var factor = FactorRegistry.Get("var_1d99");
            Assert.IsNotNull(factor);
            Assert.AreEqual(FactorCategory.Volatility, factor.Category);
            Assert.AreEqual(FactorScope.Both, factor.Scope);
        }

        [Test]
        public void Register_RegistersIvHvSpreadDependency()
        {
            VaRFactors.Register();
            var spread = FactorRegistry.Get("iv_hv_spread");
            Assert.IsNotNull(spread, "IVHVSpreadFactor should be registered by VaRFactors.Register");
        }

        [Test]
        public void Metadata_HasCorrectDependencies()
        {
            VaRFactors.Register();
            var meta = VaRFactors.Metadata;
            Assert.IsNotNull(meta);
            Assert.Contains("hv_20d", meta.Dependencies);
            Assert.Contains("chip_concentration", meta.Dependencies);
            Assert.Contains("momentum_20d", meta.Dependencies);
        }

        [Test]
        public void ComputeRank_EmptyWithoutCrossSectionData()
        {
            VaRFactors.Register();
            var factor = VaRFactors.Instance;
            var result = factor.ComputeRank(new List<Symbol>(), DateTime.UtcNow);
            Assert.IsNotNull(result.Values);
            Assert.IsNotNull(result.PercentileRanks);
            Assert.AreEqual(0, result.Values.Count);
        }
    }
}
