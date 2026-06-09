/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
*/

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data;
using QuantConnect.Data.Custom;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Barra CNE5 V4 strategy — LEAN native five-layer architecture.
    /// Universe Selection → Alpha → Portfolio Construction (InsightWeighting) → Risk Management → Execution.
    /// Uses the same 15 tushare factor fields as V3.2, but fully follows LEAN's framework.
    /// V4 is completely independent from V2/V2.1/V3/V3.2.
    /// </summary>
    public class AShareBarraCNE5V4Algorithm : QCAlgorithm
    {
        private readonly Dictionary<Symbol, Symbol> _factorToUnderlying = new();
        private AShareBarraCNE5V4AlphaModel _alphaModel;
        private Symbol _anchorSymbol;
        private decimal _previousEquity;
        private string _factorDataPath;
        private string _rebalanceFrequency;
        private int _topN;
        private decimal _targetPortfolioExposure;
        private int _minListedDays;
        private int _maxMissingFactorCount;
        private decimal _minTurnoverRate;
        private decimal? _minTotalMv;
        private decimal _minScoreSpread;
        private int _minimumPresentFactors;

        // Portfolio construction parameters
        private double _blDelta;
        private double _blTau;
        private int _blLookback;
        private int _blPeriod;

        // Risk parameters
        private decimal _stopLossPct;
        private decimal _profitActivationPct;
        private decimal _trailingStopPct;
        private int _cooldownDays;
        private int _minHoldDaysForProfitProtection;
        private decimal _maxSingleWeight;

        // Sharpe exposure scaling
        private decimal _sharpeExposureBase;
        private decimal _sharpeExposureSensitivity;
        private int _sharpeLookbackDays;
        private decimal _exposureSmoothingAlpha;
        private decimal _exposureMinScale;
        private decimal _exposureMaxScale;

        // Vol targeting
        private bool _volTargetEnabled;
        private decimal _volTargetAnnual;
        private int _volTargetLookbackDays;
        private decimal _volTargetFloorScale;
        private decimal _volTargetCapScale;

        // Regime switching
        private bool _regimeSwitchingEnabled;
        private int _regimeVolLookbackDays;
        private decimal _regimeLowVolThreshold;
        private decimal _regimeHighVolThreshold;
        private decimal _regimeTransitionAlpha;

        // IC/IR
        private int _icLookbackPeriods;
        private int _icMinObservations;
        private decimal _irSensitivity;

        // Stratified selection
        private bool _stratifiedSelectionEnabled;
        private string _industryClassificationPath;

        // Factor weights (mid_vol defaults)
        private decimal _betaWeight;
        private decimal _momentumWeight;
        private decimal _sizeWeight;
        private decimal _earningsYieldWeight;
        private decimal _residualVolatilityWeight;
        private decimal _growthWeight;
        private decimal _bookToPriceWeight;
        private decimal _leverageWeight;
        private decimal _liquidityWeight;
        private decimal _nonLinearSizeWeight;
        private decimal _moneyFlowWeight;
        private decimal _qualityWeight;
        private decimal _northboundWeight;
        private decimal _marginWeight;
        private decimal _chipCostWeight;

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2020, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            var initialCash = GetDecimalParameter("initial-cash", 1000000m);

            _factorDataPath = ResolveFactorDataPath(GetParameter("factor-data-path"));
            _rebalanceFrequency = (GetParameter("rebalance-frequency") ?? "monthly").Trim().ToLowerInvariant();
            _topN = GetIntParameter("top-n", 25);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.5m);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.90m);
            _minListedDays = GetIntParameter("min-listed-days", 250);
            _maxMissingFactorCount = GetIntParameter("max-missing-factor-count", 3);
            _minTurnoverRate = GetDecimalParameter("min-turnover-rate", 0m);
            _minTotalMv = GetOptionalDecimalParameter("min-total-mv");
            _minimumPresentFactors = GetIntParameter("minimum-present-factors", 8);

            // Portfolio construction parameters (reserved for future BL upgrade)
            _blDelta = (double)GetDecimalParameter("black-litterman-delta", 2.2m);
            _blTau = (double)GetDecimalParameter("black-litterman-tau", 0.05m);
            _blLookback = GetIntParameter("black-litterman-lookback", 1);
            _blPeriod = GetIntParameter("black-litterman-period", 63);

            // Risk parameters
            _stopLossPct = GetDecimalParameter("stop-loss-pct", 0.12m);
            _profitActivationPct = GetDecimalParameter("take-profit-activation-pct", 0.18m);
            _trailingStopPct = GetDecimalParameter("trailing-stop-pct", 0.08m);
            _cooldownDays = GetIntParameter("risk-exit-cooldown-days", 5);
            _minHoldDaysForProfitProtection = GetIntParameter("min-hold-days-for-profit-protection", 2);
            _maxSingleWeight = GetDecimalParameter("max-single-weight", 0.10m);

            // Sharpe exposure scaling
            _sharpeExposureBase = GetDecimalParameter("sharpe-exposure-base", 0.70m);
            _sharpeExposureSensitivity = GetDecimalParameter("sharpe-exposure-sensitivity", 0.25m);
            _sharpeLookbackDays = GetIntParameter("sharpe-lookback-days", 63);
            _exposureSmoothingAlpha = GetDecimalParameter("exposure-smoothing-alpha", 0.20m);
            _exposureMinScale = GetDecimalParameter("exposure-min-scale", 0.50m);
            _exposureMaxScale = GetDecimalParameter("exposure-max-scale", 1.00m);

            // Vol targeting
            _volTargetEnabled = GetBoolParameter("vol-target-enabled", true);
            _volTargetAnnual = GetDecimalParameter("vol-target-annual", 0.15m);
            _volTargetLookbackDays = GetIntParameter("vol-target-lookback-days", 20);
            _volTargetFloorScale = GetDecimalParameter("vol-target-floor-scale", 0.50m);
            _volTargetCapScale = GetDecimalParameter("vol-target-cap-scale", 1.50m);

            // Regime switching
            _regimeSwitchingEnabled = GetBoolParameter("regime-switching-enabled", true);
            _regimeVolLookbackDays = GetIntParameter("regime-vol-lookback-days", 20);
            _regimeLowVolThreshold = GetDecimalParameter("regime-low-vol-threshold", 0.15m);
            _regimeHighVolThreshold = GetDecimalParameter("regime-high-vol-threshold", 0.25m);
            _regimeTransitionAlpha = GetDecimalParameter("regime-transition-alpha", 0.30m);

            // IC/IR
            _icLookbackPeriods = GetIntParameter("ic-lookback-periods", 60);
            _icMinObservations = GetIntParameter("ic-min-observations", 12);
            _irSensitivity = GetDecimalParameter("ir-sensitivity", 0.50m);

            // Stratified selection
            _stratifiedSelectionEnabled = GetBoolParameter("stratified-selection-enabled", true);
            _industryClassificationPath = GetParameter("industry-classification-path") ?? "local_data/industry_classification/ashare_sw31_classification.csv";

            // Factor weights
            _betaWeight = GetDecimalParameter("factor-weight-beta", -0.05m);
            _momentumWeight = GetDecimalParameter("factor-weight-momentum", 0.25m);
            _sizeWeight = GetDecimalParameter("factor-weight-size", -0.05m);
            _earningsYieldWeight = GetDecimalParameter("factor-weight-earnyld", 0.20m);
            _residualVolatilityWeight = GetDecimalParameter("factor-weight-resvol", -0.10m);
            _growthWeight = GetDecimalParameter("factor-weight-growth", 0.15m);
            _bookToPriceWeight = GetDecimalParameter("factor-weight-btop", 0.10m);
            _leverageWeight = GetDecimalParameter("factor-weight-leverage", -0.05m);
            _liquidityWeight = GetDecimalParameter("factor-weight-liquidity", 0.05m);
            _nonLinearSizeWeight = GetDecimalParameter("factor-weight-nlsize", 0.00m);
            _moneyFlowWeight = GetDecimalParameter("factor-weight-moneyflow", 0.10m);
            _qualityWeight = GetDecimalParameter("factor-weight-quality", 0.20m);
            _northboundWeight = GetDecimalParameter("factor-weight-northbound", 0.08m);
            _marginWeight = GetDecimalParameter("factor-weight-margin", 0.05m);
            _chipCostWeight = GetDecimalParameter("factor-weight-chipcost", 0.07m);

            SetStartDate(startDate);
            SetEndDate(endDate);
            SetAccountCurrency(Currencies.CNY);
            SetCash(initialCash);
            SetBenchmark(_ => 0m);
            SetRiskFreeInterestRateModel(new ChinaInterestRateProvider());
            _previousEquity = initialCash;

            AShareBarraCNE5V2FactorData.SetBaseDirectory(_factorDataPath);

            // Discover universe from factor data directory
            var eligibleSymbols = new HashSet<Symbol>();
            foreach (var underlying in DiscoverUniverse())
            {
                if (!HasRequiredLocalPriceData(underlying))
                {
                    Log($"Skipping {underlying.Value}: missing local A-share daily price data");
                    continue;
                }

                var equity = AddEquity(underlying.Value, Resolution.Daily, underlying.ID.Market);
                equity.FeeModel = new AShareStockFeeModel();
                equity.FillModel = new AShareStockFillModel();
                equity.BuyingPowerModel = new AShareStockBuyingPowerModel();
                equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                equity.PortfolioModel = new AShareT1PortfolioModel();
                equity.Holdings = new AShareT1Holding(equity, Portfolio.CashBook);
                equity.Session.Size = 2;

                var factorSecurity = AddData<AShareBarraCNE5V2FactorData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _factorToUnderlying[factorSecurity.Symbol] = equity.Symbol;
                eligibleSymbols.Add(equity.Symbol);
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No Barra CNE5 V4 symbols configured from factor path {_factorDataPath}");
            }

            // Resolve industry classification path for alpha model
            var resolvedIndustryPath = ResolveOptionalDataPath(_industryClassificationPath);

            // === LEAN Native Five-Layer Architecture ===

            // 1. Universe Selection — Manual (symbols already discovered and subscribed)
            SetUniverseSelection(new ManualUniverseSelectionModel(eligibleSymbols));

            // 2. Alpha Model
            _alphaModel = new AShareBarraCNE5V4AlphaModel(
                topN: _topN,
                minScoreSpread: _minScoreSpread,
                rebalanceFrequency: _rebalanceFrequency,
                minimumPresentFactors: _minimumPresentFactors,
                minListedDays: _minListedDays,
                maxMissingFactorCount: _maxMissingFactorCount,
                minTurnoverRate: _minTurnoverRate,
                minTotalMv: _minTotalMv,
                betaWeight: _betaWeight,
                momentumWeight: _momentumWeight,
                sizeWeight: _sizeWeight,
                earningsYieldWeight: _earningsYieldWeight,
                residualVolatilityWeight: _residualVolatilityWeight,
                growthWeight: _growthWeight,
                bookToPriceWeight: _bookToPriceWeight,
                leverageWeight: _leverageWeight,
                liquidityWeight: _liquidityWeight,
                nonLinearSizeWeight: _nonLinearSizeWeight,
                moneyFlowWeight: _moneyFlowWeight,
                qualityWeight: _qualityWeight,
                northboundWeight: _northboundWeight,
                marginWeight: _marginWeight,
                chipCostWeight: _chipCostWeight,
                regimeSwitchingEnabled: _regimeSwitchingEnabled,
                regimeVolLookbackDays: _regimeVolLookbackDays,
                regimeLowVolThreshold: _regimeLowVolThreshold,
                regimeHighVolThreshold: _regimeHighVolThreshold,
                regimeTransitionAlpha: _regimeTransitionAlpha,
                icLookbackPeriods: _icLookbackPeriods,
                icMinObservations: _icMinObservations,
                irSensitivity: _irSensitivity,
                stratifiedSelectionEnabled: _stratifiedSelectionEnabled,
                industryClassificationPath: resolvedIndustryPath
            );
            SetAlpha(_alphaModel);

            // 3. Portfolio Construction — Fixed Black-Litterman (LEAN native + dimension safety)
            // Uses FixedBlackLittermanPortfolioConstructionModel which handles the dimension mismatch
            // between FormReturnsMatrix output and symbol list that causes IndexOutOfRangeException
            // in the upstream BlackLittermanOptimizationPortfolioConstructionModel.
            SetPortfolioConstruction(new FixedBlackLittermanPortfolioConstructionModel(
                TimeSpan.FromDays(ResolveRebalanceDays(_rebalanceFrequency)),
                PortfolioBias.Long,
                lookback: _blLookback,
                period: _blPeriod,
                resolution: Resolution.Daily,
                riskFreeRate: 0.03,
                delta: _blDelta,
                tau: _blTau
            ));

            // 4. Risk Management — LEAN native composite model
            // Replaces custom model whose Sharpe/vol scaling caused chronic under-investment
            // and whose stop-loss never triggered across 100 rebalances.
            SetRiskManagement(new CompositeRiskManagementModel(
                new TrailingStopRiskManagementModel(0.15m),                   // 15% trailing stop per security
                new MaximumDrawdownPercentPerSecurity(0.10m),                  // 10% max loss per security
                new MaximumDrawdownPercentPortfolio(0.20m, isTrailing: true)   // 20% trailing portfolio drawdown circuit breaker
            ));

            // 5. Execution — A-share lot size aware (rounds to 100-share lots)
            SetExecution(new AShareLotSizeExecutionModel());

            Log($"AShareBarraCNE5V4Algorithm initialized with {_factorToUnderlying.Count} factor subscriptions");
            Log("Architecture: LEAN native five-layer (Universe → Alpha → BL Portfolio → Composite Risk → Execution)");
            Log($"Portfolio: BlackLitterman delta={_blDelta} tau={_blTau} lookback={_blLookback} period={_blPeriod}");
            Log($"Risk: TrailingStop=15% MaxDrawdownPerSecurity=10% MaxDrawdownPortfolio=20%(trailing)");
            Log($"Regime: enabled={_regimeSwitchingEnabled} low={_regimeLowVolThreshold:P0} high={_regimeHighVolThreshold:P0}");
            Log($"Selection: topN={_topN} stratified={_stratifiedSelectionEnabled} minSpread={_minScoreSpread:F2}");
        }

        public override void OnData(Slice slice)
        {
            var sessionDate = DateTime.MinValue;

            // Feed factor data to AlphaModel
            foreach (var pair in slice.Get<AShareBarraCNE5V2FactorData>())
            {
                if (pair.Value == null) continue;
                if (_factorToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _alphaModel.UpdateFactorData(underlying, pair.Value);
                    sessionDate = pair.Value.EndTime.Date > sessionDate ? pair.Value.EndTime.Date : sessionDate;
                }
            }

            // Track daily returns for AlphaModel's regime/IC computation
            if (sessionDate != DateTime.MinValue)
            {
                var currentEquity = Portfolio.TotalPortfolioValue;
                if (_previousEquity > 0m)
                {
                    var dailyReturn = currentEquity / _previousEquity - 1m;
                    _alphaModel.RecordDailyReturn(dailyReturn, sessionDate);
                }

                // Track per-stock returns for IC computation
                var stockReturns = new Dictionary<Symbol, decimal>();
                foreach (var symbol in _factorToUnderlying.Values)
                {
                    if (Securities.TryGetValue(symbol, out var security) && security.Price > 0m)
                    {
                        var history = History(symbol, 2, Resolution.Daily);
                        var histList = history.ToList();
                        if (histList.Count >= 2)
                        {
                            var prevClose = histList[histList.Count - 2].Close;
                            var currClose = histList[histList.Count - 1].Close;
                            if (prevClose > 0m)
                            {
                                stockReturns[symbol] = currClose / prevClose - 1m;
                            }
                        }
                    }
                }
                _alphaModel.RecordStockReturns(stockReturns);

                _previousEquity = currentEquity;

                // Update runtime statistics
                UpdateRuntimeStatistics();
            }
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status != OrderStatus.Filled) return;
            Log(
                $"[order filled] symbol={ToTsCode(orderEvent.Symbol)} action={(orderEvent.FillQuantity > 0 ? "BUY" : "SELL")} " +
                $"quantity={Math.Abs(orderEvent.FillQuantity)} price={orderEvent.FillPrice:F4} " +
                $"order_id={orderEvent.OrderId} tag={orderEvent.Message ?? ""}");
        }

        private void UpdateRuntimeStatistics()
        {
            var exposure = _alphaModel.GetLatestExposure();
            var investedCount = Securities.Values.Count(s => s.Holdings.Invested);

            SetRuntimeStatistic("Holdings", investedCount.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Regime", _alphaModel.GetCurrentRegime());
            SetRuntimeStatistic("Return", (Portfolio.TotalPortfolioValue / _previousEquity - 1m).ToString("F4", CultureInfo.InvariantCulture));

            SetRuntimeStatistic("FX.beta", exposure["beta"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.momentum", exposure["momentum"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.size", exposure["size"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.earnyld", exposure["earnyld"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.resvol", exposure["resvol"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.growth", exposure["growth"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.btop", exposure["btop"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.leverage", exposure["leverage"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.liquidity", exposure["liquidity"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.nlsize", exposure["nlsize"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.moneyflow", exposure["moneyflow"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.quality", exposure["quality"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.northbound", exposure["northbound"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.margin", exposure["margin"].ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("FX.chipcost", exposure["chipcost"].ToString("F4", CultureInfo.InvariantCulture));
        }

        #region Utility Methods

        private IEnumerable<Symbol> DiscoverUniverse()
        {
            var universe = new HashSet<Symbol>();
            if (string.IsNullOrWhiteSpace(_factorDataPath) || !Directory.Exists(_factorDataPath)) return universe;

            foreach (var marketDir in Directory.EnumerateDirectories(_factorDataPath))
            {
                var marketName = Path.GetFileName(marketDir)?.ToLowerInvariant();
                if (string.IsNullOrWhiteSpace(marketName)) continue;
                var dailyDir = Path.Combine(marketDir, "daily");
                if (!Directory.Exists(dailyDir)) continue;

                foreach (var factorFile in Directory.EnumerateFiles(dailyDir, "*.csv"))
                {
                    var ticker = Path.GetFileNameWithoutExtension(factorFile);
                    if (string.IsNullOrWhiteSpace(ticker)) continue;
                    var market = marketName.Equals("sse", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
                    var barraSymbol = QuantConnect.Symbol.Create(ticker, SecurityType.Equity, market);
                    universe.Add(barraSymbol);
                }
            }
            return universe;
        }

        private bool HasRequiredLocalPriceData(Symbol symbol)
        {
            return File.Exists(ResolveLocalPricePath(symbol));
        }

        private string ResolveLocalPricePath(Symbol symbol)
        {
            var market = symbol.ID.Market.ToLowerInvariant();
            return Path.Combine(Globals.DataFolder, "equity", market, "daily", $"{symbol.Value}.csv");
        }

        private string ResolveFactorDataPath(string parameterValue)
        {
            if (string.IsNullOrWhiteSpace(parameterValue))
                return Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-factors");
            if (Path.IsPathRooted(parameterValue))
                return Path.GetFullPath(parameterValue);
            return Path.Combine(Globals.DataFolder, parameterValue);
        }

        private string ResolveOptionalDataPath(string relativePath)
        {
            if (string.IsNullOrWhiteSpace(relativePath)) return relativePath;
            if (Path.IsPathRooted(relativePath)) return relativePath;
            return Path.Combine(Globals.DataFolder, relativePath);
        }

        private string ToTsCode(Symbol symbol)
        {
            var market = symbol.ID.Market.ToUpperInvariant();
            var suffix = market == "SSE" ? ".SH" : ".SZ";
            return $"{symbol.Value}{suffix}";
        }

        private static int ResolveRebalanceDays(string frequency)
        {
            return frequency?.Trim().ToLowerInvariant() switch
            {
                "weekly" => 7,
                "biweekly" => 14,
                _ => 30
            };
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return DateTime.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed) ? parsed : defaultValue;
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : defaultValue;
        }

        private decimal? GetOptionalDecimalParameter(string name)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return null;
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : null;
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : defaultValue;
        }

        private bool GetBoolParameter(string name, bool defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return value.Trim().Equals("true", StringComparison.OrdinalIgnoreCase) || value.Trim().Equals("1", StringComparison.OrdinalIgnoreCase);
        }

        #endregion
    }
}
