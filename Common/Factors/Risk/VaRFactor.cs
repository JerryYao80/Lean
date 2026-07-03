// file: Common/Factors/Risk/VaRFactor.cs
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Risk
{
    /// <summary>
    /// VaR regime factor (Layer 1). Thin wrapper over VarEngine.
    /// Category=Volatility (NOT a new Risk enum value — zero-invasion).
    /// Scope=Both (TimeSeries regime + CrossSection marginal contribution).
    /// </summary>
    public class VaRFactor : IFactor
    {
        public string Id => "var_1d99";
        public string Name => "Value-at-Risk Regime (1D 99%)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "price_history+shibor";

        private readonly int _historyDays;
        private readonly int _minUsable;
        private readonly VaRScenario _scenario;
        private readonly VaRMethod _method;
        private readonly int _bootstrapSamples;
        private readonly int _seed;
        private readonly int _regimeWindow;

        // Lazy-resolved dependencies
        private IFactor _hv, _ivPct, _ivHvSpread, _chipConcentration, _momentum;
        private readonly string[] _dependencyIds = {
            "hv_20d", "iv_pct_252d", "iv_hv_spread", "chip_concentration", "momentum_20d"
        };

        // Cross-section injection (set by VarStrategy before ComputeRank)
        private Dictionary<Symbol, double[]> _xsReturns;
        private Dictionary<Symbol, double> _xsWeights;

        public VaRFactor(
            int historyDays = VaRConstants.DefaultLookbackDays,
            int minUsable = VaRConstants.MinHistoryDays,
            VaRScenario scenario = VaRScenario.OneDay99,
            VaRMethod method = VaRMethod.BootstrapHistorical,
            int bootstrapSamples = VaRConstants.DefaultBootstrapIterations,
            int seed = VaRConstants.FixedSeed,
            int regimeWindow = VaRConstants.DefaultLookbackDays)
        {
            _historyDays = historyDays;
            _minUsable = minUsable;
            _scenario = scenario;
            _method = method;
            _bootstrapSamples = bootstrapSamples;
            _seed = seed;
            _regimeWindow = regimeWindow;
        }

        private void EnsureDependencies()
        {
            if (_hv != null) return;
            FactorRegistry.Initialize();
            _hv = FactorRegistry.Get("hv_20d");
            _ivPct = FactorRegistry.Get("iv_pct_252d");
            _ivHvSpread = FactorRegistry.Get("iv_hv_spread");
            _chipConcentration = FactorRegistry.Get("chip_concentration");
            _momentum = FactorRegistry.Get("momentum_20d");
        }

        public bool IsAvailable(Symbol symbol, DateTime time)
        {
            EnsureDependencies();
            return _hv?.IsAvailable(symbol, time) == true;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            EnsureDependencies();
            var sw = Stopwatch.StartNew();

            var bars = history?.OfType<TradeBar>().OrderBy(b => b.Time).TakeLast(_historyDays).ToList();
            if (bars == null || bars.Count < _minUsable)
                return Missing(symbol, time);

            var rets = new List<double>(bars.Count - 1);
            for (int i = 1; i < bars.Count; i++)
                if (bars[i - 1].Close > 0)
                    rets.Add((double)(bars[i].Close / bars[i - 1].Close - 1m));

            if (rets.Count < _minUsable - 1) return Missing(symbol, time);
            if (rets.Any(r => double.IsNaN(r) || double.IsInfinity(r))) return Missing(symbol, time);

            // Pull dependency factor values (regime blending / quality, NOT VaR math)
            var hvR = _hv?.Compute(symbol, time, history) ?? default;
            var ivR = _ivPct?.Compute(symbol, time, null) ?? default;
            var spR = _ivHvSpread?.Compute(symbol, time, history) ?? default;

            var config = new VarConfig(
                lookbackDays: _historyDays, minHistoryDays: _minUsable,
                horizonDays: _scenario == VaRScenario.TenDay99 ? 10 : 1,
                confidence: _scenario == VaRScenario.OneDay95 ? 0.95 : 0.99,
                bootstrapIterations: _bootstrapSamples, randomSeed: _seed);

            var varResult = VarEngine.Compute(symbol, rets.ToArray(), _method, _scenario, config);
            if (!varResult.IsValid)
                return new FactorResult
                {
                    Symbol = symbol, Time = time, FactorId = Id,
                    Quality = FactorDataQuality.Missing, ComputeTimeMs = (int)sw.ElapsedMilliseconds
                };

            var regime = VarEngine.RegimeScore(rets.ToArray(), _regimeWindow, _scenario, time);

            // Quality flag
            FactorDataQuality q = FactorDataQuality.Valid;
            if (regime.IsValid && regime.RegimePercentile >= 0.99m
                && spR.Quality == FactorDataQuality.Valid && spR.RawValue > 0m)
                q = FactorDataQuality.Outlier;
            else if (hvR.Quality != FactorDataQuality.Valid && ivR.Quality != FactorDataQuality.Valid)
                q = FactorDataQuality.Stale;

            return new FactorResult
            {
                Symbol = symbol, Time = time, FactorId = Id,
                Value = regime.IsValid ? regime.RegimePercentile : 0m,
                RawValue = (decimal)varResult.ValueAtRisk,
                Quality = q, ComputeTimeMs = (int)sw.ElapsedMilliseconds
            };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time)
        {
            if (_xsReturns == null || _xsWeights == null || _xsReturns.Count == 0)
                return new FactorRankResult
                {
                    Values = new Dictionary<Symbol, decimal>(),
                    PercentileRanks = new Dictionary<Symbol, decimal>(),
                    RankedSymbols = new List<Symbol>(),
                    Time = time, FactorId = Id, ValidCount = 0, MissingCount = 0
                };

            var mc = VarEngine.MarginalContributions(_xsReturns, _xsWeights, _method, _scenario, time);
            var ordered = mc.OrderByDescending(kv => kv.Value).ToList();
            var values = mc.ToDictionary(kv => kv.Key, kv => (decimal)kv.Value);
            var pct = new Dictionary<Symbol, decimal>();
            int n = ordered.Count;
            for (int i = 0; i < n; i++)
                pct[ordered[i].Key] = n > 1 ? (decimal)(n - 1 - i) / (n - 1) : 0m;

            return new FactorRankResult
            {
                Values = values, PercentileRanks = pct,
                RankedSymbols = ordered.Select(kv => kv.Key).ToList(),
                Time = time, FactorId = Id, ValidCount = n, MissingCount = 0
            };
        }

        public void SetCrossSectionData(Dictionary<Symbol, double[]> perSymbolReturns, Dictionary<Symbol, double> weights)
        {
            _xsReturns = perSymbolReturns;
            _xsWeights = weights;
        }

        private FactorResult Missing(Symbol s, DateTime t) =>
            new FactorResult { Symbol = s, Time = t, FactorId = Id, Quality = FactorDataQuality.Missing };
    }
}
