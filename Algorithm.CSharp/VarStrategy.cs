// file: Algorithm.CSharp/VarStrategy.cs
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Risk;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// VaR three-layer pipeline strategy (Layer 3).
    /// Two modes via config "var-mode": "single-etf" (510050/510300/510500) or "multi-stock" (CSI300 subset).
    /// </summary>
    public class VarStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
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
            // Read start/end dates from parameters (allows quick-test configs)
            var startY = int.Parse(GetParameterOrDefault("start-year", "2018"));
            var startM = int.Parse(GetParameterOrDefault("start-month", "1"));
            var startD = int.Parse(GetParameterOrDefault("start-day", "1"));
            var endY = int.Parse(GetParameterOrDefault("end-year", "2025"));
            var endM = int.Parse(GetParameterOrDefault("end-month", "12"));
            var endD = int.Parse(GetParameterOrDefault("end-day", "31"));
            SetStartDate(startY, startM, startD);
            SetEndDate(endY, endM, endD);
            // Minimal-currency approach: keep USD as account currency (LEAN default).
            // We don't trade FX and have no USDCNY data, so currency labels are irrelevant —
            // only numerical values matter. Add CNY to CashBook at 1:1 fixed rate so any
            // CNY-flavored cash flow (dividends, native fees) converts cleanly without
            // crashing BuyingPowerModel.ConvertToAccountCurrency. This is the "USD==CNY 1:1"
            // trick: zero LEAN-native changes, zero FX data dependencies.
            SetCash(1000000);
            Portfolio.CashBook.Add(Currencies.CNY, 0m, 1.0m);
            SetTimeZone("Asia/Shanghai");
            SetBenchmark(x => 0);

            // Disable minimum order size check to allow small positions
            Settings.MinimumOrderMarginPortfolioPercentage = 0;

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
                // IMPORTANT: Fee model MUST return CNY (account currency) fees.
                // ConstantFeeModel defaults to USD which crashes the CNY cash book
                // during BuyingPowerModel.ConvertToAccountCurrency.
                eq.SetFeeModel(new QuantConnect.Orders.Fees.ConstantFeeModel(5m, Currencies.CNY));
            }

            SetAlpha(new VarAlphaModel(_varFactor, _lookbackDays));
            SetPortfolioConstruction(new EqualWeightPortfolioModel());
            SetExecution(new ImmediateExecutionModel());

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

            // Throttle: compute VaR once per calendar month (first trading day in days 3-7).
            // Plan §7 requires SetRuntimeStatistic + Plot in OnData; monthly cadence keeps
            // the 8-year backtest tractable while preserving the full VaR series for plotting.
            if (Time.Day > 7 || Time.Day < 3) return;
            if (_lastVaRMonth == Time.Month) return;
            _lastVaRMonth = Time.Month;

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

        private int _lastVaRMonth = -1;

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
            if (EndDate <= StartDate)
                throw new ArgumentException($"end-date must be after start-date; got {StartDate:yyyy-MM-dd} to {EndDate:yyyy-MM-dd}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }

        /// <summary>IOptimizableStrategy: 与 manifest.parameter_space 一致 (manifest_lint 校验).</summary>
        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "var-budget", "var-lookback-days", "risk-max-drawdown", "risk-max-position-weight"
        };

        /// <summary>IRlStateExportable: 序列化 RL 状态 JSON, 字段须与 manifest.state_schema 一致.</summary>
        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new {
                    sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv,
                    pnl_1d = 0m, days_held = 0
                }).ToList();
            return JsonConvert.SerializeObject(new {
                ts = algo.Time.ToString("o"), strategy = "VarStrategy",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                var_1d99 = 0m, var_regime = 0m, drawdown = 0m, n_open_positions = positions.Count
            });
        }
    }
}
