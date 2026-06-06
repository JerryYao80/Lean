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
using System.Text;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Custom;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Barra CNE5 V3.2 strategy — extends V2.1 with regime switching, IC/IR factor calibration,
    /// vol targeting, turnover constraint, and industry-neutral stratified selection.
    /// Same 15 factors as V2/V2.1, with dynamic weights adapting to market conditions.
    /// All features are config-toggleable. V3.2 is fully independent from V2/V2.1.
    /// </summary>
    public class AShareBarraCNE5V3_2Algorithm : QCAlgorithm
    {
        private readonly Dictionary<Symbol, Symbol> _factorToUnderlying = new();
        private readonly Dictionary<Symbol, AShareBarraCNE5V2FactorData> _latestFactorsByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestScoresByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestTargetWeightsByUnderlying = new();
        private readonly Dictionary<Symbol, DateTime> _riskCooldownUntilBySymbol = new();
        private readonly Dictionary<Symbol, decimal> _peakPriceBySymbol = new();
        private readonly Dictionary<Symbol, int> _holdingDaysBySymbol = new();
        private readonly List<FactorExposureV3_2Row> _factorExposureRows = new();
        private readonly List<decimal> _dailyReturns = new();
        private readonly List<DateTime> _dailyReturnDates = new();
        private readonly List<DailySummaryV3_2Row> _dailySummaryRows = new();
        private readonly List<TradeRowV3_2> _tradeRows = new();

        private Symbol _anchorSymbol;
        private string _factorDataPath;
        private string _fallbackFactorDataPath;
        private string _livePriceSnapshotPath;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _allocationReportPath;
        private string _factorExposureReportPath;
        private string _portfolioStatePath;
        private string _monteCarloReportPath;
        private string _rebalanceFrequency;
        private decimal _targetPortfolioExposure;
        private decimal _minScoreSpread;
        private decimal _minTurnoverRate;
        private decimal? _minTotalMv;
        private decimal _exposureMinScale;
        private decimal _exposureMaxScale;
        private decimal _stopLossPct;
        private decimal _profitActivationPct;
        private decimal _trailingStopPct;
        private int _topN;
        private int _minListedDays;
        private int _maxMissingFactorCount;
        private int _riskExitCooldownDays;
        private int _minHoldDaysForProfitProtection;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private int _rebalanceCount;
        private DateTime _lastProcessedDate;
        private DateTime _lastSignalEvaluationDate;
        private DateTime _lastRebalanceDate;
        private DateTime _lastLiveProcessingTime;
        private DateTime _lastPortfolioLogUtc;
        private DateTime _lastRuntimePersistUtc;
        private DateTime _lastLiveFactorDiskRefreshDate;
        private DateTime _liveSnapshotLastWriteTimeUtc;
        private bool _monteCarloEnabled;
        private bool _oosEnabled;
        private decimal _monteCarloFactorPerturbationScale;
        private decimal _latestScoreSpread;
        private decimal _latestExposureScale;
        private decimal _latestEffectiveTargetExposure;
        private decimal _previousEquity;

        // Sharpe-based exposure scaling parameters
        private decimal _sharpeExposureBase;
        private decimal _sharpeExposureSensitivity;
        private int _sharpeLookbackDays;
        private decimal _exposureSmoothingAlpha;
        private decimal _smoothedExposureScale;

        // OOS testing parameters
        private DateTime _oosTrainingStartDate;
        private DateTime _oosTrainingEndDate;
        private DateTime _oosTestStartDate;
        private bool _inOosTestPeriod;
        private AShareBarraCNE5V3_2SignalSettings _trainingPeriodSettings;
        private AShareBarraCNE5V3_2SignalSettings _signalSettings;

        // V3: Regime switching
        private bool _regimeSwitchingEnabled;
        private int _regimeVolLookbackDays;
        private decimal _regimeLowVolThreshold;
        private decimal _regimeHighVolThreshold;
        private string _currentRegime;
        private decimal _regimeTransitionAlpha;

        // V3: Vol targeting
        private bool _volTargetEnabled;
        private decimal _volTargetAnnual;
        private int _volTargetLookbackDays;
        private decimal _volTargetFloorScale;
        private decimal _volTargetCapScale;
        private decimal _currentVolScale;

        // V3: Turnover constraint
        private bool _turnoverConstraintEnabled;
        private decimal _maxTurnover;
        private Dictionary<Symbol, decimal> _previousWeights = new();

        // V3: IC/IR tracking
        private Dictionary<string, List<decimal>> _factorICHistory = new();
        private Dictionary<Symbol, decimal> _prevPeriodFactorScores = new();
        private Dictionary<Symbol, decimal> _prevPeriodStockReturns = new();
        private int _icLookbackPeriods;
        private int _icMinObservations;
        private decimal _irSensitivity;

        // V3: Industry stratification
        private bool _stratifiedSelectionEnabled;
        private Dictionary<Symbol, string> _symbolIndustryMap = new();
        private string _industryClassificationPath;

        private Dictionary<Symbol, decimal> _liveSnapshotPricesBySymbol = new();
        private TimeSpan _liveSignalInterval = TimeSpan.FromMinutes(3);
        private TimeSpan _livePriceSyncInterval = TimeSpan.FromMinutes(1);

        // Monte Carlo state
        private Random _monteCarloRandom;
        private List<decimal[]> _historicalFactorReturns;

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2020, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            var initialCash = GetDecimalParameter("initial-cash", 1000000m);

            _factorDataPath = ResolveFactorDataPath(GetParameter("factor-data-path"));
            _fallbackFactorDataPath = ResolveOptionalFactorDataPath(GetParameter("external-factor-path"));
            _livePriceSnapshotPath = ResolveOutputPath(GetParameter("live-price-snapshot-file"), "barra-cne5v3-2-live-price-snapshot.json");
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "barra-cne5v3-2-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "barra-cne5v3-2-daily-summary.csv");
            _allocationReportPath = ResolveOutputPath(GetParameter("allocation-report-file"), "barra-cne5v3-2-allocation.csv");
            _factorExposureReportPath = ResolveOutputPath(GetParameter("factor-exposure-file"), "barra-cne5v3-2-factor-exposure.csv");
            _portfolioStatePath = ResolveOutputPath(GetParameter("portfolio-state-file"), "barra-cne5v3-2-live-state.json");
            _monteCarloReportPath = ResolveOutputPath(GetParameter("monte-carlo-file"), "barra-cne5v3-2-monte-carlo.csv");
            _rebalanceFrequency = (GetParameter("rebalance-frequency") ?? "monthly").Trim().ToLowerInvariant();
            _topN = GetIntParameter("top-n", 25);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.5m);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.90m);
            _minListedDays = GetIntParameter("min-listed-days", 250);
            _maxMissingFactorCount = GetIntParameter("max-missing-factor-count", 3);
            _minTurnoverRate = GetDecimalParameter("min-turnover-rate", 0m);
            _minTotalMv = GetOptionalDecimalParameter("min-total-mv");

            // Sharpe-based exposure scaling (replaces Kelly)
            // V3.2 fix: Higher Sharpe base (0.70 vs 0.50) to avoid death spiral
            _sharpeExposureBase = GetDecimalParameter("sharpe-exposure-base", 0.70m);
            _sharpeExposureSensitivity = GetDecimalParameter("sharpe-exposure-sensitivity", 0.25m);
            _sharpeLookbackDays = GetIntParameter("sharpe-lookback-days", 63);
            _exposureSmoothingAlpha = GetDecimalParameter("exposure-smoothing-alpha", 0.20m);
            // V3.2 fix: Higher min scale (0.50 vs 0.40) to prevent excessive de-risking
            _exposureMinScale = GetDecimalParameter("kelly-min-scale", 0.50m);
            _exposureMaxScale = GetDecimalParameter("kelly-max-scale", 1.00m);
            _smoothedExposureScale = _sharpeExposureBase;

            // V3: Regime switching
            _regimeSwitchingEnabled = GetBoolParameter("regime-switching-enabled", true);
            _regimeVolLookbackDays = GetIntParameter("regime-vol-lookback-days", 20);
            _regimeLowVolThreshold = GetDecimalParameter("regime-low-vol-threshold", 0.15m);
            _regimeHighVolThreshold = GetDecimalParameter("regime-high-vol-threshold", 0.25m);
            _regimeTransitionAlpha = GetDecimalParameter("regime-transition-alpha", 0.30m);
            _currentRegime = "mid_vol";

            // V3: Vol targeting
            _volTargetEnabled = GetBoolParameter("vol-target-enabled", true);
            _volTargetAnnual = GetDecimalParameter("vol-target-annual", 0.15m);
            _volTargetLookbackDays = GetIntParameter("vol-target-lookback-days", 20);
            _volTargetFloorScale = GetDecimalParameter("vol-target-floor-scale", 0.50m);
            _volTargetCapScale = GetDecimalParameter("vol-target-cap-scale", 1.50m);
            _currentVolScale = 1.0m;

            // V3: Turnover constraint
            _turnoverConstraintEnabled = GetBoolParameter("turnover-constraint-enabled", true);
            _maxTurnover = GetDecimalParameter("max-turnover", 0.30m);

            // V3: IC/IR parameters
            _icLookbackPeriods = GetIntParameter("ic-lookback-periods", 60);
            _icMinObservations = GetIntParameter("ic-min-observations", 12);
            _irSensitivity = GetDecimalParameter("ir-sensitivity", 0.50m);

            // V3: Industry stratification
            _stratifiedSelectionEnabled = GetBoolParameter("stratified-selection-enabled", true);
            _industryClassificationPath = GetParameter("industry-classification-path") ?? "local_data/industry_classification/ashare_sw31_classification.csv";

            // Risk management — V2.1 defaults tightened
            // V3.2 fix: Relaxed stop-loss (12% vs 8% in V3) to reduce premature exits
            _stopLossPct = GetDecimalParameter("stop-loss-pct", 0.12m);
            // V3.2 fix: Wider profit activation (18% vs 12% in V3)
            _profitActivationPct = GetDecimalParameter("take-profit-activation-pct", 0.18m);
            // V3.2 fix: Wider trailing stop (8% vs 5% in V3)
            _trailingStopPct = GetDecimalParameter("trailing-stop-pct", 0.08m);
            _riskExitCooldownDays = GetIntParameter("risk-exit-cooldown-days", 5);
            _minHoldDaysForProfitProtection = GetIntParameter("min-hold-days-for-profit-protection", 2);

            _signalSettings = new AShareBarraCNE5V3_2SignalSettings
            {
                BetaWeight = GetDecimalParameter("factor-weight-beta", -0.05m),
                MomentumWeight = GetDecimalParameter("factor-weight-momentum", 0.25m),
                SizeWeight = GetDecimalParameter("factor-weight-size", -0.05m),
                EarningsYieldWeight = GetDecimalParameter("factor-weight-earnyld", 0.20m),
                ResidualVolatilityWeight = GetDecimalParameter("factor-weight-resvol", -0.10m),
                GrowthWeight = GetDecimalParameter("factor-weight-growth", 0.15m),
                BookToPriceWeight = GetDecimalParameter("factor-weight-btop", 0.10m),
                LeverageWeight = GetDecimalParameter("factor-weight-leverage", -0.05m),
                LiquidityWeight = GetDecimalParameter("factor-weight-liquidity", 0.05m),
                NonLinearSizeWeight = GetDecimalParameter("factor-weight-nlsize", 0.00m),
                MoneyFlowWeight = GetDecimalParameter("factor-weight-moneyflow", 0.10m),
                QualityWeight = GetDecimalParameter("factor-weight-quality", 0.20m),
                NorthboundWeight = GetDecimalParameter("factor-weight-northbound", 0.08m),
                MarginWeight = GetDecimalParameter("factor-weight-margin", 0.05m),
                ChipCostWeight = GetDecimalParameter("factor-weight-chipcost", 0.07m),
                MinimumPresentFactors = GetIntParameter("minimum-present-factors", 8),
                WeightingMode = (GetParameter("weighting-mode") ?? "black-litterman").Trim(),
                BlackLittermanTau = GetDecimalParameter("black-litterman-tau", 0.05m),
                BlackLittermanRiskAversion = GetDecimalParameter("black-litterman-risk-aversion", 2.20m),
                BlackLittermanViewScale = GetDecimalParameter("black-litterman-view-scale", 0.08m),
                BlackLittermanViewConfidence = GetDecimalParameter("black-litterman-view-confidence", 0.65m),
                BlackLittermanPriorBlend = GetDecimalParameter("black-litterman-prior-blend", 0.30m),
                KellyWeightFraction = GetDecimalParameter("kelly-weight-fraction", 0.50m),
                KellyVarianceFloor = GetDecimalParameter("kelly-variance-floor", 0.35m),
                KellyResidualVolatilityScale = GetDecimalParameter("kelly-residual-volatility-scale", 0.40m),
                KellyBetaPenaltyScale = GetDecimalParameter("kelly-beta-penalty-scale", 0.10m),
                MaxSingleWeight = GetDecimalParameter("max-single-weight", 0.10m),
                // V3.2 fix: Use equal-weight prior to avoid Size signal contradiction
                UseMarketCapPrior = GetBoolParameter("use-market-cap-prior", false)
            };

            _latestExposureScale = _sharpeExposureBase;
            _latestEffectiveTargetExposure = _targetPortfolioExposure * _latestExposureScale;

            _oosEnabled = GetBoolParameter("oos-enabled", true);
            _oosTrainingStartDate = GetDateParameter("oos-training-start-date", startDate);
            _oosTrainingEndDate = GetDateParameter("oos-training-end-date", new DateTime(2024, 12, 31));
            _oosTestStartDate = GetDateParameter("oos-test-start-date", new DateTime(2025, 1, 1));
            _trainingPeriodSettings = null;
            _inOosTestPeriod = false;

            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", true);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);
            _monteCarloRandom = new Random(_monteCarloSeed);
            _historicalFactorReturns = new List<decimal[]>();

            SetStartDate(startDate);
            SetEndDate(endDate);
            SetAccountCurrency(Currencies.CNY);
            SetCash(initialCash);
            SetBenchmark(_ => 0m);
            SetRiskFreeInterestRateModel(new ChinaInterestRateProvider());
            _previousEquity = initialCash;
            _liveSignalInterval = TimeSpan.FromMinutes(Math.Max(1, GetIntParameter("live-signal-interval-minutes", 3)));
            _livePriceSyncInterval = TimeSpan.FromSeconds(Math.Max(10, GetIntParameter("live-price-poll-interval-seconds", 60)));

            AShareBarraCNE5V2FactorData.SetBaseDirectory(_factorDataPath);

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
                equity.Session.Size = 2;

                var factorSecurity = AddData<AShareBarraCNE5V2FactorData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _factorToUnderlying[factorSecurity.Symbol] = equity.Symbol;
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No Barra CNE5 V3.2 symbols were configured from factor path {_factorDataPath}");
            }

            Log($"AShareBarraCNE5V3_2Algorithm initialized with {_factorToUnderlying.Count} factor subscriptions");
            Log("Execution mode: LEAN native orders (SetHoldings/MarketOrder). All statistics computed by LEAN engine.");
            Log(
                $"Config: factor path={_factorDataPath} fallback={_fallbackFactorDataPath ?? "-"} rebalance={_rebalanceFrequency} " +
                $"topN={_topN} exposure={_targetPortfolioExposure:F2} weighting={_signalSettings.WeightingMode} minScoreSpread={_minScoreSpread:F2}");
            Log(
                $"Sharpe exposure: base={_sharpeExposureBase:F2} sensitivity={_sharpeExposureSensitivity:F2} " +
                $"lookback={_sharpeLookbackDays} smoothing={_exposureSmoothingAlpha:F2} " +
                $"minScale={_exposureMinScale:F2} maxScale={_exposureMaxScale:F2}");
            Log(
                $"Risk overlay: stopLoss={_stopLossPct:P0} takeProfitActivation={_profitActivationPct:P0} " +
                $"trailingStop={_trailingStopPct:P0} cooldownDays={_riskExitCooldownDays}");
            Log(
                $"V3.2 features: regime={_regimeSwitchingEnabled} volTarget={_volTargetEnabled} " +
                $"turnoverConstraint={_turnoverConstraintEnabled} stratified={_stratifiedSelectionEnabled}");
            Log(
                $"Regime: lookback={_regimeVolLookbackDays} low={_regimeLowVolThreshold:P0} high={_regimeHighVolThreshold:P0} alpha={_regimeTransitionAlpha:F2}");
            Log(
                $"VolTarget: annual={_volTargetAnnual:P0} lookback={_volTargetLookbackDays} floor={_volTargetFloorScale:F2} cap={_volTargetCapScale:F2}");
            Log(
                $"IC/IR: lookback={_icLookbackPeriods} minObs={_icMinObservations} sensitivity={_irSensitivity:F2}");
            Log(
                $"Turnover: max={_maxTurnover:P0}");

            if (_oosEnabled)
            {
                Log(
                    $"Out-of-sample testing enabled: training=[{_oosTrainingStartDate:yyyy-MM-dd}, {_oosTrainingEndDate:yyyy-MM-dd}] " +
                    $"test=[{_oosTestStartDate:yyyy-MM-dd}, {endDate:yyyy-MM-dd}]");
            }

            if (_monteCarloEnabled)
            {
                Log(
                    $"Monte Carlo simulation enabled: trials={_monteCarloTrials} horizonDays={_monteCarloHorizonDays} " +
                    $"blockSize={_monteCarloBlockSize} factorScale={_monteCarloFactorPerturbationScale:F2} seed={_monteCarloSeed}");
            }

            if (LiveMode)
            {
                Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.Every(_livePriceSyncInterval), RunLiveMonitoringCycle);
                Log(
                    $"Live monitoring schedule: signal every {_liveSignalInterval.TotalMinutes:F0} minute(s); " +
                    $"snapshot sync every {_livePriceSyncInterval.TotalSeconds:F0} second(s)");
            }

            if (_stratifiedSelectionEnabled)
            {
                LoadIndustryClassification();
            }

            SetRuntimeStatistic("Holdings", "0");
            SetRuntimeStatistic("Exposure", _latestExposureScale.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Target Exp", _latestEffectiveTargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("OOS", _oosEnabled ? "enabled" : "disabled");
            SetRuntimeStatistic("MC", _monteCarloEnabled ? "enabled" : "disabled");
        }

        public override void OnData(Slice slice)
        {
            var sessionDate = DateTime.MinValue;
            foreach (var pair in slice.Get<AShareBarraCNE5V2FactorData>())
            {
                if (pair.Value == null) continue;
                if (_factorToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _latestFactorsByUnderlying[underlying] = pair.Value;
                    sessionDate = pair.Value.EndTime.Date > sessionDate ? pair.Value.EndTime.Date : sessionDate;
                }
            }

            if (sessionDate == DateTime.MinValue && _anchorSymbol != null && slice.Bars.TryGetValue(_anchorSymbol, out var anchorBar))
            {
                sessionDate = anchorBar.EndTime.Date;
            }

            if (sessionDate != DateTime.MinValue)
            {
                if (LiveMode)
                {
                    EnsureLiveFactorSnapshots(sessionDate.Date);
                    if (!ShouldRunLiveProcessingCycle(sessionDate.Date)) return;
                }
                ProcessSession(sessionDate.Date);
            }
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status != OrderStatus.Filled) return;

            var symbol = orderEvent.Symbol;
            var fillPrice = orderEvent.FillPrice;
            var quantity = orderEvent.FillQuantity;
            var absoluteQuantity = Math.Abs(quantity);
            var isBuy = quantity > 0;

            if (isBuy)
            {
                if (!_peakPriceBySymbol.ContainsKey(symbol) || fillPrice > _peakPriceBySymbol[symbol])
                {
                    _peakPriceBySymbol[symbol] = fillPrice;
                }
                _holdingDaysBySymbol[symbol] = 0;
                _riskCooldownUntilBySymbol.Remove(symbol);
            }

            // Record trade for CSV output
            var security = Securities[symbol];
            var tradeValue = absoluteQuantity * fillPrice;
            var fee = security.Holdings.LastTradeProfit != 0m ? 0m : 0m; // LEAN handles fees internally
            var tag = orderEvent.Message ?? string.Empty;
            var score = 0m;
            // Extract score from tag if present (e.g. "REBALANCE 20250103 score=0.7074")
            var scoreMatch = System.Text.RegularExpressions.Regex.Match(tag, @"score=([0-9.]+)");
            if (scoreMatch.Success && decimal.TryParse(scoreMatch.Groups[1].Value, out var parsedScore))
            {
                score = parsedScore;
            }
            var reason = tag.Split(' ')[0];

            _tradeRows.Add(new TradeRowV3_2
            {
                TradeDate = Time.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                ExecutedAt = Time.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture),
                Symbol = ToTsCode(symbol),
                Action = isBuy ? "BUY" : "SELL",
                Quantity = (int)absoluteQuantity,
                Price = fillPrice,
                TradeValue = tradeValue,
                Fee = fee,
                Score = score,
                Reason = reason
            });

            Log(
                $"[order filled] symbol={ToTsCode(symbol)} action={(isBuy ? "BUY" : "SELL")} " +
                $"quantity={absoluteQuantity} price={fillPrice:F4} " +
                $"order_id={orderEvent.OrderId} tag={tag}");
        }

        public override void OnEndOfAlgorithm()
        {
            if (_monteCarloEnabled && _dailyReturns.Count > _monteCarloBlockSize)
            {
                RunMonteCarloSimulation();
            }

            if (_oosEnabled && _trainingPeriodSettings != null)
            {
                Log($"[OOS] Training period factor weights calibrated and applied to test period");
            }

            PersistOutputs();
            Log($"Saved Barra V3.2.2 outputs: trades={_tradeReportPath} daily={_dailySummaryPath} allocations={_allocationReportPath} exposures={_factorExposureReportPath} montecarlo={_monteCarloReportPath}");
        }

        public static bool IsFreshFactorSnapshot(AShareBarraCNE5V2FactorData factor, DateTime sessionDate)
        {
            return factor != null && factor.EndTime.Date == sessionDate.Date && factor.PresentFactorCount > 0;
        }

        private void ProcessSession(DateTime sessionDate, bool force = false)
        {
            var firstUpdateOfSession = _lastProcessedDate != sessionDate;
            decimal dailyReturn = 0m;

            if (firstUpdateOfSession)
            {
                _lastProcessedDate = sessionDate;
                foreach (var key in _holdingDaysBySymbol.Keys.ToList())
                {
                    _holdingDaysBySymbol[key] += 1;
                }
            }

            // OOS period detection
            if (_oosEnabled)
            {
                var wasInOosTestPeriod = _inOosTestPeriod;
                _inOosTestPeriod = sessionDate >= _oosTestStartDate;
                if (!wasInOosTestPeriod && _inOosTestPeriod && _trainingPeriodSettings == null)
                {
                    _trainingPeriodSettings = CalibrateFactorWeightsFromTrainingPeriod();
                    if (_trainingPeriodSettings != null)
                    {
                        Log($"[OOS] Transition to test period at {sessionDate:yyyy-MM-dd}. Using calibrated factor weights from training period.");
                    }
                }
            }

            PruneExpiredRiskCooldowns(sessionDate);
            ApplyRiskManagementExits(sessionDate);

            var eligibleFactors = _latestFactorsByUnderlying
                .Where(pair =>
                    IsFreshFactorSnapshot(pair.Value, sessionDate) &&
                    IsEligibleFactor(pair.Value) &&
                    !IsSymbolInRiskCooldown(pair.Key, sessionDate))
                .ToDictionary(pair => pair.Key, pair => pair.Value);

            var rebalance = false;
            var shouldEvaluateSignals = LiveMode || _lastSignalEvaluationDate != sessionDate;

            if (shouldEvaluateSignals)
            {
                _lastSignalEvaluationDate = sessionDate;
                var shouldRefreshTargets = LiveMode || ShouldRebalance(sessionDate, _lastRebalanceDate, _rebalanceFrequency);

                if (shouldRefreshTargets)
                {
                    var activeSettings = (_oosEnabled && _inOosTestPeriod && _trainingPeriodSettings != null)
                        ? _trainingPeriodSettings
                        : _signalSettings;

                    // V3: Regime switching — adjust factor weights based on market regime
                    if (_regimeSwitchingEnabled)
                    {
                        var newRegime = DetectMarketRegime();
                        if (newRegime != _currentRegime)
                        {
                            Log($"[regime] {sessionDate:yyyyMMdd} regime change: {_currentRegime} -> {newRegime}");
                            _currentRegime = newRegime;
                        }
                        ApplyRegimeWeights(activeSettings, _currentRegime);
                    }

                    // V3: IC/IR weight adjustment — update weights based on factor IC history
                    if (_factorICHistory.Count > 0)
                    {
                        AShareBarraCNE5V3_2SignalModel.ApplyIRWeightAdjustment(
                            activeSettings, _factorICHistory, _icMinObservations, _irSensitivity);
                    }

                    var scores = AShareBarraCNE5V3_2SignalModel.ComputeScores(eligibleFactors, activeSettings);
                    ReplaceLatestScores(scores);
                    _latestScoreSpread = GetScoreSpread(scores);

                    // V3: Track factor IC for next period
                    if (_prevPeriodStockReturns.Count > 0 && _prevPeriodFactorScores.Count > 0)
                    {
                        var factorZScores = AShareBarraCNE5V3_2SignalModel.ComputeFactorZScores(eligibleFactors, activeSettings);
                        var periodIC = AShareBarraCNE5V3_2SignalModel.ComputeFactorIC(factorZScores, _prevPeriodStockReturns);
                        foreach (var icPair in periodIC)
                        {
                            if (!_factorICHistory.ContainsKey(icPair.Key))
                                _factorICHistory[icPair.Key] = new List<decimal>();
                            _factorICHistory[icPair.Key].Add(icPair.Value);
                            if (_factorICHistory[icPair.Key].Count > _icLookbackPeriods)
                                _factorICHistory[icPair.Key].RemoveAt(0);
                        }
                    }
                    _prevPeriodFactorScores = new Dictionary<Symbol, decimal>(scores);
                    _prevPeriodStockReturns = new Dictionary<Symbol, decimal>();

                    var exposureState = ComputeSharpeBasedExposureScale();
                    _latestExposureScale = exposureState.SmoothedScale;

                    // V3: Vol targeting
                    _currentVolScale = ComputeVolTargetScale();
                    _latestEffectiveTargetExposure = Math.Min(1m, _targetPortfolioExposure * _latestExposureScale * _currentVolScale);

                    LogSignalRanking(sessionDate, eligibleFactors.Count, scores);
                    Log(
                        $"[sharpe] trade_date={sessionDate:yyyyMMdd} daily_returns={exposureState.DailyReturnsCount} " +
                        $"sharpe_ann={exposureState.AnnualizedSharpe:F2} raw_scale={exposureState.RawScale:F2} " +
                        $"smoothed_scale={exposureState.SmoothedScale:F2} vol_scale={_currentVolScale:F2} " +
                        $"effective_exposure={_latestEffectiveTargetExposure:F2} regime={_currentRegime}");

                    // V3: Stratified or standard selection
                    List<AShareBarraCNE5V3_2Target> targets;
                    if (_stratifiedSelectionEnabled && _symbolIndustryMap.Count > 0)
                    {
                        targets = AShareBarraCNE5V3_2SignalModel.SelectPortfolioStratified(
                            scores, eligibleFactors, _symbolIndustryMap, _topN, _minScoreSpread,
                            _latestEffectiveTargetExposure, activeSettings.WeightingMode, activeSettings);
                    }
                    else
                    {
                        targets = AShareBarraCNE5V3_2SignalModel.SelectPortfolio(
                            scores, eligibleFactors, _topN, _minScoreSpread,
                            _latestEffectiveTargetExposure, activeSettings.WeightingMode, activeSettings);
                    }

                    // V3: Turnover constraint
                    if (_turnoverConstraintEnabled && _previousWeights.Count > 0)
                    {
                        var targetWeightMap = targets.ToDictionary(t => t.Symbol, t => t.Weight);
                        var constrained = ApplyTurnoverConstraint(targetWeightMap, _previousWeights);
                        targets = targets.Select(t => new AShareBarraCNE5V3_2Target
                        {
                            Symbol = t.Symbol,
                            Weight = constrained.TryGetValue(t.Symbol, out var w) ? w : t.Weight,
                            Score = t.Score
                        }).ToList();
                    }

                    if (targets.Count == 0)
                    {
                        _latestTargetWeightsByUnderlying.Clear();
                        Log($"{sessionDate:yyyy-MM-dd} rebalance skipped: eligible={eligibleFactors.Count} scoreSpread={_latestScoreSpread:F4} threshold={_minScoreSpread:F4}");
                    }
                    else
                    {
                        ReplaceLatestTargets(targets);
                        _previousWeights = targets.ToDictionary(t => t.Symbol, t => t.Weight);
                        ExecuteRebalance(targets, sessionDate);
                        rebalance = true;
                        _rebalanceCount += 1;
                        _lastRebalanceDate = sessionDate;
                        var turnover = ComputeTurnover(_previousWeights, targets.ToDictionary(t => t.Symbol, t => t.Weight));
                        Log(
                            $"{sessionDate:yyyy-MM-dd} rebalance -> eligible={eligibleFactors.Count} selected={targets.Count} " +
                            $"scoreSpread={_latestScoreSpread:F4} exposure_scale={_latestExposureScale:F2} " +
                            $"regime={_currentRegime} vol_scale={_currentVolScale:F2} turnover={turnover:F4}");
                    }
                }
            }

            // Update peak prices for trailing stop
            foreach (var symbol in _factorToUnderlying.Values)
            {
                if (Securities.TryGetValue(symbol, out var security) && security.Holdings.Invested && security.Price > 0m)
                {
                    if (!_peakPriceBySymbol.ContainsKey(symbol) || security.Price > _peakPriceBySymbol[symbol])
                    {
                        _peakPriceBySymbol[symbol] = security.Price;
                    }
                }
            }

            // Record daily return for Monte Carlo and Sharpe exposure
            var currentEquity = Portfolio.TotalPortfolioValue;
            if (firstUpdateOfSession && _previousEquity > 0m)
            {
                dailyReturn = currentEquity / _previousEquity - 1m;
                _dailyReturns.Add(dailyReturn);
                _dailyReturnDates.Add(Time.Date);

                // V3.2 fix: Track per-stock returns for IC calculation using individual stock returns
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
                                _prevPeriodStockReturns[symbol] = currClose / prevClose - 1m;
                            }
                        }
                    }
                }
            }

            // Collect factor returns for Monte Carlo
            if (_monteCarloEnabled && eligibleFactors.Count > 0)
            {
                CollectFactorReturns(eligibleFactors, dailyReturn);
            }

            // Compute factor exposure
            var weightMap = BuildCurrentWeightMap(currentEquity);
            var exposure = AShareBarraCNE5V3_2SignalModel.ComputePortfolioExposure(weightMap, _latestFactorsByUnderlying);
            UpsertFactorExposure(new FactorExposureV3_2Row
            {
                TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Holdings = Securities.Values.Count(s => s.Holdings.Invested),
                Beta = exposure["beta"],
                Momentum = exposure["momentum"],
                Size = exposure["size"],
                EarningsYield = exposure["earnyld"],
                ResidualVolatility = exposure["resvol"],
                Growth = exposure["growth"],
                BookToPrice = exposure["btop"],
                Leverage = exposure["leverage"],
                Liquidity = exposure["liquidity"],
                NonLinearSize = exposure["nlsize"],
                MoneyFlow = exposure["moneyflow"],
                Quality = exposure["quality"],
                Northbound = exposure["northbound"],
                Margin = exposure["margin"],
                ChipCost = exposure["chipcost"]
            });

            var investedCount = Securities.Values.Count(s => s.Holdings.Invested);
            var selectedCount = _latestTargetWeightsByUnderlying.Count > 0 ? _latestTargetWeightsByUnderlying.Count : investedCount;

            SetRuntimeStatistic("Holdings", investedCount.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Last Session", sessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Signals", selectedCount.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Score Spr", _latestScoreSpread.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Exposure", _latestExposureScale.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Target Exp", _latestEffectiveTargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("OOS Period", _inOosTestPeriod ? "test" : "training");
            SetRuntimeStatistic("Regime", _currentRegime ?? "mid_vol");
            SetRuntimeStatistic("VolScale", _currentVolScale.ToString("F2", CultureInfo.InvariantCulture));

            if (firstUpdateOfSession)
            {
                var oosTag = _oosEnabled ? (_inOosTestPeriod ? "[OOS-TEST]" : "[OOS-TRAIN]") : "";
                Log(
                    $"{oosTag}[daily snapshot] trade_date={sessionDate:yyyyMMdd} equity={currentEquity:F0} " +
                    $"cash={Portfolio.Cash:F0} invested={Portfolio.TotalHoldingsValue:F0} holdings={investedCount} " +
                    $"eligible={eligibleFactors.Count} selected={selectedCount} " +
                    $"scoreSpread={_latestScoreSpread:F4} exposure_scale={_latestExposureScale:F2} effective_exposure={_latestEffectiveTargetExposure:F2} " +
                    $"daily_return={dailyReturn:P4}");

                _dailySummaryRows.Add(new DailySummaryV3_2Row
                {
                    TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                    Equity = currentEquity,
                    Cash = Portfolio.Cash,
                    Invested = Portfolio.TotalHoldingsValue,
                    DailyReturn = dailyReturn,
                    Holdings = investedCount,
                    ExposureScale = _latestExposureScale,
                    EffectiveExposure = _latestEffectiveTargetExposure,
                    SharpeAnnualized = _dailyReturns.Count >= 20 ? ComputeCurrentSharpe() : 0m,
                    Regime = _currentRegime,
                    VolTargetScale = _currentVolScale,
                    Turnover = ComputeTurnover(
                        _latestTargetWeightsByUnderlying.ToDictionary(k => k.Key, k => k.Value),
                        _previousWeights),
                    AvgIC = ComputeAvgIC(),
                    AvgIR = ComputeAvgIR()
                });
            }

            if (LiveMode || force)
            {
                _lastLiveProcessingTime = ResolveLiveProcessingTimestamp();
            }
            _previousEquity = currentEquity;
        }

        /// <summary>
        /// Compute Sharpe-ratio-based exposure scale from rolling daily returns.
        /// Replaces V2's binary Kelly formula with a more stable approach.
        /// Formula: scale = clamp(base + sensitivity * annualized_sharpe, minScale, maxScale)
        /// Then apply EMA smoothing to prevent sudden jumps.
        /// </summary>
        private SharpeExposureV3_2State ComputeSharpeBasedExposureScale()
        {
            var minDays = Math.Max(20, _sharpeLookbackDays / 3);

            if (_dailyReturns.Count < minDays)
            {
                return new SharpeExposureV3_2State
                {
                    DailyReturnsCount = _dailyReturns.Count,
                    AnnualizedSharpe = 0m,
                    RawScale = _sharpeExposureBase,
                    SmoothedScale = _sharpeExposureBase,
                    EffectiveExposure = _targetPortfolioExposure * _sharpeExposureBase
                };
            }

            var lookbackReturns = _dailyReturns
                .TakeLast(Math.Min(_sharpeLookbackDays, _dailyReturns.Count))
                .ToList();

            var meanDailyReturn = lookbackReturns.Average();
            var variance = lookbackReturns.Count > 1
                ? lookbackReturns.Select(r => Math.Pow((double)(r - meanDailyReturn), 2)).Average()
                : 0.0;
            var stdDailyReturn = (decimal)Math.Sqrt(variance);

            // Subtract risk-free rate for proper Sharpe calculation
            var lookbackDates = _dailyReturnDates
                .TakeLast(Math.Min(_sharpeLookbackDays, _dailyReturnDates.Count))
                .ToList();
            var dailyRiskFreeRate = lookbackDates.Count > 0
                ? RiskFreeInterestRateModel.GetAverageRiskFreeRate(lookbackDates) / 252m
                : 0m;
            var excessDailyReturn = meanDailyReturn - dailyRiskFreeRate;

            var annualizedSharpe = stdDailyReturn > 0m
                ? excessDailyReturn / stdDailyReturn * (decimal)Math.Sqrt(252)
                : 0m;

            var rawScale = Clamp(
                _sharpeExposureBase + _sharpeExposureSensitivity * annualizedSharpe,
                _exposureMinScale,
                _exposureMaxScale);

            var smoothedScale = _exposureSmoothingAlpha * rawScale
                              + (1m - _exposureSmoothingAlpha) * _smoothedExposureScale;
            _smoothedExposureScale = smoothedScale;

            return new SharpeExposureV3_2State
            {
                DailyReturnsCount = lookbackReturns.Count,
                AnnualizedSharpe = annualizedSharpe,
                RawScale = rawScale,
                SmoothedScale = smoothedScale,
                EffectiveExposure = _targetPortfolioExposure * smoothedScale
            };
        }

        private decimal ComputeCurrentSharpe()
        {
            if (_dailyReturns.Count < 20) return 0m;
            var lookback = _dailyReturns.TakeLast(Math.Min(_sharpeLookbackDays, _dailyReturns.Count)).ToList();
            var mean = lookback.Average();
            var variance = lookback.Select(r => Math.Pow((double)(r - mean), 2)).Average();
            var std = (decimal)Math.Sqrt(variance);
            return std > 0m ? mean / std * (decimal)Math.Sqrt(252) : 0m;
        }

        private decimal ComputeAvgIC()
        {
            if (_factorICHistory.Count == 0) return 0m;
            var allICs = _factorICHistory.Values.SelectMany(ics => ics).ToList();
            return allICs.Count > 0 ? allICs.Average() : 0m;
        }

        private decimal ComputeAvgIR()
        {
            if (_factorICHistory.Count == 0) return 0m;
            var irs = new List<decimal>();
            foreach (var ics in _factorICHistory.Values)
            {
                if (ics.Count < _icMinObservations) continue;
                var mean = ics.Average();
                var std = (decimal)Math.Sqrt(ics.Select(x => Math.Pow((double)(x - mean), 2)).Average());
                if (std > 0m) irs.Add(mean / std);
            }
            return irs.Count > 0 ? irs.Average() : 0m;
        }

        private void ExecuteRebalance(IReadOnlyList<AShareBarraCNE5V3_2Target> targets, DateTime sessionDate)
        {
            var targetSymbols = new HashSet<Symbol>(targets.Select(t => t.Symbol));
            foreach (var symbol in _factorToUnderlying.Values)
            {
                if (Securities.TryGetValue(symbol, out var security) && security.Holdings.Invested)
                {
                    if (!targetSymbols.Contains(symbol))
                    {
                        var tag = $"REBALANCE_EXIT {sessionDate:yyyyMMdd}";
                        Liquidate(symbol, tag);
                        _peakPriceBySymbol.Remove(symbol);
                        _holdingDaysBySymbol.Remove(symbol);
                    }
                }
            }

            foreach (var target in targets)
            {
                if (!Securities.TryGetValue(target.Symbol, out var security)) continue;
                if (security.Price <= 0m) continue;

                var tag = $"REBALANCE {sessionDate:yyyyMMdd} score={target.Score:F4}";
                SetHoldings(target.Symbol, target.Weight, tag: tag);
            }

            LogTargetPreview(sessionDate, targets);
        }

        private void ApplyRiskManagementExits(DateTime sessionDate)
        {
            var stopLossExits = 0;
            var trailingStopExits = 0;

            foreach (var symbol in _factorToUnderlying.Values.ToList())
            {
                if (!Securities.TryGetValue(symbol, out var security)) continue;
                if (!security.Holdings.Invested) continue;

                var avgPrice = security.Holdings.AveragePrice;
                var currentPrice = security.Price;
                if (avgPrice <= 0m || currentPrice <= 0m) continue;

                var holdingDays = _holdingDaysBySymbol.TryGetValue(symbol, out var hd) ? hd : 0;
                var peakPrice = _peakPriceBySymbol.TryGetValue(symbol, out var pp) ? pp : currentPrice;
                peakPrice = Math.Max(peakPrice, currentPrice);
                _peakPriceBySymbol[symbol] = peakPrice;

                var reason = ResolveRiskExitReason(avgPrice, currentPrice, peakPrice, holdingDays);
                if (reason == null) continue;

                var tag = $"{reason} {sessionDate:yyyyMMdd}";
                Liquidate(symbol, tag);
                _peakPriceBySymbol.Remove(symbol);
                _holdingDaysBySymbol.Remove(symbol);
                _riskCooldownUntilBySymbol[symbol] = sessionDate.AddDays(_riskExitCooldownDays);

                if (reason == "STOP_LOSS") stopLossExits++;
                else if (reason == "TRAILING_STOP") trailingStopExits++;
            }

            if (stopLossExits + trailingStopExits > 0)
            {
                Log(
                    $"[risk exits] trade_date={sessionDate:yyyyMMdd} stop_loss={stopLossExits} " +
                    $"trailing_stop={trailingStopExits}");
            }
        }

        private string ResolveRiskExitReason(decimal averagePrice, decimal currentPrice, decimal peakPrice, int holdingDays)
        {
            if (averagePrice <= 0m || currentPrice <= 0m) return null;

            if (_stopLossPct > 0m && currentPrice <= averagePrice * (1m - _stopLossPct))
            {
                return "STOP_LOSS";
            }

            var profitActivated =
                _profitActivationPct > 0m &&
                holdingDays >= _minHoldDaysForProfitProtection &&
                peakPrice >= averagePrice * (1m + _profitActivationPct);
            if (profitActivated && _trailingStopPct > 0m && currentPrice <= peakPrice * (1m - _trailingStopPct))
            {
                return "TRAILING_STOP";
            }

            return null;
        }

        private void PruneExpiredRiskCooldowns(DateTime sessionDate)
        {
            foreach (var pair in _riskCooldownUntilBySymbol.ToList())
            {
                if (pair.Value.Date < sessionDate.Date)
                {
                    _riskCooldownUntilBySymbol.Remove(pair.Key);
                }
            }
        }

        private bool IsSymbolInRiskCooldown(Symbol symbol, DateTime sessionDate)
        {
            return _riskCooldownUntilBySymbol.TryGetValue(symbol, out var cooldownUntil)
                && cooldownUntil.Date >= sessionDate.Date;
        }

        private string DetectMarketRegime()
        {
            if (_dailyReturns.Count < _regimeVolLookbackDays) return "mid_vol";
            var lookback = _dailyReturns.TakeLast(_regimeVolLookbackDays).ToList();
            var mean = lookback.Average();
            var variance = lookback.Select(r => Math.Pow((double)(r - mean), 2)).Average();
            var std = (decimal)Math.Sqrt(variance);
            var annualizedVol = std * (decimal)Math.Sqrt(252);

            if (annualizedVol < _regimeLowVolThreshold) return "low_vol";
            if (annualizedVol > _regimeHighVolThreshold) return "high_vol";
            return "mid_vol";
        }

        private void ApplyRegimeWeights(AShareBarraCNE5V3_2SignalSettings settings, string regime)
        {
            var regimeWeights = regime switch
            {
                "low_vol" => settings.LowVolWeights,
                "high_vol" => settings.HighVolWeights,
                _ => settings.MidVolWeights
            };

            var setters = new (string key, Action<decimal> setter)[]
            {
                ("beta", w => settings.BetaWeight = w),
                ("momentum", w => settings.MomentumWeight = w),
                ("size", w => settings.SizeWeight = w),
                ("earnyld", w => settings.EarningsYieldWeight = w),
                ("resvol", w => settings.ResidualVolatilityWeight = w),
                ("growth", w => settings.GrowthWeight = w),
                ("btop", w => settings.BookToPriceWeight = w),
                ("leverage", w => settings.LeverageWeight = w),
                ("liquidity", w => settings.LiquidityWeight = w),
                ("nlsize", w => settings.NonLinearSizeWeight = w),
                ("moneyflow", w => settings.MoneyFlowWeight = w),
                ("quality", w => settings.QualityWeight = w),
                ("northbound", w => settings.NorthboundWeight = w),
                ("margin", w => settings.MarginWeight = w),
                ("chipcost", w => settings.ChipCostWeight = w),
            };

            foreach (var (key, setter) in setters)
            {
                if (!regimeWeights.TryGetValue(key, out var targetWeight)) continue;
                var currentWeight = GetCurrentWeight(settings, key);
                var blended = _regimeTransitionAlpha * targetWeight + (1m - _regimeTransitionAlpha) * currentWeight;
                setter(blended);
            }
        }

        private static decimal GetCurrentWeight(AShareBarraCNE5V3_2SignalSettings settings, string key)
        {
            return key switch
            {
                "beta" => settings.BetaWeight,
                "momentum" => settings.MomentumWeight,
                "size" => settings.SizeWeight,
                "earnyld" => settings.EarningsYieldWeight,
                "resvol" => settings.ResidualVolatilityWeight,
                "growth" => settings.GrowthWeight,
                "btop" => settings.BookToPriceWeight,
                "leverage" => settings.LeverageWeight,
                "liquidity" => settings.LiquidityWeight,
                "nlsize" => settings.NonLinearSizeWeight,
                "moneyflow" => settings.MoneyFlowWeight,
                "quality" => settings.QualityWeight,
                "northbound" => settings.NorthboundWeight,
                "margin" => settings.MarginWeight,
                "chipcost" => settings.ChipCostWeight,
                _ => 0m
            };
        }

        private decimal ComputeVolTargetScale()
        {
            if (!_volTargetEnabled) return 1.0m;
            if (_dailyReturns.Count < _volTargetLookbackDays) return 1.0m;
            var lookback = _dailyReturns.TakeLast(_volTargetLookbackDays).ToList();
            var mean = lookback.Average();
            var variance = lookback.Select(r => Math.Pow((double)(r - mean), 2)).Average();
            var std = (decimal)Math.Sqrt(variance);
            var annualizedVol = std * (decimal)Math.Sqrt(252);
            if (annualizedVol <= 0m) return 1.0m;
            var rawScale = _volTargetAnnual / annualizedVol;
            return Clamp(rawScale, _volTargetFloorScale, _volTargetCapScale);
        }

        private Dictionary<Symbol, decimal> ApplyTurnoverConstraint(
            Dictionary<Symbol, decimal> targetWeights,
            Dictionary<Symbol, decimal> previousWeights)
        {
            if (!_turnoverConstraintEnabled) return targetWeights;
            var turnover = ComputeTurnover(targetWeights, previousWeights);
            if (turnover <= _maxTurnover) return targetWeights;

            var blendRatio = _maxTurnover / turnover;
            var allSymbols = targetWeights.Keys.Union(previousWeights.Keys).ToHashSet();
            var constrained = new Dictionary<Symbol, decimal>();
            foreach (var symbol in allSymbols)
            {
                var target = targetWeights.TryGetValue(symbol, out var tw) ? tw : 0m;
                var prev = previousWeights.TryGetValue(symbol, out var pw) ? pw : 0m;
                constrained[symbol] = prev + blendRatio * (target - prev);
            }
            return constrained;
        }

        private static decimal ComputeTurnover(
            Dictionary<Symbol, decimal> newWeights,
            Dictionary<Symbol, decimal> oldWeights)
        {
            var allSymbols = newWeights.Keys.Union(oldWeights.Keys).ToHashSet();
            return allSymbols.Sum(s =>
                Math.Abs(newWeights.GetValueOrDefault(s) - oldWeights.GetValueOrDefault(s)));
        }

        private void LoadIndustryClassification()
        {
            var path = ResolveOptionalDataPath(_industryClassificationPath);
            if (!File.Exists(path))
            {
                Log($"[V3] Industry classification file not found: {path}. Stratified selection disabled.");
                _stratifiedSelectionEnabled = false;
                return;
            }

            var loadedCount = 0;
            foreach (var line in File.ReadLines(path).Skip(1))
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                var parts = line.Split(',');
                if (parts.Length < 5) continue;

                var tsCode = parts[0].Trim();
                var swL1Name = parts[4].Trim();
                if (string.IsNullOrWhiteSpace(tsCode) || string.IsNullOrWhiteSpace(swL1Name)) continue;

                if (!TryParseTsCode(tsCode, out var symbol)) continue;

                _symbolIndustryMap[symbol] = swL1Name;
                loadedCount++;
            }

            Log($"[V3] Loaded {loadedCount} industry classifications from {path}");
            if (_symbolIndustryMap.Count == 0)
            {
                Log("[V3] No industry mappings loaded. Stratified selection disabled.");
                _stratifiedSelectionEnabled = false;
            }
        }

        private string ResolveOptionalDataPath(string relativePath)
        {
            if (string.IsNullOrWhiteSpace(relativePath)) return relativePath;
            if (Path.IsPathRooted(relativePath)) return relativePath;
            return Path.Combine(Globals.DataFolder, relativePath);
        }

        private AShareBarraCNE5V3_2SignalSettings CalibrateFactorWeightsFromTrainingPeriod()
        {
            var calibrated = new AShareBarraCNE5V3_2SignalSettings
            {
                BetaWeight = _signalSettings.BetaWeight,
                MomentumWeight = _signalSettings.MomentumWeight,
                SizeWeight = _signalSettings.SizeWeight,
                EarningsYieldWeight = _signalSettings.EarningsYieldWeight,
                ResidualVolatilityWeight = _signalSettings.ResidualVolatilityWeight,
                GrowthWeight = _signalSettings.GrowthWeight,
                BookToPriceWeight = _signalSettings.BookToPriceWeight,
                LeverageWeight = _signalSettings.LeverageWeight,
                LiquidityWeight = _signalSettings.LiquidityWeight,
                NonLinearSizeWeight = _signalSettings.NonLinearSizeWeight,
                MoneyFlowWeight = _signalSettings.MoneyFlowWeight,
                QualityWeight = _signalSettings.QualityWeight,
                NorthboundWeight = _signalSettings.NorthboundWeight,
                MarginWeight = _signalSettings.MarginWeight,
                ChipCostWeight = _signalSettings.ChipCostWeight,
                MinimumPresentFactors = _signalSettings.MinimumPresentFactors,
                WeightingMode = _signalSettings.WeightingMode,
                BlackLittermanTau = _signalSettings.BlackLittermanTau,
                BlackLittermanRiskAversion = _signalSettings.BlackLittermanRiskAversion,
                BlackLittermanViewScale = _signalSettings.BlackLittermanViewScale,
                BlackLittermanViewConfidence = _signalSettings.BlackLittermanViewConfidence,
                BlackLittermanPriorBlend = _signalSettings.BlackLittermanPriorBlend,
                KellyWeightFraction = _signalSettings.KellyWeightFraction,
                KellyVarianceFloor = _signalSettings.KellyVarianceFloor,
                KellyResidualVolatilityScale = _signalSettings.KellyResidualVolatilityScale,
                KellyBetaPenaltyScale = _signalSettings.KellyBetaPenaltyScale,
                MaxSingleWeight = _signalSettings.MaxSingleWeight,
                LowVolWeights = new Dictionary<string, decimal>(_signalSettings.LowVolWeights),
                MidVolWeights = new Dictionary<string, decimal>(_signalSettings.MidVolWeights),
                HighVolWeights = new Dictionary<string, decimal>(_signalSettings.HighVolWeights),
                IRSensitivity = _signalSettings.IRSensitivity,
                ICLookbackPeriods = _signalSettings.ICLookbackPeriods,
                ICMinObservations = _signalSettings.ICMinObservations
            };

            if (_factorICHistory.Count > 0)
            {
                AShareBarraCNE5V3_2SignalModel.ApplyIRWeightAdjustment(
                    calibrated, _factorICHistory, _icMinObservations, _irSensitivity);
                Log("[OOS] Applied IC/IR weight calibration from training period to test period settings");
            }
            else
            {
                Log("[OOS] No IC history available from training period - using default weights");
            }

            return calibrated;
        }

        private void CollectFactorReturns(IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors, decimal portfolioReturn)
        {
            if (factors.Count == 0) return;

            var factorReturns = new decimal[15];
            var count = 0;

            foreach (var pair in factors)
            {
                var f = pair.Value;
                if (f == null) continue;
                count++;
                factorReturns[0] += f.Beta ?? 0m;
                factorReturns[1] += f.Momentum ?? 0m;
                factorReturns[2] += f.Size ?? 0m;
                factorReturns[3] += f.EarningsYield ?? 0m;
                factorReturns[4] += f.ResidualVolatility ?? 0m;
                factorReturns[5] += f.Growth ?? 0m;
                factorReturns[6] += f.BookToPrice ?? 0m;
                factorReturns[7] += f.Leverage ?? 0m;
                factorReturns[8] += f.Liquidity ?? 0m;
                factorReturns[9] += f.NonLinearSize ?? 0m;
                factorReturns[10] += f.MoneyFlow ?? 0m;
                factorReturns[11] += f.Quality ?? 0m;
                factorReturns[12] += f.Northbound ?? 0m;
                factorReturns[13] += f.Margin ?? 0m;
                factorReturns[14] += f.ChipCost ?? 0m;
            }

            if (count > 0)
            {
                for (var i = 0; i < factorReturns.Length; i++)
                {
                    factorReturns[i] /= count;
                }
                _historicalFactorReturns.Add(factorReturns);
            }
        }

        private void RunMonteCarloSimulation()
        {
            if (_dailyReturns.Count < _monteCarloBlockSize)
            {
                Log($"[Monte Carlo] Insufficient daily returns: {_dailyReturns.Count} < {_monteCarloBlockSize}");
                return;
            }

            Log($"[Monte Carlo] Running {_monteCarloTrials} trials with {_dailyReturns.Count} daily return observations");

            var terminalValues = new List<decimal>();
            var maxDrawdowns = new List<decimal>();
            var sharpeRatios = new List<decimal>();

            for (var trial = 0; trial < _monteCarloTrials; trial++)
            {
                var pathValue = 1m;
                var peakValue = 1m;
                var maxDrawdown = 0m;
                var dailyReturnsTrial = new List<decimal>();

                for (var day = 0; day < _monteCarloHorizonDays; day++)
                {
                    var blockStart = _monteCarloRandom.Next(_dailyReturns.Count - _monteCarloBlockSize + 1);
                    var blockIndex = _monteCarloRandom.Next(_monteCarloBlockSize);
                    var sampledReturn = _dailyReturns[blockStart + blockIndex];

                    var perturbation = 1m + _monteCarloFactorPerturbationScale * (decimal)(_monteCarloRandom.NextDouble() * 2 - 1);
                    var adjustedReturn = sampledReturn * perturbation;

                    pathValue *= (1m + adjustedReturn);
                    peakValue = Math.Max(peakValue, pathValue);
                    maxDrawdown = Math.Max(maxDrawdown, (peakValue - pathValue) / peakValue);
                    dailyReturnsTrial.Add(adjustedReturn);
                }

                terminalValues.Add(pathValue);
                maxDrawdowns.Add(maxDrawdown);

                if (dailyReturnsTrial.Count > 0)
                {
                    var meanReturn = dailyReturnsTrial.Average();
                    var stdReturn = dailyReturnsTrial.Count > 1
                        ? (decimal)Math.Sqrt(dailyReturnsTrial.Select(r => Math.Pow((double)(r - meanReturn), 2)).Average())
                        : 0m;
                    var sharpe = stdReturn > 0m ? meanReturn / stdReturn * (decimal)Math.Sqrt(252) : 0m;
                    sharpeRatios.Add(sharpe);
                }
            }

            var stats = new MonteCarloStatistics
            {
                TerminalValueP05 = GetPercentile(terminalValues, 0.05m),
                TerminalValueP25 = GetPercentile(terminalValues, 0.25m),
                TerminalValueP50 = GetPercentile(terminalValues, 0.50m),
                TerminalValueP75 = GetPercentile(terminalValues, 0.75m),
                TerminalValueP95 = GetPercentile(terminalValues, 0.95m),
                MaxDrawdownP05 = GetPercentile(maxDrawdowns, 0.05m),
                MaxDrawdownP50 = GetPercentile(maxDrawdowns, 0.50m),
                MaxDrawdownP95 = GetPercentile(maxDrawdowns, 0.95m),
                SharpeP05 = GetPercentile(sharpeRatios, 0.05m),
                SharpeP50 = GetPercentile(sharpeRatios, 0.50m),
                SharpeP95 = GetPercentile(sharpeRatios, 0.95m)
            };

            Log(
                $"[Monte Carlo] Terminal Value: P05={stats.TerminalValueP05:F4} P50={stats.TerminalValueP50:F4} P95={stats.TerminalValueP95:F4} " +
                $"MaxDD: P05={stats.MaxDrawdownP05:P2} P50={stats.MaxDrawdownP50:P2} P95={stats.MaxDrawdownP95:P2} " +
                $"Sharpe: P05={stats.SharpeP05:F2} P50={stats.SharpeP50:F2} P95={stats.SharpeP95:F2}");

            var mcRows = new List<string>
            {
                "statistic,p05,p25,p50,p75,p95",
                $"terminal_value,{stats.TerminalValueP05:F6},{stats.TerminalValueP25:F6},{stats.TerminalValueP50:F6},{stats.TerminalValueP75:F6},{stats.TerminalValueP95:F6}",
                $"max_drawdown,{stats.MaxDrawdownP05:F6},{stats.MaxDrawdownP50:F6},{stats.MaxDrawdownP50:F6},{stats.MaxDrawdownP50:F6},{stats.MaxDrawdownP95:F6}",
                $"sharpe_ratio,{stats.SharpeP05:F6},{stats.SharpeP50:F6},{stats.SharpeP50:F6},{stats.SharpeP50:F6},{stats.SharpeP95:F6}"
            };

            File.WriteAllLines(_monteCarloReportPath, mcRows);
            Log($"[Monte Carlo] Results saved to {_monteCarloReportPath}");
        }

        private static decimal GetPercentile(List<decimal> values, decimal percentile)
        {
            if (values == null || values.Count == 0) return 0m;
            var sorted = values.OrderBy(v => v).ToList();
            var index = (int)Math.Floor(percentile * (sorted.Count - 1));
            index = Math.Max(0, Math.Min(sorted.Count - 1, index));
            return sorted[index];
        }

        public static bool ShouldRebalance(DateTime currentDate, DateTime lastRebalanceDate, string frequency)
        {
            if (lastRebalanceDate == default) return true;
            var token = (frequency ?? "monthly").Trim().ToLowerInvariant();
            switch (token)
            {
                case "weekly": return (currentDate.Date - lastRebalanceDate.Date).TotalDays >= 7;
                case "biweekly": return (currentDate.Date - lastRebalanceDate.Date).TotalDays >= 14;
                case "semi-monthly":
                case "semimonthly":
                    var day = currentDate.Day;
                    if (day != 1 && day != 16) return false;
                    return (currentDate.Date - lastRebalanceDate.Date).TotalDays >= 10;
                default: return currentDate.Year != lastRebalanceDate.Year || currentDate.Month != lastRebalanceDate.Month;
            }
        }

        private bool IsEligibleFactor(AShareBarraCNE5V2FactorData factor)
        {
            if (factor == null || factor.IsSt) return false;
            if (factor.ListedDays.HasValue && factor.ListedDays.Value < _minListedDays) return false;
            if (factor.MissingFactorCount.HasValue && factor.MissingFactorCount.Value > _maxMissingFactorCount) return false;
            if (factor.TurnoverRate.HasValue && factor.TurnoverRate.Value < _minTurnoverRate) return false;
            if (_minTotalMv.HasValue && factor.TotalMv.HasValue && factor.TotalMv.Value < _minTotalMv.Value) return false;
            return factor.PresentFactorCount >= _signalSettings.MinimumPresentFactors;
        }

        private void ReplaceLatestScores(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            _latestScoresByUnderlying.Clear();
            if (scores == null) return;
            foreach (var pair in scores) _latestScoresByUnderlying[pair.Key] = pair.Value;
        }

        private void ReplaceLatestTargets(IReadOnlyList<AShareBarraCNE5V3_2Target> targets)
        {
            _latestTargetWeightsByUnderlying.Clear();
            if (targets == null) return;
            foreach (var target in targets) _latestTargetWeightsByUnderlying[target.Symbol] = target.Weight;
        }

        private Dictionary<Symbol, decimal> BuildCurrentWeightMap(decimal equity)
        {
            var weights = new Dictionary<Symbol, decimal>();
            if (equity <= 0m) return weights;
            foreach (var symbol in _factorToUnderlying.Values)
            {
                if (Securities.TryGetValue(symbol, out var security) && security.Holdings.Invested)
                {
                    weights[symbol] = security.Holdings.HoldingsValue / equity;
                }
            }
            return weights;
        }

        private decimal GetScoreSpread(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            if (scores == null || scores.Count == 0) return 0m;
            var orderedScores = scores.Values.OrderBy(v => v).ToList();
            var median = GetMedian(orderedScores);
            return scores.Values.Max() - median;
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0) return 0m;
            var midpoint = values.Count / 2;
            return values.Count % 2 == 0
                ? (values[midpoint - 1] + values[midpoint]) / 2m
                : values[midpoint];
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue) return minValue;
            return value > maxValue ? maxValue : value;
        }

        private void LogSignalRanking(DateTime sessionDate, int eligibleCount, IReadOnlyDictionary<Symbol, decimal> scores)
        {
            var rankingPreview = scores == null
                ? "none"
                : string.Join(", ",
                    scores.OrderByDescending(pair => pair.Value)
                        .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                        .Take(5)
                        .Select(pair => $"{ToTsCode(pair.Key)}={pair.Value:F4}"));
            if (string.IsNullOrWhiteSpace(rankingPreview)) rankingPreview = "none";
            Log($"[signal ranking] trade_date={sessionDate:yyyyMMdd} eligible={eligibleCount} preview={rankingPreview}");
        }

        private void LogTargetPreview(DateTime sessionDate, IReadOnlyList<AShareBarraCNE5V3_2Target> targets)
        {
            var preview = targets == null
                ? "none"
                : string.Join(", ",
                    targets.OrderByDescending(target => target.Score)
                        .ThenBy(target => target.Symbol.Value, StringComparer.Ordinal)
                        .Take(5)
                        .Select(target => $"{ToTsCode(target.Symbol)} weight={target.Weight:P2} score={target.Score:F4}"));
            if (string.IsNullOrWhiteSpace(preview)) preview = "none";
            Log($"[target preview] trade_date={sessionDate:yyyyMMdd} selected={targets?.Count ?? 0} preview={preview}");
        }

        // Live trading support methods
        private bool ShouldRunLiveProcessingCycle(DateTime sessionDate)
        {
            if (!LiveMode) return true;
            if (_lastProcessedDate != sessionDate || _lastLiveProcessingTime == default) return true;
            var currentTimestamp = ResolveLiveProcessingTimestamp();
            if (currentTimestamp <= _lastLiveProcessingTime) return false;
            return currentTimestamp - _lastLiveProcessingTime >= _liveSignalInterval;
        }

        private DateTime ResolveLiveProcessingTimestamp()
        {
            if (_anchorSymbol != null && Securities.TryGetValue(_anchorSymbol, out var security) && security.LocalTime != default)
                return security.LocalTime;
            return Time != default ? Time : DateTime.UtcNow;
        }

        private void RunLiveMonitoringCycle()
        {
            if (!LiveMode) return;
            var sessionDate = ResolveLiveSessionDate();
            EnsureLiveFactorSnapshots(sessionDate);
            if (ShouldRunLiveProcessingCycle(sessionDate))
            {
                ProcessSession(sessionDate);
            }
        }

        private DateTime ResolveLiveSessionDate()
        {
            if (_anchorSymbol != null && Securities.TryGetValue(_anchorSymbol, out var security))
            {
                var localTime = security.LocalTime;
                if (localTime != default) return localTime.Date;
            }
            return Time.Date;
        }

        private int EnsureLiveFactorSnapshots(DateTime sessionDate, bool forceRefresh = false)
        {
            if (!LiveMode) return 0;
            var freshFactorCount = _latestFactorsByUnderlying.Values.Count(factor => IsFreshFactorSnapshot(factor, sessionDate));
            if (!forceRefresh && _lastLiveFactorDiskRefreshDate == sessionDate && freshFactorCount > 0) return freshFactorCount;

            var loadedCount = 0;
            foreach (var underlying in _factorToUnderlying.Values.Distinct())
            {
                if (TryLoadFactorSnapshotFromDisk(underlying, sessionDate, out var factor, out var usedFallback, out var carryForwarded))
                {
                    _latestFactorsByUnderlying[underlying] = factor;
                    loadedCount += 1;
                }
            }

            _lastLiveFactorDiskRefreshDate = loadedCount > 0 ? sessionDate : default;
            Log($"[factor sync] trade_date={sessionDate:yyyyMMdd} loaded={loadedCount} fresh_before={freshFactorCount} forced={(forceRefresh ? 1 : 0)}");
            return loadedCount;
        }

        private bool TryLoadFactorSnapshotFromDisk(Symbol underlying, DateTime sessionDate,
            out AShareBarraCNE5V2FactorData factor, out bool usedFallback, out bool carryForwarded)
        {
            usedFallback = false;
            carryForwarded = false;

            if (TryLoadFactorSnapshotFromPath(underlying, sessionDate, _factorDataPath, out factor, out var matchedTradeDate))
            {
                carryForwarded = matchedTradeDate.Date != sessionDate.Date;
                return true;
            }

            if (string.IsNullOrWhiteSpace(_fallbackFactorDataPath))
            {
                factor = null;
                return false;
            }

            var primaryFullPath = Path.GetFullPath(_factorDataPath);
            var fallbackFullPath = Path.GetFullPath(_fallbackFactorDataPath);
            if (string.Equals(primaryFullPath, fallbackFullPath, StringComparison.OrdinalIgnoreCase))
            {
                factor = null;
                return false;
            }

            if (TryLoadFactorSnapshotFromPath(underlying, sessionDate, _fallbackFactorDataPath, out factor, out matchedTradeDate))
            {
                usedFallback = true;
                carryForwarded = matchedTradeDate.Date != sessionDate.Date;
                return true;
            }

            factor = null;
            return false;
        }

        private bool TryLoadFactorSnapshotFromPath(Symbol underlying, DateTime sessionDate,
            string factorRootPath, out AShareBarraCNE5V2FactorData factor, out DateTime matchedTradeDate)
        {
            factor = null;
            matchedTradeDate = default;
            if (string.IsNullOrWhiteSpace(factorRootPath)) return false;

            var factorPath = AShareBarraCNE5V2FactorData.ResolveSourcePath(underlying, factorRootPath);
            if (!File.Exists(factorPath)) return false;

            var targetTradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            string matchedLine = null;
            foreach (var line in File.ReadLines(factorPath).Reverse())
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase)) continue;
                var separatorIndex = line.IndexOf(',');
                if (separatorIndex <= 0) continue;
                var lineTradeDate = line.Substring(0, separatorIndex);
                if (string.CompareOrdinal(lineTradeDate, targetTradeDate) > 0) continue;
                matchedLine = line;
                break;
            }

            if (string.IsNullOrWhiteSpace(matchedLine)) return false;

            var csv = matchedLine.Split(',');
            if (csv.Length < 21 || !TryParseTradeDate(csv[0], out matchedTradeDate)) return false;

            factor = new AShareBarraCNE5V2FactorData
            {
                Symbol = underlying,
                Time = sessionDate.Date,
                EndTime = sessionDate.Date,
                Value = ParseNullableDecimal(csv[2]) ?? 0m,
                Beta = ParseNullableDecimal(csv[1]),
                Momentum = ParseNullableDecimal(csv[2]),
                Size = ParseNullableDecimal(csv[3]),
                EarningsYield = ParseNullableDecimal(csv[4]),
                ResidualVolatility = ParseNullableDecimal(csv[5]),
                Growth = ParseNullableDecimal(csv[6]),
                BookToPrice = ParseNullableDecimal(csv[7]),
                Leverage = ParseNullableDecimal(csv[8]),
                Liquidity = ParseNullableDecimal(csv[9]),
                NonLinearSize = ParseNullableDecimal(csv[10]),
                MoneyFlow = ParseNullableDecimal(csv[11]),
                Quality = ParseNullableDecimal(csv[12]),
                Northbound = ParseNullableDecimal(csv[13]),
                Margin = ParseNullableDecimal(csv[14]),
                ChipCost = ParseNullableDecimal(csv[15]),
                TotalMv = ParseNullableDecimal(csv[16]),
                TurnoverRate = ParseNullableDecimal(csv[17]),
                ListedDays = ParseNullableInt(csv[18]),
                MissingFactorCount = ParseNullableInt(csv[19]),
                IsSt = ParseNullableInt(csv[20]).GetValueOrDefault() != 0
            };
            return true;
        }

        private void PersistOutputs()
        {
            try
            {
                // Write factor exposures
                if (_factorExposureRows.Count > 0)
                {
                    var exposureLines = new List<string> { "trade_date,holdings,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,moneyflow,quality,northbound,margin,chipcost" };
                    exposureLines.AddRange(_factorExposureRows.Select(row =>
                        $"{row.TradeDate},{row.Holdings},{row.Beta:F6},{row.Momentum:F6},{row.Size:F6},{row.EarningsYield:F6},{row.ResidualVolatility:F6},{row.Growth:F6},{row.BookToPrice:F6},{row.Leverage:F6},{row.Liquidity:F6},{row.NonLinearSize:F6},{row.MoneyFlow:F6},{row.Quality:F6},{row.Northbound:F6},{row.Margin:F6},{row.ChipCost:F6}"));
                    File.WriteAllLines(_factorExposureReportPath, exposureLines);
                }

                // Write daily summary with Sharpe-based metrics
                if (_dailySummaryRows.Count > 0)
                {
                    var dailyLines = new List<string> { "trade_date,equity,cash,invested,daily_return,holdings,exposure_scale,effective_exposure,sharpe_annualized,regime,vol_target_scale,turnover,avg_ic,avg_ir" };
                    dailyLines.AddRange(_dailySummaryRows.Select(row =>
                        $"{row.TradeDate},{row.Equity:F2},{row.Cash:F2},{row.Invested:F2},{row.DailyReturn:F8},{row.Holdings},{row.ExposureScale:F4},{row.EffectiveExposure:F4},{row.SharpeAnnualized:F4},{row.Regime},{row.VolTargetScale:F4},{row.Turnover:F4},{row.AvgIC:F4},{row.AvgIR:F4}"));
                    File.WriteAllLines(_dailySummaryPath, dailyLines);
                }

                // Write allocations
                var allocLines = new List<string> { "trade_date,symbol,weight,quantity,price,market_value" };
                var equity = Portfolio.TotalPortfolioValue;
                foreach (var symbol in _factorToUnderlying.Values)
                {
                    if (Securities.TryGetValue(symbol, out var security) && security.Holdings.Invested)
                    {
                        var weight = security.Holdings.HoldingsValue / equity;
                        allocLines.Add($"{Time:yyyyMMdd},{ToTsCode(symbol)},{weight:F6},{security.Holdings.Quantity},{security.Price:F6},{security.Holdings.HoldingsValue:F6}");
                    }
                }
                File.WriteAllLines(_allocationReportPath, allocLines);

                // Write trade report
                if (_tradeRows.Count > 0)
                {
                    var tradeLines = new List<string> { "trade_date,executed_at,symbol,action,quantity,price,trade_value,fee,score,reason" };
                    tradeLines.AddRange(_tradeRows.Select(row =>
                        $"{row.TradeDate},{row.ExecutedAt},{row.Symbol},{row.Action},{row.Quantity.ToString(CultureInfo.InvariantCulture)},{row.Price.ToString("F6", CultureInfo.InvariantCulture)},{row.TradeValue.ToString("F6", CultureInfo.InvariantCulture)},{row.Fee.ToString("F6", CultureInfo.InvariantCulture)},{row.Score.ToString("F6", CultureInfo.InvariantCulture)},{row.Reason ?? string.Empty}"));
                    File.WriteAllLines(_tradeReportPath, tradeLines);
                }

                // Write portfolio state for live recovery
                if (LiveMode)
                {
                    var statePayload = new
                    {
                        Equity = Portfolio.TotalPortfolioValue,
                        Cash = Portfolio.Cash,
                        LastRebalanceDate = _lastRebalanceDate,
                        RebalanceCount = _rebalanceCount,
                        ExposureScale = _latestExposureScale,
                        EffectiveExposure = _latestEffectiveTargetExposure,
                        SharpeAnnualized = ComputeCurrentSharpe(),
                        RiskCooldowns = _riskCooldownUntilBySymbol.Select(pair => new { TsCode = ToTsCode(pair.Key), CooldownUntil = pair.Value }).ToList(),
                        PeakPrices = _peakPriceBySymbol.Select(pair => new { TsCode = ToTsCode(pair.Key), PeakPrice = pair.Value }).ToList()
                    };
                    File.WriteAllText(_portfolioStatePath, JsonConvert.SerializeObject(statePayload, Formatting.Indented));
                }
            }
            catch (Exception ex)
            {
                Log($"[persist] failed to write outputs: {ex.Message}");
            }
        }

        private void UpsertFactorExposure(FactorExposureV3_2Row row)
        {
            _factorExposureRows.RemoveAll(existing => string.Equals(existing.TradeDate, row.TradeDate, StringComparison.Ordinal));
            _factorExposureRows.Add(row);
        }

        // Utility methods
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

        private string ResolveOptionalFactorDataPath(string parameterValue)
        {
            if (string.IsNullOrWhiteSpace(parameterValue)) return null;
            return Path.GetFullPath(parameterValue);
        }

        private string ResolveOutputPath(string parameterValue, string defaultFileName)
        {
            if (!string.IsNullOrWhiteSpace(parameterValue)) return Path.GetFullPath(parameterValue);
            var outputDir = Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v3-2-outputs");
            if (!Directory.Exists(outputDir)) Directory.CreateDirectory(outputDir);
            return Path.Combine(outputDir, defaultFileName);
        }

        private string ToTsCode(Symbol symbol)
        {
            var market = symbol.ID.Market.ToUpperInvariant();
            var suffix = market == "SSE" ? ".SH" : ".SZ";
            return $"{symbol.Value}{suffix}";
        }

        private bool TryParseTsCode(string tsCode, out Symbol symbol)
        {
            symbol = default;
            if (string.IsNullOrWhiteSpace(tsCode)) return false;
            var parts = tsCode.Split('.', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length != 2) return false;
            var ticker = parts[0].Trim();
            var marketSuffix = parts[1].Trim().ToUpperInvariant();
            var market = marketSuffix == "SH" ? Market.SSE : Market.SZSE;
            symbol = QuantConnect.Symbol.Create(ticker, SecurityType.Equity, market);
            return true;
        }

        private bool TryParseTradeDate(string value, out DateTime date)
        {
            date = default;
            if (string.IsNullOrWhiteSpace(value)) return false;
            return DateTime.TryParseExact(value, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }

        private decimal? ParseNullableDecimal(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return null;
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : null;
        }

        private int? ParseNullableInt(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return null;
            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : null;
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
    }

    // V3.2 Data structures
    internal sealed class SharpeExposureV3_2State
    {
        public int DailyReturnsCount { get; set; }
        public decimal AnnualizedSharpe { get; set; }
        public decimal RawScale { get; set; }
        public decimal SmoothedScale { get; set; }
        public decimal EffectiveExposure { get; set; }
    }

    public sealed class FactorExposureV3_2Row
    {
        public string TradeDate { get; set; }
        public int Holdings { get; set; }
        public decimal Beta { get; set; }
        public decimal Momentum { get; set; }
        public decimal Size { get; set; }
        public decimal EarningsYield { get; set; }
        public decimal ResidualVolatility { get; set; }
        public decimal Growth { get; set; }
        public decimal BookToPrice { get; set; }
        public decimal Leverage { get; set; }
        public decimal Liquidity { get; set; }
        public decimal NonLinearSize { get; set; }
        public decimal MoneyFlow { get; set; }
        public decimal Quality { get; set; }
        public decimal Northbound { get; set; }
        public decimal Margin { get; set; }
        public decimal ChipCost { get; set; }
    }

    internal sealed class DailySummaryV3_2Row
    {
        public string TradeDate { get; set; }
        public decimal Equity { get; set; }
        public decimal Cash { get; set; }
        public decimal Invested { get; set; }
        public decimal DailyReturn { get; set; }
        public int Holdings { get; set; }
        public decimal ExposureScale { get; set; }
        public decimal EffectiveExposure { get; set; }
        public decimal SharpeAnnualized { get; set; }
        public string Regime { get; set; }
        public decimal VolTargetScale { get; set; }
        public decimal Turnover { get; set; }
        public decimal AvgIC { get; set; }
        public decimal AvgIR { get; set; }
    }

    internal sealed class TradeRowV3_2
    {
        public string TradeDate { get; set; }
        public string ExecutedAt { get; set; }
        public string Symbol { get; set; }
        public string Action { get; set; }
        public int Quantity { get; set; }
        public decimal Price { get; set; }
        public decimal TradeValue { get; set; }
        public decimal Fee { get; set; }
        public decimal Score { get; set; }
        public string Reason { get; set; }
    }
}
