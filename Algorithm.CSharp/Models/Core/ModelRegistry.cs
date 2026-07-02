using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Algorithm.Framework.Execution;

namespace QuantConnect.Algorithm.CSharp.Models.Core
{
    /// <summary>
    /// 模型动物园注册表 - 全局索引, 按 ID/类型检索模型.
    /// 放在 Algorithm.CSharp 层 (因 IAlphaModel 等接口在 Algorithm 层).
    /// </summary>
    public static class ModelRegistry
    {
        private static readonly Dictionary<string, object> _models = new();
        private static readonly Dictionary<string, ModelMetadata> _metadata = new();
        private static bool _initialized = false;

        public static void Initialize()
        {
            if (_initialized) return;

            // Alpha models (因子动物园消费方)
            RegisterAlpha(new ChipPeakFactorZooAlphaModel(), "alpha_chippeak");

            // Portfolio models
            RegisterPortfolio(new Portfolio.EqualWeightPortfolioModel(), "portfolio_equal_weight");

            // Risk models
            RegisterRisk(new Risk.MaxDrawdownRiskModel(), "risk_max_drawdown");
            RegisterRisk(new Risk.PositionLimitRiskModel(), "risk_position_limit");

            // Execution models
            RegisterExecution(new Execution.ImmediateExecutionModel(), "execution_immediate");

            _initialized = true;
        }

        public static void RegisterAlpha(IAlphaModel m, string id) => Register(m, "alpha", id);
        public static void RegisterPortfolio(IPortfolioConstructionModel m, string id) => Register(m, "portfolio", id);
        public static void RegisterRisk(IRiskManagementModel m, string id) => Register(m, "risk", id);
        public static void RegisterUniverse(IUniverseSelectionModel m, string id) => Register(m, "universe", id);
        public static void RegisterExecution(IExecutionModel m, string id) => Register(m, "execution", id);

        private static void Register<T>(T model, string type, string id)
        {
            _models[id] = model;
            _metadata[id] = new ModelMetadata { Id = id, Name = model.GetType().Name, ModelType = type };
        }

        public static IAlphaModel GetAlpha(string id) => Get<IAlphaModel>(id);
        public static IPortfolioConstructionModel GetPortfolio(string id) => Get<IPortfolioConstructionModel>(id);
        public static IRiskManagementModel GetRisk(string id) => Get<IRiskManagementModel>(id);
        public static IUniverseSelectionModel GetUniverse(string id) => Get<IUniverseSelectionModel>(id);
        public static IExecutionModel GetExecution(string id) => Get<IExecutionModel>(id);

        private static T Get<T>(string id) => _models.TryGetValue(id, out var m) ? (T)m : default;

        public static IReadOnlyList<ModelMetadata> ListByType(string type)
            => _metadata.Values.Where(m => m.ModelType == type).ToList();

        public static IReadOnlyDictionary<string, ModelMetadata> AllMetadata() => _metadata;
    }
}
