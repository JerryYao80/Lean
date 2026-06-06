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
    /// Barra CNE5 V2 strategy with 15 factors (10 original + 5 new: moneyflow, quality, northbound, margin, chipcost).
    /// Uses LEAN native orders (SetHoldings/MarketOrder) for all portfolio management.
    /// LEAN computes all statistics (Sharpe, drawdown, etc.) natively.
    /// Features:
    /// - Look-ahead bias prevention: factors must be fresh (trade_date == session_date)
    /// - Out-of-sample testing: training period for factor weight calibration, test period for validation
    /// - Monte Carlo simulation: bootstrap resampling of LEAN native daily returns
    /// </summary>
    public class AShareBarraCNE5V2Algorithm : QCAlgorithm
    {
        private readonly Dictionary<Symbol, Symbol> _factorToUnderlying = new();
        private readonly Dictionary<Symbol, AShareBarraCNE5V2FactorData> _latestFactorsByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestScoresByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestTargetWeightsByUnderlying = new();
        private readonly Dictionary<Symbol, DateTime> _riskCooldownUntilBySymbol = new();
        private readonly Dictionary<Symbol, decimal> _peakPriceBySymbol = new();
        private readonly Dictionary<Symbol, int> _holdingDaysBySymbol = new();
        private readonly List<FactorExposureV2Row> _factorExposureRows = new();
        private readonly List<decimal> _dailyReturns = new();

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
        private decimal _portfolioKellyFraction;
        private decimal _kellyFallbackScale;
        private decimal _kellyMinExposureScale;
        private decimal _kellyMaxExposureScale;
        private decimal _stopLossPct;
        private decimal _profitActivationPct;
        private decimal _trailingStopPct;
        private int _topN;
        private int _minListedDays;
        private int _maxMissingFactorCount;
        private int _kellyLookbackClosedTrades;
        private int _kellyMinClosedTrades;
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
        private decimal _latestKellyScale;
        private decimal _latestEffectiveTargetExposure;
        private decimal _previousEquity;

        // OOS testing parameters
        private DateTime _oosTrainingStartDate;
        private DateTime _oosTrainingEndDate;
        private DateTime _oosTestStartDate;
        private bool _inOosTestPeriod;
        private AShareBarraCNE5V2SignalSettings _trainingPeriodSettings;
        private AShareBarraCNE5V2SignalSettings _signalSettings;

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
            _livePriceSnapshotPath = ResolveOutputPath(GetParameter("live-price-snapshot-file"), "barra-cne5v2-live-price-snapshot.json");
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "barra-cne5v2-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "barra-cne5v2-daily-summary.csv");
            _allocationReportPath = ResolveOutputPath(GetParameter("allocation-report-file"), "barra-cne5v2-allocation.csv");
            _factorExposureReportPath = ResolveOutputPath(GetParameter("factor-exposure-file"), "barra-cne5v2-factor-exposure.csv");
            _portfolioStatePath = ResolveOutputPath(GetParameter("portfolio-state-file"), "barra-cne5v2-live-state.json");
            _monteCarloReportPath = ResolveOutputPath(GetParameter("monte-carlo-file"), "barra-cne5v2-monte-carlo.csv");
            _rebalanceFrequency = (GetParameter("rebalance-frequency") ?? "monthly").Trim().ToLowerInvariant();
            _topN = GetIntParameter("top-n", 30);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.5m);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.95m);
            _minListedDays = GetIntParameter("min-listed-days", 250);
            _maxMissingFactorCount = GetIntParameter("max-missing-factor-count", 3);
            _minTurnoverRate = GetDecimalParameter("min-turnover-rate", 0m);
            _minTotalMv = GetOptionalDecimalParameter("min-total-mv");
            _portfolioKellyFraction = GetDecimalParameter("portfolio-kelly-fraction", 0.50m);
            _kellyLookbackClosedTrades = GetIntParameter("kelly-lookback-closed-trades", 24);
            _kellyMinClosedTrades = GetIntParameter("kelly-min-closed-trades", 8);
            _kellyFallbackScale = GetDecimalParameter("kelly-fallback-scale", 0.70m);
            _kellyMinExposureScale = GetDecimalParameter("kelly-min-scale", 0.25m);
            _kellyMaxExposureScale = GetDecimalParameter("kelly-max-scale", 1.00m);
            _stopLossPct = GetDecimalParameter("stop-loss-pct", 0.10m);
            _profitActivationPct = GetDecimalParameter("take-profit-activation-pct", 0.12m);
            _trailingStopPct = GetDecimalParameter("trailing-stop-pct", 0.06m);
            _riskExitCooldownDays = GetIntParameter("risk-exit-cooldown-days", 5);
            _minHoldDaysForProfitProtection = GetIntParameter("min-hold-days-for-profit-protection", 2);

            _signalSettings = new AShareBarraCNE5V2SignalSettings
            {
                BetaWeight = GetDecimalParameter("factor-weight-beta", -0.05m),
                MomentumWeight = GetDecimalParameter("factor-weight-momentum", 0.20m),
                SizeWeight = GetDecimalParameter("factor-weight-size", -0.10m),
                EarningsYieldWeight = GetDecimalParameter("factor-weight-earnyld", 0.20m),
                ResidualVolatilityWeight = GetDecimalParameter("factor-weight-resvol", -0.10m),
                GrowthWeight = GetDecimalParameter("factor-weight-growth", 0.15m),
                BookToPriceWeight = GetDecimalParameter("factor-weight-btop", 0.10m),
                LeverageWeight = GetDecimalParameter("factor-weight-leverage", -0.05m),
                LiquidityWeight = GetDecimalParameter("factor-weight-liquidity", 0.05m),
                NonLinearSizeWeight = GetDecimalParameter("factor-weight-nlsize", 0.00m),
                MoneyFlowWeight = GetDecimalParameter("factor-weight-moneyflow", 0.10m),
                QualityWeight = GetDecimalParameter("factor-weight-quality", 0.15m),
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
                MaxSingleWeight = GetDecimalParameter("max-single-weight", 0.12m)
            };

            _latestKellyScale = _kellyFallbackScale;
            _latestEffectiveTargetExposure = _targetPortfolioExposure * _latestKellyScale;

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
                throw new InvalidOperationException($"No Barra CNE5 V2 symbols were configured from factor path {_factorDataPath}");
            }

            Log($"AShareBarraCNE5V2Algorithm initialized with {_factorToUnderlying.Count} factor subscriptions");
            Log("Execution mode: LEAN native orders (SetHoldings/MarketOrder). All statistics computed by LEAN engine.");
            Log(
                $"Config: factor path={_factorDataPath} fallback={_fallbackFactorDataPath ?? "-"} rebalance={_rebalanceFrequency} " +
                $"topN={_topN} exposure={_targetPortfolioExposure:F2} weighting={_signalSettings.WeightingMode} minScoreSpread={_minScoreSpread:F2}");
            Log(
                $"Risk overlay: portfolioKelly={_portfolioKellyFraction:F2} fallbackScale={_kellyFallbackScale:F2} " +
                $"stopLoss={_stopLossPct:P0} takeProfitActivation={_profitActivationPct:P0} trailingStop={_trailingStopPct:P0} cooldownDays={_riskExitCooldownDays}");

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

            SetRuntimeStatistic("Holdings", "0");
            SetRuntimeStatistic("Kelly", _latestKellyScale.ToString("F2", CultureInfo.InvariantCulture));
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

            // Track peak price for trailing stop
            if (quantity > 0)
            {
                if (!_peakPriceBySymbol.ContainsKey(symbol) || fillPrice > _peakPriceBySymbol[symbol])
                {
                    _peakPriceBySymbol[symbol] = fillPrice;
                }
                _holdingDaysBySymbol[symbol] = 0;
                _riskCooldownUntilBySymbol.Remove(symbol);
            }

            Log(
                $"[order filled] symbol={ToTsCode(symbol)} action={(quantity > 0 ? "BUY" : "SELL")} " +
                $"quantity={Math.Abs(quantity)} price={fillPrice:F4} " +
                $"order_id={orderEvent.OrderId} tag={orderEvent.Message ?? string.Empty}");
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
            Log($"Saved Barra V2 outputs: trades={_tradeReportPath} daily={_dailySummaryPath} allocations={_allocationReportPath} exposures={_factorExposureReportPath} montecarlo={_monteCarloReportPath}");
        }

        public static bool IsFreshFactorSnapshot(AShareBarraCNE5V2FactorData factor, DateTime sessionDate)
        {
            return factor != null && factor.EndTime.Date == sessionDate.Date && factor.PresentFactorCount > 0;
        }

        private void ProcessSession(DateTime sessionDate, bool force = false)
        {
            var firstUpdateOfSession = _lastProcessedDate != sessionDate;
            if (firstUpdateOfSession)
            {
                _lastProcessedDate = sessionDate;
                // Increment holding days for all tracked symbols
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

            // Filter factors with look-ahead bias prevention
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

                    var scores = AShareBarraCNE5V2SignalModel.ComputeScores(eligibleFactors, activeSettings);
                    ReplaceLatestScores(scores);
                    _latestScoreSpread = GetScoreSpread(scores);

                    var kellyState = ComputePortfolioKellyState();
                    _latestKellyScale = kellyState.ExposureScale;
                    _latestEffectiveTargetExposure = Math.Min(1m, _targetPortfolioExposure * _latestKellyScale);
                    LogSignalRanking(sessionDate, eligibleFactors.Count, scores);
                    Log(
                        $"[kelly] trade_date={sessionDate:yyyyMMdd} closed_trades={kellyState.ClosedTrades} " +
                        $"win_rate={kellyState.WinRate:P1} payoff={kellyState.PayoffRatio:F2} raw={kellyState.RawKellyFraction:F2} " +
                        $"scale={_latestKellyScale:F2} effective_exposure={_latestEffectiveTargetExposure:F2}");

                    var targets = AShareBarraCNE5V2SignalModel.SelectPortfolio(
                        scores, eligibleFactors, _topN, _minScoreSpread,
                        _latestEffectiveTargetExposure, activeSettings.WeightingMode, activeSettings);

                    if (targets.Count == 0)
                    {
                        _latestTargetWeightsByUnderlying.Clear();
                        Log($"{sessionDate:yyyy-MM-dd} rebalance skipped: eligible={eligibleFactors.Count} scoreSpread={_latestScoreSpread:F4} threshold={_minScoreSpread:F4}");
                    }
                    else
                    {
                        ReplaceLatestTargets(targets);
                        ExecuteRebalance(targets, sessionDate);
                        rebalance = true;
                        _rebalanceCount += 1;
                        _lastRebalanceDate = sessionDate;
                        Log(
                            $"{sessionDate:yyyy-MM-dd} rebalance -> eligible={eligibleFactors.Count} selected={targets.Count} " +
                            $"scoreSpread={_latestScoreSpread:F4} kelly={_latestKellyScale:F2}");
                    }
                }
            }

            // Update peak prices for trailing stop tracking
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

            // Record daily return for Monte Carlo (only on first update of session to avoid double-counting)
            var currentEquity = Portfolio.TotalPortfolioValue;
            decimal dailyReturn = 0m;
            if (firstUpdateOfSession && _previousEquity > 0m)
            {
                dailyReturn = currentEquity / _previousEquity - 1m;
                _dailyReturns.Add(dailyReturn);
            }

            // Collect factor returns for Monte Carlo
            if (_monteCarloEnabled && eligibleFactors.Count > 0)
            {
                CollectFactorReturns(eligibleFactors, dailyReturn);
            }

            // Compute factor exposure
            var weightMap = BuildCurrentWeightMap(currentEquity);
            var exposure = AShareBarraCNE5V2SignalModel.ComputePortfolioExposure(weightMap, _latestFactorsByUnderlying);
            UpsertFactorExposure(new FactorExposureV2Row
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
            SetRuntimeStatistic("Kelly", _latestKellyScale.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Target Exp", _latestEffectiveTargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("OOS Period", _inOosTestPeriod ? "test" : "training");
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
            SetRuntimeStatistic("Total Fees", Portfolio.TotalFees.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Net Profit", (Portfolio.TotalPortfolioValue - Portfolio.TotalFees - _previousEquity).ToString("F2", CultureInfo.InvariantCulture));

            if (firstUpdateOfSession)
            {
                var oosTag = _oosEnabled ? (_inOosTestPeriod ? "[OOS-TEST]" : "[OOS-TRAIN]") : "";
                Log(
                    $"{oosTag}[daily snapshot] trade_date={sessionDate:yyyyMMdd} equity={currentEquity:F0} " +
                    $"cash={Portfolio.Cash:F0} invested={Portfolio.TotalHoldingsValue:F0} holdings={investedCount} " +
                    $"eligible={eligibleFactors.Count} selected={selectedCount} " +
                    $"scoreSpread={_latestScoreSpread:F4} kelly={_latestKellyScale:F2} exposure={_latestEffectiveTargetExposure:F2} " +
                    $"daily_return={dailyReturn:P4}");
            }

            if (LiveMode || force)
            {
                _lastLiveProcessingTime = ResolveLiveProcessingTimestamp();
            }
            _previousEquity = currentEquity;
        }

        /// <summary>
        /// Execute rebalance using LEAN native SetHoldings.
        /// This places real MarketOrder orders through LEAN's execution pipeline.
        /// </summary>
        private void ExecuteRebalance(IReadOnlyList<AShareBarraCNE5V2Target> targets, DateTime sessionDate)
        {
            // First, liquidate symbols not in target and in risk cooldown
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

            // Set holdings for target symbols
            foreach (var target in targets)
            {
                if (!Securities.TryGetValue(target.Symbol, out var security)) continue;
                if (security.Price <= 0m) continue;

                var tag = $"REBALANCE {sessionDate:yyyyMMdd} score={target.Score:F4}";
                SetHoldings(target.Symbol, target.Weight, tag: tag);
            }

            LogTargetPreview(sessionDate, targets);
        }

        /// <summary>
        /// Apply risk management exits using LEAN native Liquidate.
        /// Checks stop-loss and trailing stop conditions on LEAN portfolio holdings.
        /// </summary>
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

        private void RemoveRiskBlockedTargets(DateTime sessionDate)
        {
            foreach (var symbol in _latestTargetWeightsByUnderlying.Keys.ToList())
            {
                if (IsSymbolInRiskCooldown(symbol, sessionDate))
                {
                    _latestTargetWeightsByUnderlying.Remove(symbol);
                }
            }
        }

        /// <summary>
        /// Compute Kelly sizing state from LEAN native closed trade history.
        /// Uses Transactions.GetOrders() to extract actual trade P&L.
        /// </summary>
        private KellySizingState ComputePortfolioKellyState()
        {
            var closedTrades = ExtractClosedTradeReturns();
            var recentReturns = closedTrades
                .TakeLast(Math.Max(1, _kellyLookbackClosedTrades))
                .ToList();

            if (recentReturns.Count < _kellyMinClosedTrades)
            {
                return new KellySizingState
                {
                    ClosedTrades = recentReturns.Count,
                    ExposureScale = Clamp(_kellyFallbackScale, _kellyMinExposureScale, _kellyMaxExposureScale),
                    RawKellyFraction = 0m,
                    WinRate = 0m,
                    PayoffRatio = 0m
                };
            }

            var wins = recentReturns.Where(v => v > 0m).ToList();
            var losses = recentReturns.Where(v => v < 0m).ToList();
            if (wins.Count == 0 || losses.Count == 0)
            {
                return new KellySizingState
                {
                    ClosedTrades = recentReturns.Count,
                    ExposureScale = Clamp(_kellyFallbackScale, _kellyMinExposureScale, _kellyMaxExposureScale),
                    RawKellyFraction = 0m,
                    WinRate = wins.Count == 0 ? 0m : 1m,
                    PayoffRatio = 0m
                };
            }

            var winRate = wins.Count / (decimal)recentReturns.Count;
            var averageWin = wins.Average();
            var averageLoss = Math.Abs(losses.Average());
            var payoffRatio = averageLoss > 0m ? averageWin / averageLoss : 0m;
            var rawKelly = payoffRatio > 0m ? winRate - (1m - winRate) / payoffRatio : 0m;
            var exposureScale = Clamp(
                Math.Max(0m, rawKelly) * Clamp(_portfolioKellyFraction, 0m, 1m),
                _kellyMinExposureScale,
                _kellyMaxExposureScale);

            return new KellySizingState
            {
                ClosedTrades = recentReturns.Count,
                WinRate = winRate,
                PayoffRatio = payoffRatio,
                RawKellyFraction = rawKelly,
                ExposureScale = exposureScale
            };
        }

        /// <summary>
        /// Extract closed trade returns from LEAN native order history.
        /// Matches BUY/SELL fills to compute realized returns.
        /// </summary>
        private List<decimal> ExtractClosedTradeReturns()
        {
            var closedReturns = new List<decimal>();
            var openLotsBySymbol = new Dictionary<string, Queue<OpenLot>>(StringComparer.Ordinal);

            foreach (var order in Transactions.GetOrders())
            {
                if (order == null || order.Status != OrderStatus.Filled) continue;
                if (order.Symbol == null || order.SecurityType != SecurityType.Equity) continue;

                var symbolKey = order.Symbol.Value;
                var fillPrice = order.Price;
                var quantity = (int)Math.Abs(order.Quantity);
                var fee = order.Value * AShareStockFeeModel.DefaultCommissionRate;

                if (order.Direction == OrderDirection.Buy)
                {
                    if (!openLotsBySymbol.TryGetValue(symbolKey, out var openLots))
                    {
                        openLots = new Queue<OpenLot>();
                        openLotsBySymbol[symbolKey] = openLots;
                    }
                    openLots.Enqueue(new OpenLot
                    {
                        Quantity = quantity,
                        Price = fillPrice,
                        FeePerShare = quantity > 0 ? fee / quantity : 0m
                    });
                }
                else if (order.Direction == OrderDirection.Sell)
                {
                    if (!openLotsBySymbol.TryGetValue(symbolKey, out var queuedLots) || queuedLots.Count == 0) continue;

                    var sellQuantityRemaining = quantity;
                    var sellFeePerShare = quantity > 0 ? fee / quantity : 0m;
                    while (sellQuantityRemaining > 0 && queuedLots.Count > 0)
                    {
                        var lot = queuedLots.Peek();
                        var matchedQuantity = Math.Min(sellQuantityRemaining, lot.Quantity);
                        var costBasis = Math.Max(1m, matchedQuantity * lot.Price);
                        var profitLoss = (fillPrice - lot.Price) * matchedQuantity - (lot.FeePerShare + sellFeePerShare) * matchedQuantity;
                        closedReturns.Add(profitLoss / costBasis);

                        lot.Quantity -= matchedQuantity;
                        sellQuantityRemaining -= matchedQuantity;
                        if (lot.Quantity <= 0) queuedLots.Dequeue();
                    }
                }
            }

            return closedReturns;
        }

        private AShareBarraCNE5V2SignalSettings CalibrateFactorWeightsFromTrainingPeriod()
        {
            Log($"[OOS] Calibrating factor weights from training period (not yet implemented - using defaults)");
            return new AShareBarraCNE5V2SignalSettings
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
                MaxSingleWeight = _signalSettings.MaxSingleWeight
            };
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

        /// <summary>
        /// Monte Carlo simulation using LEAN native daily returns for baseline,
        /// with factor perturbation for stress testing.
        /// </summary>
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
                    // Block bootstrap from actual LEAN daily returns
                    var blockStart = _monteCarloRandom.Next(_dailyReturns.Count - _monteCarloBlockSize + 1);
                    var blockIndex = _monteCarloRandom.Next(_monteCarloBlockSize);
                    var sampledReturn = _dailyReturns[blockStart + blockIndex];

                    // Apply factor perturbation for stress testing
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

        private void ReplaceLatestTargets(IReadOnlyList<AShareBarraCNE5V2Target> targets)
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

        private void LogTargetPreview(DateTime sessionDate, IReadOnlyList<AShareBarraCNE5V2Target> targets)
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

                // Write daily summary from LEAN native portfolio
                if (_dailyReturns.Count > 0)
                {
                    var dailyLines = new List<string> { "trade_date,equity,cash,invested,daily_return,holdings,kelly_scale,effective_exposure" };
                    // We don't have per-day equity stored, so write a summary
                    // LEAN's own summary JSON has the full equity curve
                    File.WriteAllLines(_dailySummaryPath, dailyLines);
                }

                // Write allocations from LEAN native portfolio
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

                // Write portfolio state for live recovery
                if (LiveMode)
                {
                    var statePayload = new
                    {
                        Equity = Portfolio.TotalPortfolioValue,
                        Cash = Portfolio.Cash,
                        LastRebalanceDate = _lastRebalanceDate,
                        RebalanceCount = _rebalanceCount,
                        KellyScale = _latestKellyScale,
                        EffectiveExposure = _latestEffectiveTargetExposure,
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

        private void UpsertFactorExposure(FactorExposureV2Row row)
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
            var outputDir = Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-outputs");
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

    // Data structures
    public sealed class AShareBarraCNE5V2SignalSettings
    {
        public decimal BetaWeight { get; set; } = -0.05m;
        public decimal MomentumWeight { get; set; } = 0.20m;
        public decimal SizeWeight { get; set; } = -0.10m;
        public decimal EarningsYieldWeight { get; set; } = 0.20m;
        public decimal ResidualVolatilityWeight { get; set; } = -0.10m;
        public decimal GrowthWeight { get; set; } = 0.15m;
        public decimal BookToPriceWeight { get; set; } = 0.10m;
        public decimal LeverageWeight { get; set; } = -0.05m;
        public decimal LiquidityWeight { get; set; } = 0.05m;
        public decimal NonLinearSizeWeight { get; set; } = 0.00m;
        public decimal MoneyFlowWeight { get; set; } = 0.10m;
        public decimal QualityWeight { get; set; } = 0.15m;
        public decimal NorthboundWeight { get; set; } = 0.08m;
        public decimal MarginWeight { get; set; } = 0.05m;
        public decimal ChipCostWeight { get; set; } = 0.07m;
        public int MinimumPresentFactors { get; set; } = 8;
        public string WeightingMode { get; set; } = "black-litterman";
        public decimal BlackLittermanTau { get; set; } = 0.05m;
        public decimal BlackLittermanRiskAversion { get; set; } = 2.20m;
        public decimal BlackLittermanViewScale { get; set; } = 0.08m;
        public decimal BlackLittermanViewConfidence { get; set; } = 0.65m;
        public decimal BlackLittermanPriorBlend { get; set; } = 0.30m;
        public decimal KellyWeightFraction { get; set; } = 0.50m;
        public decimal KellyVarianceFloor { get; set; } = 0.35m;
        public decimal KellyResidualVolatilityScale { get; set; } = 0.40m;
        public decimal KellyBetaPenaltyScale { get; set; } = 0.10m;
        public decimal MaxSingleWeight { get; set; } = 0.12m;
    }

    public sealed class AShareBarraCNE5V2Target
    {
        public Symbol Symbol { get; init; }
        public decimal Weight { get; init; }
        public decimal Score { get; init; }
    }

    public sealed class FactorExposureV2Row
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

    public sealed class MonteCarloStatistics
    {
        public decimal TerminalValueP05 { get; set; }
        public decimal TerminalValueP25 { get; set; }
        public decimal TerminalValueP50 { get; set; }
        public decimal TerminalValueP75 { get; set; }
        public decimal TerminalValueP95 { get; set; }
        public decimal MaxDrawdownP05 { get; set; }
        public decimal MaxDrawdownP50 { get; set; }
        public decimal MaxDrawdownP95 { get; set; }
        public decimal SharpeP05 { get; set; }
        public decimal SharpeP50 { get; set; }
        public decimal SharpeP95 { get; set; }
    }

    internal sealed class OpenLot
    {
        public int Quantity { get; set; }
        public decimal Price { get; set; }
        public decimal FeePerShare { get; set; }
    }

    internal sealed class KellySizingState
    {
        public int ClosedTrades { get; set; }
        public decimal WinRate { get; set; }
        public decimal PayoffRatio { get; set; }
        public decimal RawKellyFraction { get; set; }
        public decimal ExposureScale { get; set; }
    }
}
