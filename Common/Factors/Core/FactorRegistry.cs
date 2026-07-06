using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Factors.Core
{
    public static class FactorRegistry
    {
        private static readonly Dictionary<string, IFactor> _factors = new();
        private static readonly Dictionary<string, FactorMetadata> _metadata = new();
        private static bool _initialized = false;
        private static readonly object _lock = new();

        public static void Initialize()
        {
            lock (_lock)
            {
                if (_initialized) return;

                // Volatility factors
                Register(new QuantConnect.Factors.Volatility.IVPercentileFactor(252));
                Register(new QuantConnect.Factors.Volatility.HVFactor(20));
                Register(new QuantConnect.Factors.Volatility.VIXFactor());
                Register(new QuantConnect.Factors.Volatility.IVSkewFactor(252));
                Register(new QuantConnect.Factors.Volatility.IVTermStructureFactor());

                // Trend factors
                Register(new QuantConnect.Factors.Trend.MomentumFactor(20));
                Register(new QuantConnect.Factors.Trend.MACrossFactor(5, 20));
                Register(new QuantConnect.Factors.Trend.RSIFactor(14));

                // Value factors
                Register(new QuantConnect.Factors.Value.PEPercentileFactor());
                Register(new QuantConnect.Factors.Value.PBPercentileFactor());
                Register(new QuantConnect.Factors.Value.DividendYieldFactor());

                // Quality factors
                Register(new QuantConnect.Factors.Quality.ROEFactor());
                Register(new QuantConnect.Factors.Quality.MarginFactor());
                Register(new QuantConnect.Factors.Quality.LeverageFactor());

                // Sentiment factors
                Register(new QuantConnect.Factors.Sentiment.CrowdingFactor());
                Register(new QuantConnect.Factors.Sentiment.PCRFactor());
                Register(new QuantConnect.Factors.Sentiment.NorthboundFactor());

                // Liquidity factors
                Register(new QuantConnect.Factors.Liquidity.TurnoverRateFactor());
                Register(new QuantConnect.Factors.Liquidity.AmihudIlliquidityFactor());

                // Chip factors
                Register(new QuantConnect.Factors.Chip.ConcentrationFactor());
                Register(new QuantConnect.Factors.Chip.ProfitRatioFactor());
                Register(new QuantConnect.Factors.Chip.PeakPatternFactor());
                Register(new QuantConnect.Factors.Chip.ChipPeakCompositeFactor());
                Register(new QuantConnect.Factors.Chip.CostDeviationFactor());

                // Forward factors (前瞻性因子, docs/qianzhan.md)
                // 3 个立足：数据立足(tushare 表) + 计算立足(静态 Compute 方法) + 消费立足(Layer A/B/C)
                // 3 层管线：Layer A 选股(alpha) / Layer B 风控(预警) / Layer C 执行(仓位调节)
                Register(new QuantConnect.Factors.Forward.BigOrderNetFlowFactor());
                Register(new QuantConnect.Factors.Forward.NorthboundMomentumFactor());
                Register(new QuantConnect.Factors.Forward.AuctionGapFactor());
                Register(new QuantConnect.Factors.Forward.MomentumAccelerationFactor());
                Register(new QuantConnect.Factors.Forward.VolumeAnomalyFactor());
                Register(new QuantConnect.Factors.Forward.EarningsSurpriseFactor());
                Register(new QuantConnect.Factors.Forward.DisclosureTimingFactor());
                Register(new QuantConnect.Factors.Forward.InsiderTradeFactor());
                Register(new QuantConnect.Factors.Forward.PledgeRiskFactor());
                Register(new QuantConnect.Factors.Forward.M1M2ScissorsFactor());
                Register(new QuantConnect.Factors.Forward.ThemeHeatFactor());
                Register(new QuantConnect.Factors.Forward.ConvertPremiumFactor());

                _initialized = true;
            }
        }

        public static void Register(IFactor factor)
        {
            // 直接注册，不调用 EnsureInitialized()（避免 Initialize→Register→EnsureInitialized 无限递归）
            lock (_lock)
            {
                _factors[factor.Id] = factor;
                _metadata[factor.Id] = new FactorMetadata
                {
                    Id = factor.Id, Name = factor.Name, Category = factor.Category,
                    Scope = factor.Scope, ComputeMode = factor.ComputeMode, DataSource = factor.DataSource
                };
            }
        }

        public static IFactor Get(string factorId)
        { EnsureInitialized(); return _factors.TryGetValue(factorId, out var f) ? f : null; }

        public static IReadOnlyList<IFactor> GetByCategory(FactorCategory category)
        { EnsureInitialized(); return _factors.Values.Where(f => f.Category == category).ToList(); }

        public static IReadOnlyList<IFactor> GetByScope(FactorScope scope)
        { EnsureInitialized(); return _factors.Values.Where(f => f.Scope == scope || f.Scope == FactorScope.Both).ToList(); }

        public static IReadOnlyList<IFactor> GetTimingFactors() => GetByScope(FactorScope.TimeSeries);
        public static IReadOnlyList<IFactor> GetSelectionFactors() => GetByScope(FactorScope.CrossSection);
        public static IReadOnlyList<IFactor> GetByComputeMode(FactorComputeMode mode)
        { EnsureInitialized(); return _factors.Values.Where(f => f.ComputeMode == mode).ToList(); }

        public static IReadOnlyDictionary<string, FactorMetadata> AllMetadata()
        { EnsureInitialized(); return _metadata; }

        private static void EnsureInitialized() { if (!_initialized) Initialize(); }
    }
}