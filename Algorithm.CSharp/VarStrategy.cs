// file: Algorithm.CSharp/VarStrategy.cs
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.CSharp.Models;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Risk;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// VaR three-layer pipeline strategy (Layer 3).
    /// Two modes via config "var-mode": "single-etf" (510050/510300/510500) or "multi-stock" (CSI300 subset).
    /// </summary>
    public class VarStrategy : QCAlgorithm
    {
        private string _varMode;
        private List<string> _symbols;
        private decimal _varBudget;
        private int _lookbackDays;
        private VaRScenario _scenario;
        private VaRMethod _method;
        private VaRFactor _varFactor;

        public override void Initialize()
        {
            SetStartDate(2018, 1, 1);
            SetEndDate(2025, 12, 31);
            SetCash(1000000);
            SetTimeZone("Asia/Shanghai");

            _varMode = GetParameterOrDefault("var-mode", "single-etf");
            _varBudget = decimal.Parse(GetParameterOrDefault("var-budget", "0.02"));
            _lookbackDays = int.Parse(GetParameterOrDefault("var-lookback-days", "252"));
            var confidence = int.Parse(GetParameterOrDefault("var-confidence", "99"));
            var horizon = int.Parse(GetParameterOrDefault("var-horizon-days", "1"));
            _scenario = (confidence, horizon) switch
            {
                (95, 1) => VaRScenario.OneDay95,
                (99, 1) => VaRScenario.OneDay99,
                (99, 10) => VaRScenario.TenDay99,
                _ => throw new ArgumentException($"Unsupported confidence/horizon: {confidence}/{horizon}")
            };
            _method = Enum.Parse<VaRMethod>(GetParameterOrDefault("var-method", "BootstrapHistorical"));

            ValidateConfig();

            VaRFactors.Register(_scenario, _method);
            _varFactor = VaRFactors.Instance;

            _symbols = _varMode == "single-etf"
                ? new List<string> { "510050", "510300", "510500" }
                : GetParameterOrDefault("var-symbols", "510050,510300,510500").Split(',').ToList();

            foreach (var ticker in _symbols)
            {
                var eq = AddEquity(ticker, Resolution.Daily, Market.SSE);
                eq.SetFeeModel(new QuantConnect.Orders.Fees.ConstantFeeModel(5m));
            }

            SetPortfolioConstruction(new EqualWeightPortfolioModel());

            var maxDD = decimal.Parse(GetParameterOrDefault("risk-max-drawdown", "0.20"));
            var maxPos = decimal.Parse(GetParameterOrDefault("risk-max-position-weight", "0.40"));
            SetRiskManagement(CompositeRiskModel.FromVaR(
                varBudgetFraction: _varBudget, maxDrawdown: maxDD, maxPositionWeight: maxPos,
                method: _method, scenario: _scenario, lookbackDays: _lookbackDays));

            SetWarmUp(_lookbackDays + 10, Resolution.Daily);
        }

        public override void OnData(Slice slice)
        {
            if (IsWarmingUp) return;

            foreach (var symbol in _symbols.Select(s => Securities[s].Symbol))
            {
                var history = History<TradeBar>(symbol, _lookbackDays + 10, Resolution.Daily);
                var result = _varFactor.Compute(symbol, Time, history);

                if (result.Quality == QuantConnect.Factors.Core.FactorDataQuality.Valid)
                {
                    SetRuntimeStatistic("VaR 1D99", (double)result.RawValue);
                    SetRuntimeStatistic("VaR Regime", (double)result.Value);
                    Plot("VaR", "1D99", result.RawValue);
                    Plot("VaR", "Regime", result.Value);
                }
            }
        }

        private void ValidateConfig()
        {
            if (_varBudget <= 0)
                throw new ArgumentException($"var-budget must be > 0, got {_varBudget}");
            if (_lookbackDays < VaRConstants.MinHistoryDays)
                throw new ArgumentException($"var-lookback-days must be >= {VaRConstants.MinHistoryDays}, got {_lookbackDays}");
            if (_varMode != "single-etf" && _varMode != "multi-stock")
                throw new ArgumentException($"var-mode must be 'single-etf' or 'multi-stock', got '{_varMode}'");
            if (StartDate < new DateTime(2006, 10, 8))
                throw new ArgumentException("start-date must be >= 2006-10-08 (first SHIBOR)");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }
    }
}
