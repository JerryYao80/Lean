// file: Algorithm.CSharp/Models/Risk/CompositeRiskModel.cs
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// Composite risk model chaining VaR -> MaxDrawdown -> PositionLimit.
    /// Extends native CompositeRiskManagementModel (NOT sealed) — adds only static factories.
    /// Base class uses riskAdjusted.Concat(targets).DistinctBy(Symbol) (newer wins).
    /// Chain is monotonically non-increasing (each stage only reduces or passes through).
    /// </summary>
    /// <remarks>
    /// NOTE: MaxDrawdownRiskModel is long-only (only liquidates Quantity > 0).
    /// FromVaR factory is intended for long-only A-share universes (ETFs + CSI300).
    /// </remarks>
    public class CompositeRiskModel : CompositeRiskManagementModel
    {
        public CompositeRiskModel(params IRiskManagementModel[] models) : base(models) { }
        public CompositeRiskModel(IEnumerable<IRiskManagementModel> models) : base(models) { }

        /// <summary>Default chain: VaR -> MaxDrawdown -> PositionLimit.</summary>
        public static CompositeRiskModel FromVaR(
            decimal varBudgetFraction = 0.02m,
            decimal maxDrawdown = 0.20m,
            decimal maxPositionWeight = 0.40m,
            VaRMethod method = VaRMethod.BootstrapHistorical,
            VaRScenario scenario = VaRScenario.OneDay99,
            int lookbackDays = VaRConstants.DefaultLookbackDays)
        {
            var varModel = new VaRRiskModel(
                varBudgetFraction: varBudgetFraction, method: method,
                scenario: scenario, lookbackDays: lookbackDays);
            var ddModel = new MaxDrawdownRiskModel(maxDrawdown: maxDrawdown);
            var plModel = new PositionLimitRiskModel(maxPosition: maxPositionWeight);
            return new CompositeRiskModel(varModel, ddModel, plModel);
        }

        /// <summary>Custom chain from explicit models.</summary>
        public static CompositeRiskModel FromChain(params IRiskManagementModel[] models)
        {
            if (models == null || models.Length == 0)
                throw new ArgumentException("CompositeRiskModel.FromChain requires at least one model.");
            return new CompositeRiskModel(models);
        }

        /// <summary>Build from ModelRegistry IDs.</summary>
        public static CompositeRiskModel FromRegistryIds(params string[] modelIds)
        {
            Core.ModelRegistry.Initialize();
            var models = new List<IRiskManagementModel>();
            foreach (var id in modelIds)
            {
                var m = Core.ModelRegistry.GetRisk(id);
                if (m == null) throw new InvalidOperationException("Unknown risk model id: " + id);
                models.Add(m);
            }
            return new CompositeRiskModel(models);
        }
    }
}
