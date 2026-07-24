// file: Algorithm.CSharp/Models/Risk/VaRRiskModel.cs
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.Market;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// VaR-based risk model (Layer 2). Implements IRiskManagementModel via RiskManagementModel base.
    /// Computes portfolio VaR via VarEngine; scales targets down (never up) when VaR breaches budget.
    /// Uses incremental return cache + date gate (mirrors AShareBarraCNE5V4RiskManagementModel).
    /// </summary>
    public class VaRRiskModel : RiskManagementModel
    {
        private readonly decimal _varBudgetFraction;
        private readonly decimal _esBudgetFraction;
        private readonly VaRMethod _method;
        private readonly VaRScenario _scenario;
        private readonly int _lookbackDays;
        private readonly VarConfig _varConfig;

        // Incremental return cache: symbol -> rolling list of daily returns
        private readonly Dictionary<Symbol, List<double>> _returnsCache = new();
        private DateTime _lastVarDate = DateTime.MinValue;
        private decimal _lastScale = 1.0m;

        public decimal LastScaleApplied => _lastScale;

        public VaRRiskModel(
            decimal varBudgetFraction = 0.02m,
            decimal esBudgetFraction = 0.015m,
            VaRMethod method = VaRMethod.BootstrapHistorical,
            VaRScenario scenario = VaRScenario.OneDay99,
            int lookbackDays = VaRConstants.DefaultLookbackDays)
        {
            _varBudgetFraction = varBudgetFraction;
            _esBudgetFraction = esBudgetFraction;
            _method = method;
            _scenario = scenario;
            _lookbackDays = lookbackDays;
            _varConfig = VarConfig.FromScenario(scenario, lookbackDays);
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // Date gate: skip recomputation if same day
            if (algorithm.Time.Date == _lastVarDate.Date)
                return ScaleTargets(algorithm, targets, _lastScale);

            // Warmup / empty checks
            if (algorithm.IsWarmingUp || targets == null || targets.Length == 0)
                return targets;

            var tpv = algorithm.Portfolio.TotalPortfolioValue;
            if (tpv <= 0) return targets;

            // Update incremental return cache from latest history
            UpdateReturnsCache(algorithm, targets);

            // Build portfolio input from cache
            var input = BuildPortfolioInput(algorithm, targets, tpv, algorithm.Time);
            if (input == null) return targets;

            var varResult = VarEngine.ComputePortfolio(input.Value, _method, _scenario, _varConfig);
            if (!varResult.IsValid)
            {
                algorithm.Error($"VaRRiskModel: VarEngine returned {varResult.Quality}; passing targets through");
                _lastScale = 1.0m;
                _lastVarDate = algorithm.Time;
                return targets;
            }

            // Breach metric: max(VaR, ES_normalized)
            var breach = (decimal)varResult.ValueAtRisk;
            if (_esBudgetFraction > 0 && varResult.ExpectedShortfall > 0)
            {
                var esNorm = (decimal)(varResult.ExpectedShortfall / (double)_esBudgetFraction) * _varBudgetFraction;
                breach = Math.Max(breach, esNorm);
            }

            // Scale: never scale up
            var scale = breach <= _varBudgetFraction ? 1.0m : _varBudgetFraction / breach;
            scale = Math.Max(0m, Math.Min(1m, scale));

            _lastScale = scale;
            _lastVarDate = algorithm.Time;

            return ScaleTargets(algorithm, targets, scale);
        }

        private void UpdateReturnsCache(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var symbols = targets.Select(t => t.Symbol).Distinct().ToList();
            foreach (var sym in symbols)
            {
                if (!_returnsCache.ContainsKey(sym))
                    _returnsCache[sym] = new List<double>();

                // Fetch only the latest needed bars (incremental: fetch lookback on first call, then append)
                if (_returnsCache[sym].Count == 0)
                {
                    var history = algorithm.History<TradeBar>(sym, _lookbackDays + 10, Resolution.Daily);
                    var closes = history.ToList();
                    for (int i = 1; i < closes.Count; i++)
                    {
                        if (closes[i - 1].Close > 0)
                            _returnsCache[sym].Add((double)(closes[i].Close / closes[i - 1].Close - 1m));
                    }
                }
            }
        }

        private PortfolioVaRInput? BuildPortfolioInput(QCAlgorithm algorithm, IPortfolioTarget[] targets, decimal tpv, DateTime asOf)
        {
            var validTargets = targets
                .Where(t => t != null && algorithm.Securities.ContainsKey(t.Symbol) && algorithm.Securities[t.Symbol].Price > 0)
                .ToList();
            if (validTargets.Count == 0) return null;

            var symbols = validTargets.Select(t => t.Symbol).ToList();
            var returnsList = new List<IReadOnlyList<double>>();
            var weights = new List<double>();

            foreach (var t in validTargets)
            {
                if (!_returnsCache.TryGetValue(t.Symbol, out var rets) || rets.Count < VaRConstants.MinHistoryDays)
                    return null;
                // Take last lookbackDays
                var window = rets.Skip(Math.Max(0, rets.Count - _lookbackDays)).ToList();
                returnsList.Add(window);
                var price = algorithm.Securities[t.Symbol].Price;
                weights.Add((double)(Math.Abs(t.Quantity) * price / tpv));
            }

            return new PortfolioVaRInput(returnsList, weights, symbols, asOf);
        }

        private IEnumerable<IPortfolioTarget> ScaleTargets(QCAlgorithm algorithm, IPortfolioTarget[] targets, decimal scale)
        {
            foreach (var t in targets)
            {
                if (t == null || t.Quantity == 0 || scale == 1.0m)
                {
                    yield return t;
                    continue;
                }

                var lotSize = algorithm.Securities.ContainsKey(t.Symbol)
                    ? algorithm.Securities[t.Symbol].SymbolProperties.LotSize
                    : 100m;

                var rawQty = t.Quantity * scale;
                var sign = Math.Sign(rawQty);
                var absQty = Math.Abs(rawQty);
                var rounded = Math.Floor(absQty / lotSize) * lotSize * sign;
                // Sub-lot -> 0
                if (Math.Abs(rounded) > 0 && Math.Abs(rounded) < lotSize)
                    rounded = 0m;

                yield return new PortfolioTarget(t.Symbol, rounded);
            }
        }
    }
}
