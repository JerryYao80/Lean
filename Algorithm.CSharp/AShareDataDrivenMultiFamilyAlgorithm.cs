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
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Multi-family data-driven stock strategy that composites 12 strategy family signals
    /// into a unified scoring model, then selects a long-only equity portfolio via
    /// Black-Litterman/Kelly sizing with macro regime adjustment.
    /// </summary>
    public class AShareDataDrivenMultiFamilyAlgorithm : QCAlgorithm
    {
        private const decimal CommissionRate = AShareStockFeeModel.DefaultCommissionRate;
        private const decimal MinimumCommission = AShareStockFeeModel.DefaultMinimumCommission;
        private const decimal StampDutyRate = AShareStockFeeModel.DefaultStampDutyRate;
        private const decimal TransferFeeRate = AShareStockFeeModel.DefaultTransferFeeRate;

        private readonly Dictionary<Symbol, Symbol> _factorToUnderlying = new();
        private readonly Dictionary<Symbol, AShareTushareFactorData> _latestFactorsByUnderlying = new();
        private readonly Dictionary<Symbol, AShareBarraCNE5FactorData> _latestBarraFactorsByUnderlying = new();
        private AShareMarketSentimentData _latestSentimentData;
        private readonly Dictionary<Symbol, AShareImpliedVolatilityData> _latestIvBySymbol = new();
        private readonly Dictionary<Symbol, decimal> _latestScoresByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestTargetWeightsByUnderlying = new();
        private readonly Dictionary<Symbol, Dictionary<string, decimal>> _latestFamilyScoresByUnderlying = new();
        private readonly Dictionary<Symbol, SyntheticPosition> _positions = new();
        private readonly Dictionary<Symbol, DateTime> _riskCooldownUntilBySymbol = new();
        private readonly List<TradeRow> _tradeRows = new();
        private readonly List<DailySummaryRow> _dailyRows = new();
        private readonly List<AllocationRow> _allocationRows = new();
        private readonly List<FamilyExposureRow> _familyExposureRows = new();

        private Symbol _anchorSymbol;
        private string _version;
        private string _factorDataPath;
        private string _barraFactorDataPath;
        private string _fallbackFactorDataPath;
        private string _livePriceSnapshotPath;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _allocationReportPath;
        private string _familyExposureReportPath;
        private string _portfolioStatePath;
        private string _rebalanceFrequency;
        private decimal _initialSyntheticCash;
        private decimal _syntheticCash;
        private decimal _previousEquity;
        private decimal _targetPortfolioExposure;
        private decimal _minScoreSpread;
        private decimal _portfolioKellyFraction;
        private decimal _kellyFallbackScale;
        private decimal _kellyMinExposureScale;
        private decimal _kellyMaxExposureScale;
        private decimal _stopLossPct;
        private decimal _profitActivationPct;
        private decimal _trailingStopPct;
        private decimal _dynamicStopLossPct;
        private decimal _dynamicTrailingStopPct;
        private int _dynamicCooldownDays;
        private bool _dynamicRiskControls;
        private decimal _regimeBasisWeight;
        private decimal _regimePcrWeight;
        private decimal _regimeVixWeight;
        private decimal _extremeFearBoost;
        private decimal _vixHighThreshold;
        private decimal _vixExtremeThreshold;
        private int _topN;
        private int _minListedDays;
        private int _minPresentFamilies;
        private int _kellyLookbackClosedTrades;
        private int _kellyMinClosedTrades;
        private int _riskExitCooldownDays;
        private int _minHoldDaysForProfitProtection;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private int _syntheticRebalanceCount;
        private DateTime _lastProcessedDate;
        private DateTime _lastSignalEvaluationDate;
        private DateTime _lastRebalanceDate;
        private DateTime _lastLiveProcessingTime;
        private DateTime _lastPortfolioLogUtc;
        private DateTime _lastRuntimePersistUtc;
        private DateTime _lastLiveFactorDiskRefreshDate;
        private DateTime _liveSnapshotLastWriteTimeUtc;
        private bool _syncLeanPortfolio;
        private bool _monteCarloEnabled;
        private decimal _latestScoreSpread;
        private decimal _latestKellyScale;
        private decimal _latestEffectiveTargetExposure;
        private decimal _latestRegimeAdjustment;
        private decimal _monteCarloFactorPerturbationScale;
        private int _latestStopLossExitCount;
        private int _latestTrailingStopExitCount;
        private AShareMultiFamilySignalSettings _signalSettings;
        private Dictionary<Symbol, decimal> _liveSnapshotPricesBySymbol = new();
        private TimeSpan _liveSignalInterval = TimeSpan.FromMinutes(3);
        private TimeSpan _livePriceSyncInterval = TimeSpan.FromMinutes(1);

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2020, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            var initialCash = GetDecimalParameter("initial-cash", 1000000m);

            _factorDataPath = ResolveFactorDataPath(GetParameter("factor-data-path"));
            _fallbackFactorDataPath = ResolveOptionalFactorDataPath(GetParameter("external-factor-path"));
            _livePriceSnapshotPath = ResolveOutputPath(GetParameter("live-price-snapshot-file"), "multi-family-live-price-snapshot.json");
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "multi-family-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "multi-family-daily-summary.csv");
            _allocationReportPath = ResolveOutputPath(GetParameter("allocation-report-file"), "multi-family-allocation.csv");
            _familyExposureReportPath = ResolveOutputPath(GetParameter("family-exposure-file"), "multi-family-family-exposure.csv");
            _portfolioStatePath = ResolveOutputPath(GetParameter("portfolio-state-file"), "multi-family-live-state.json");
            _rebalanceFrequency = (GetParameter("rebalance-frequency") ?? "monthly").Trim().ToLowerInvariant();
            _topN = GetIntParameter("top-n", 30);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.5m);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.95m);
            _minListedDays = GetIntParameter("min-listed-days", 250);
            _minPresentFamilies = GetIntParameter("min-present-families", 4);
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
            _signalSettings = new AShareMultiFamilySignalSettings
            {
                MomentumReversalWeight = GetDecimalParameter("family-weight-momentum-reversal", 1.0m),
                ValueQualityWeight = GetDecimalParameter("family-weight-value-quality", 1.0m),
                MoneyFlowWeight = GetDecimalParameter("family-weight-money-flow", 0.8m),
                EarningsSurpriseWeight = GetDecimalParameter("family-weight-earnings-surprise", 0.8m),
                ChipCostWeight = GetDecimalParameter("family-weight-chip-cost", 0.6m),
                EtfPremiumWeight = GetDecimalParameter("family-weight-etf-premium", 0.3m),
                SectorRotationWeight = GetDecimalParameter("family-weight-sector-rotation", 0.5m),
                MarginSignalWeight = GetDecimalParameter("family-weight-margin-signal", 0.8m),
                NorthboundFlowWeight = GetDecimalParameter("family-weight-northbound-flow", 0.8m),
                MultiFactorWeight = GetDecimalParameter("family-weight-multi-factor", 1.0m),
                AnalystSignalWeight = GetDecimalParameter("family-weight-analyst-signal", 0.3m),
                MacroRateWeight = GetDecimalParameter("family-weight-macro-rate", 0.5m),
                BarraMomentumWeight = GetDecimalParameter("family-weight-barra-momentum", 0.8m),
                BarraValueWeight = GetDecimalParameter("family-weight-barra-value", 0.6m),
                BarraQualityWeight = GetDecimalParameter("family-weight-barra-quality", 0.4m),
                LowVolatilityWeight = GetDecimalParameter("family-weight-low-volatility", 1.0m),
                SizeTiltWeight = GetDecimalParameter("family-weight-size-tilt", 0.6m),
                LiquidityPremiumWeight = GetDecimalParameter("family-weight-liquidity-premium", 0.5m),
                ChipConcentrationWeight = GetDecimalParameter("family-weight-chip-concentration", 0.8m),
                RateSensitivityWeight = GetDecimalParameter("family-weight-rate-sensitivity", 0.4m),
                BasisSentimentWeight = GetDecimalParameter("family-weight-basis-sentiment", 0.8m),
                OptionsPcrWeight = GetDecimalParameter("family-weight-options-pcr", 0.6m),
                MarginShortRatioWeight = GetDecimalParameter("family-weight-margin-short-ratio", 0.5m),
                BarraBetaWeight = GetDecimalParameter("family-weight-barra-beta", 0.5m),
                BarraNlsizeWeight = GetDecimalParameter("family-weight-barra-nlsize", 0.3m),
                BarraResvolWeight = GetDecimalParameter("family-weight-barra-resvol", 0.6m),
                BarraLiquidityWeight = GetDecimalParameter("family-weight-barra-liquidity", 0.4m),
                TopN = _topN,
                MinPresentFamilies = _minPresentFamilies,
                MinPrice = GetDecimalParameter("min-price", 5m),
                MinCircMv = GetDecimalParameter("min-circ-mv", 1_000_000m),
                MinTurnoverRate = GetDecimalParameter("min-turnover-rate", 0.3m),
                MinListedDays = _minListedDays,
                MaxSingleWeight = GetDecimalParameter("max-single-weight", 0.10m),
                WeightingMode = (GetParameter("weighting-mode") ?? "black-litterman").Trim(),
                BlackLittermanTau = GetDecimalParameter("black-litterman-tau", 0.05m),
                BlackLittermanRiskAversion = GetDecimalParameter("black-litterman-risk-aversion", 2.20m),
                BlackLittermanViewScale = GetDecimalParameter("black-litterman-view-scale", 0.08m),
                BlackLittermanViewConfidence = GetDecimalParameter("black-litterman-view-confidence", 0.65m),
                BlackLittermanPriorBlend = GetDecimalParameter("black-litterman-prior-blend", 0.40m),
                KellyWeightFraction = GetDecimalParameter("kelly-weight-fraction", 0.50m),
                KellyVarianceFloor = GetDecimalParameter("kelly-variance-floor", 0.35m),
                MinRegimeAdjustment = GetDecimalParameter("min-regime-adjustment", 0.55m)
            };
            _latestKellyScale = _kellyFallbackScale;
            _latestEffectiveTargetExposure = _targetPortfolioExposure * _latestKellyScale;
            _latestRegimeAdjustment = 1.0m;
            _dynamicRiskControls = GetBoolParameter("dynamic-risk-controls", false);
            _regimeBasisWeight = GetDecimalParameter("regime-basis-weight", 0.25m);
            _regimePcrWeight = GetDecimalParameter("regime-pcr-weight", 0.20m);
            _regimeVixWeight = GetDecimalParameter("regime-vix-weight", 0.15m);
            _extremeFearBoost = GetDecimalParameter("extreme-fear-boost", 0.10m);
            _vixHighThreshold = GetDecimalParameter("vix-high-threshold", 0.25m);
            _vixExtremeThreshold = GetDecimalParameter("vix-extreme-threshold", 0.35m);
            _dynamicStopLossPct = _stopLossPct;
            _dynamicTrailingStopPct = _trailingStopPct;
            _dynamicCooldownDays = _riskExitCooldownDays;

            SetStartDate(startDate);
            SetEndDate(endDate);
            SetAccountCurrency(Currencies.CNY);
            SetCash(initialCash);
            SetBenchmark(_ => 0m);
            SetRiskFreeInterestRateModel(new ChinaInterestRateProvider());
            var version = (GetParameter("version") ?? "V1").Trim().ToUpperInvariant();
            _version = version;
            SetAlgorithmId($"AShareMultiFamily{version}");
            _initialSyntheticCash = initialCash;
            _syntheticCash = initialCash;
            _previousEquity = initialCash;
            _liveSignalInterval = TimeSpan.FromMinutes(Math.Max(1, GetIntParameter("live-signal-interval-minutes", 3)));
            _livePriceSyncInterval = TimeSpan.FromSeconds(Math.Max(10, GetIntParameter("live-price-poll-interval-seconds", 60)));
            _syncLeanPortfolio = GetBoolParameter("sync-lean-portfolio", !LiveMode);
            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", false);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);

            AShareTushareFactorData.SetBaseDirectory(_factorDataPath);

            var isV3 = string.Equals(version, "V3", StringComparison.OrdinalIgnoreCase);
            var isV4 = string.Equals(version, "V4", StringComparison.OrdinalIgnoreCase);
            var isV6 = string.Equals(version, "V6", StringComparison.OrdinalIgnoreCase);
            var isV51 = version.StartsWith("V5.1", StringComparison.OrdinalIgnoreCase) ||
                        string.Equals(version, "V51", StringComparison.OrdinalIgnoreCase);
            var usesBarra = isV3 || isV4 || isV6 || isV51;
            if (usesBarra)
            {
                _barraFactorDataPath = ResolveOptionalFactorDataPath(GetParameter("barra-factor-path"));
                if (!string.IsNullOrWhiteSpace(_barraFactorDataPath))
                {
                    AShareBarraCNE5FactorData.SetBaseDirectory(_barraFactorDataPath);
                }
            }

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

                var factorSecurity = AddData<AShareTushareFactorData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _factorToUnderlying[factorSecurity.Symbol] = equity.Symbol;
                _anchorSymbol ??= equity.Symbol;

                if (usesBarra && !string.IsNullOrWhiteSpace(_barraFactorDataPath))
                {
                    var barraSecurity = AddData<AShareBarraCNE5FactorData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                }

                if (isV6)
                {
                    // V6: Subscribe to market sentiment data (single market-wide subscription)
                    if (_anchorSymbol == equity.Symbol)
                    {
                        AddData<AShareMarketSentimentData>(_anchorSymbol, Resolution.Daily, TimeZones.Shanghai, false);
                    }
                }
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No multi-family symbols were configured from factor path {_factorDataPath}");
            }

            Log($"AShareDataDrivenMultiFamilyAlgorithm initialized with {_factorToUnderlying.Count} factor subscriptions");
            Log(_syncLeanPortfolio
                ? "Execution mode: synthetic portfolio with Lean portfolio sync for backtest statistics."
                : "Execution mode: synthetic-only portfolio; no Lean orders or Lean portfolio sync.");
            Log(
                $"Synthetic execution enabled: factor path={_factorDataPath} fallback={_fallbackFactorDataPath ?? "-"} rebalance={_rebalanceFrequency} " +
                $"topN={_topN} exposure={_targetPortfolioExposure:F2} weighting={_signalSettings.WeightingMode} minScoreSpread={_minScoreSpread:F2} " +
                $"minPresentFamilies={_minPresentFamilies} minRegimeAdjustment={_signalSettings.MinRegimeAdjustment:F2}");
            Log(
                $"Risk overlay: portfolioKelly={_portfolioKellyFraction:F2} fallbackScale={_kellyFallbackScale:F2} " +
                $"stopLoss={_stopLossPct:P0} takeProfitActivation={_profitActivationPct:P0} trailingStop={_trailingStopPct:P0} cooldownDays={_riskExitCooldownDays}");
            if (_monteCarloEnabled)
            {
                Log(
                    $"Monte Carlo summary enabled: trials={_monteCarloTrials} horizonDays={_monteCarloHorizonDays} " +
                    $"blockSize={_monteCarloBlockSize} factorScale={_monteCarloFactorPerturbationScale:F2} seed={_monteCarloSeed}");
            }
            if (LiveMode)
            {
                Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.Every(_livePriceSyncInterval), RunLiveMonitoringCycle);
                Log(
                    $"Live monitoring schedule: signal every {_liveSignalInterval.TotalMinutes:F0} minute(s); " +
                    $"snapshot sync every {_livePriceSyncInterval.TotalSeconds:F0} second(s) using rt_k snapshot prices and factor CSV refresh.");
            }
            SetRuntimeStatistic("Exec Mode", _syncLeanPortfolio ? "synthetic+sync" : "synthetic");
            SetRuntimeStatistic("Syn Equity", _syntheticCash.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", "0");
            SetRuntimeStatistic("Kelly", _latestKellyScale.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Target Exp", _latestEffectiveTargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Regime", _latestRegimeAdjustment.ToString("F2", CultureInfo.InvariantCulture));
            if (LiveMode)
            {
                BootstrapLiveSessionState();
            }
        }

        public override void OnData(Slice slice)
        {
            var sessionDate = DateTime.MinValue;
            foreach (var pair in slice.Get<AShareTushareFactorData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                if (_factorToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _latestFactorsByUnderlying[underlying] = pair.Value;
                    sessionDate = pair.Value.EndTime.Date > sessionDate ? pair.Value.EndTime.Date : sessionDate;
                }
            }

            foreach (var pair in slice.Get<AShareBarraCNE5FactorData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                var underlying = pair.Key != null && pair.Key.HasUnderlying ? pair.Key.Underlying : pair.Key;
                if (underlying != null)
                {
                    // Always update cache — keeps the most recent snapshot per symbol.
                    // V4 uses carry-forward logic in IsFreshBarraFactorSnapshot so
                    // stale Barra data still contributes to scoring.
                    _latestBarraFactorsByUnderlying[underlying] = pair.Value;
                    if (sessionDate == DateTime.MinValue)
                    {
                        sessionDate = pair.Value.EndTime.Date > sessionDate ? pair.Value.EndTime.Date : sessionDate;
                    }
                }
            }

            // V6: Cache market sentiment data
            foreach (var pair in slice.Get<AShareMarketSentimentData>())
            {
                if (pair.Value != null)
                {
                    _latestSentimentData = pair.Value;
                    if (sessionDate == DateTime.MinValue)
                    {
                        sessionDate = pair.Value.EndTime.Date > sessionDate ? pair.Value.EndTime.Date : sessionDate;
                    }
                }
            }

            // V6: Cache IV data
            foreach (var pair in slice.Get<AShareImpliedVolatilityData>())
            {
                if (pair.Value != null)
                {
                    var underlying = pair.Key != null && pair.Key.HasUnderlying ? pair.Key.Underlying : pair.Key;
                    if (underlying != null)
                    {
                        _latestIvBySymbol[underlying] = pair.Value;
                    }
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
                    if (!ShouldRunLiveProcessingCycle(sessionDate.Date))
                    {
                        return;
                    }
                }
                ProcessSession(sessionDate.Date);
            }
        }

        public override void OnEndOfAlgorithm()
        {
            PublishSyntheticSummaryStatistics();
            PersistOutputs();
            Log($"Saved multi-family outputs: trades={_tradeReportPath}, daily={_dailySummaryPath}, allocations={_allocationReportPath}, family_exposures={_familyExposureReportPath}");
        }

        public static bool ShouldRebalance(DateTime currentDate, DateTime lastRebalanceDate, string frequency)
        {
            if (lastRebalanceDate == default)
            {
                return true;
            }

            var token = (frequency ?? "monthly").Trim().ToLowerInvariant();
            switch (token)
            {
                case "weekly":
                    return (currentDate.Date - lastRebalanceDate.Date).TotalDays >= 7;
                case "biweekly":
                    return (currentDate.Date - lastRebalanceDate.Date).TotalDays >= 14;
                default:
                    return currentDate.Year != lastRebalanceDate.Year || currentDate.Month != lastRebalanceDate.Month;
            }
        }

        public static bool IsFreshFactorSnapshot(AShareTushareFactorData factor, DateTime sessionDate)
        {
            return factor != null && factor.EndTime.Date == sessionDate.Date && factor.PresentFieldCount > 0;
        }

        public bool IsFreshBarraFactorSnapshot(AShareBarraCNE5FactorData factor, DateTime sessionDate)
        {
            if (factor == null || factor.PresentFactorCount <= 0)
            {
                return false;
            }

            // V4/V5.1: Barra data is monthly — accept the most recent snapshot on or before sessionDate
            if (string.Equals(_version, "V4", StringComparison.OrdinalIgnoreCase) ||
                _version.StartsWith("V5.1", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(_version, "V51", StringComparison.OrdinalIgnoreCase))
            {
                return factor.EndTime.Date <= sessionDate.Date;
            }

            // V3 and earlier: exact date match
            return factor.EndTime.Date == sessionDate.Date;
        }

        private decimal ComputeMacroRegimeAdjustment(IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var sample = factors.Values.FirstOrDefault(f => f != null);
            if (sample == null)
            {
                return 1.0m;
            }

            int macroFields = 0;
            decimal macroScore = 0m;

            var cpiYoy = sample.GetDecimal("cn_cpi_nt_yoy");
            if (cpiYoy.HasValue) { macroScore -= cpiYoy.Value * 0.3m; macroFields++; }

            var m2Yoy = sample.GetDecimal("cn_m_m2_yoy");
            if (m2Yoy.HasValue) { macroScore += m2Yoy.Value * 0.3m; macroFields++; }

            var pmi = sample.GetDecimal("cn_pmi_PMI020201");
            if (pmi.HasValue) { macroScore += (pmi.Value - 50m) * 0.02m; macroFields++; }

            var lpr1y = sample.GetDecimal("shibor_lpr_1y");
            if (lpr1y.HasValue) { macroScore -= lpr1y.Value * 0.1m; macroFields++; }

            if (macroFields == 0)
            {
                return 1.0m;
            }

            // Normalize: positive macro score → regime > 1 (cap at 1), negative → regime < 1 (floor at MinRegimeAdjustment)
            var rawRegime = 1.0m + macroScore * 0.05m;
            return Clamp(rawRegime, _signalSettings.MinRegimeAdjustment, 1.0m);
        }

        private decimal ComputeMacroRegimeAdjustmentV6(IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            // V6: Enhanced regime with sentiment signals
            // regime = 0.40*macroScore + 0.25*basisScore + 0.20*pcrScore + 0.15*vixScore
            var macroRegime = ComputeMacroRegimeAdjustment(factors);
            var macroWeight = 1.0m - _regimeBasisWeight - _regimePcrWeight - _regimeVixWeight;

            decimal basisScore = 0m;
            decimal pcrScore = 0m;
            decimal vixScore = 0m;

            if (_latestSentimentData != null)
            {
                // Basis: deep 贴水 → bullish contrarian (high score)
                if (_latestSentimentData.BasisComposite.HasValue)
                {
                    var basis = _latestSentimentData.BasisComposite.Value;
                    basisScore = -basis * 10m; // Scale: -2% basis → score 0.2
                    basisScore = Clamp(basisScore, -1m, 1m);
                }

                // PCR: high PCR → fear → contrarian bullish
                if (_latestSentimentData.PcrComposite.HasValue)
                {
                    var pcr = _latestSentimentData.PcrComposite.Value;
                    pcrScore = (pcr - 0.9m) * 2m; // PCR > 0.9 → positive
                    pcrScore = Clamp(pcrScore, -1m, 1m);
                }

                // VIX: low VIX → calm → bullish; extreme VIX → contrarian boost
                if (_latestSentimentData.VixComposite.HasValue)
                {
                    var vix = _latestSentimentData.VixComposite.Value / 100m; // Convert from percentage
                    if (vix > _vixExtremeThreshold)
                    {
                        vixScore = 0.3m; // Contrarian: extreme fear = potential bottom
                    }
                    else if (vix > _vixHighThreshold)
                    {
                        vixScore = -0.5m; // High VIX = caution
                    }
                    else
                    {
                        vixScore = 0.5m; // Low VIX = favorable
                    }
                }

                // Extreme fear boost
                if (_latestSentimentData.IsExtremeFear)
                {
                    vixScore += _extremeFearBoost;
                }
            }

            var rawRegime = macroWeight * macroRegime + _regimeBasisWeight * (1m + basisScore) + _regimePcrWeight * (1m + pcrScore) + _regimeVixWeight * (1m + vixScore);
            return Clamp(rawRegime, _signalSettings.MinRegimeAdjustment, 1.0m);
        }

        private void ComputeDynamicRiskParams()
        {
            if (!_dynamicRiskControls || _latestSentimentData == null)
            {
                _dynamicStopLossPct = _stopLossPct;
                _dynamicTrailingStopPct = _trailingStopPct;
                _dynamicCooldownDays = _riskExitCooldownDays;
                return;
            }

            var vixLevel = _latestSentimentData.VixComposite.HasValue
                ? _latestSentimentData.VixComposite.Value / 100m
                : 0.20m;

            // VIX-adjusted multipliers
            decimal vixMultiplier;
            if (vixLevel > _vixExtremeThreshold)
            {
                vixMultiplier = 0.7m; // Tight stops in extreme volatility
            }
            else if (vixLevel > _vixHighThreshold)
            {
                vixMultiplier = 0.85m;
            }
            else
            {
                vixMultiplier = 1.0m;
            }

            // Extreme fear: widen stops (give positions room to recover)
            var fearMultiplier = _latestSentimentData.IsExtremeFear ? 1.3m : 1.0m;

            _dynamicStopLossPct = _stopLossPct * vixMultiplier * fearMultiplier;
            _dynamicTrailingStopPct = _trailingStopPct * vixMultiplier * fearMultiplier;
            _dynamicCooldownDays = vixLevel > 0.30m
                ? (int)(_riskExitCooldownDays * 1.5m)
                : _riskExitCooldownDays;
        }

        private void RunLiveMonitoringCycle()
        {
            if (!LiveMode)
            {
                return;
            }

            var sessionDate = ResolveLiveSessionDate();
            EnsureLiveFactorSnapshots(sessionDate);
            if (ShouldRunLiveProcessingCycle(sessionDate))
            {
                ProcessSession(sessionDate);
                return;
            }

            SyncLiveSnapshotPortfolioState(sessionDate);
        }

        private void BootstrapLiveSessionState()
        {
            RestoreSyntheticPortfolioState();
            var bootstrapDate = ResolveBootstrapSessionDate();
            if (bootstrapDate == default)
            {
                Log("[bootstrap] skipped: unable to resolve live trade date from snapshot or runtime clock.");
                return;
            }

            var snapshotPrices = LoadLiveSnapshotPrices();
            var loadedCount = EnsureLiveFactorSnapshots(bootstrapDate, forceRefresh: true);
            Log(
                $"[bootstrap] trade_date={bootstrapDate:yyyyMMdd} snapshot_prices={snapshotPrices.Count} " +
                $"loaded_factors={loadedCount}");
            ProcessSession(bootstrapDate, true);
        }

        private void SyncLiveSnapshotPortfolioState(DateTime sessionDate)
        {
            if (!LiveMode || !_syncLeanPortfolio)
            {
                return;
            }

            var prices = BuildPriceMap();
            if (prices.Count == 0)
            {
                return;
            }

            var equity = ComputeEquity(prices);
            SyncLeanPortfolioState(prices);
            SetRuntimeStatistic("Syn Equity", equity.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", _positions.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Last Session", sessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        }

        private void ProcessSession(DateTime sessionDate, bool force = false)
        {
            var firstUpdateOfSession = _lastProcessedDate != sessionDate;
            if (firstUpdateOfSession)
            {
                _lastProcessedDate = sessionDate;
                foreach (var position in _positions.Values)
                {
                    position.HoldingDays += 1;
                }
            }

            PruneExpiredRiskCooldowns(sessionDate);

            // V6: Compute dynamic risk parameters before risk exits
            if (_dynamicRiskControls && _latestSentimentData != null)
            {
                ComputeDynamicRiskParams();
            }

            var prices = BuildPriceMap();
            var riskExitSummary = ApplyRiskManagementExits(sessionDate, prices);
            if (riskExitSummary.TotalExits > 0)
            {
                RemoveRiskBlockedTargets(sessionDate);
                prices = BuildPriceMap();
                if (_syncLeanPortfolio)
                {
                    SyncLeanPortfolioState(prices);
                }
            }

            var eligibleFactors = _latestFactorsByUnderlying
                .Where(pair =>
                    IsFreshFactorSnapshot(pair.Value, sessionDate) &&
                    IsEligibleFactor(pair.Value) &&
                    !IsSymbolInRiskCooldown(pair.Key, sessionDate))
                .ToDictionary(pair => pair.Key, pair => pair.Value);

            var rebalance = false;
            var liveAligned = false;
            var scoreSpread = _latestScoreSpread;
            var turnover = riskExitSummary.Turnover;
            var shouldEvaluateSignals = LiveMode || _lastSignalEvaluationDate != sessionDate;
            if (shouldEvaluateSignals)
            {
                _lastSignalEvaluationDate = sessionDate;
                var shouldRefreshTargets = LiveMode || ShouldRebalance(sessionDate, _lastRebalanceDate, _rebalanceFrequency);
                if (shouldRefreshTargets)
                {
                    var barraFactors = _latestBarraFactorsByUnderlying.Count > 0
                        ? _latestBarraFactorsByUnderlying
                            .Where(p => IsFreshBarraFactorSnapshot(p.Value, sessionDate))
                            .ToDictionary(p => p.Key, p => p.Value)
                        : null;
                    var familyScores = AShareMultiFamilySignalModel.ComputeFamilyScores(eligibleFactors, _signalSettings, barraFactors, _latestSentimentData);
                    var scores = AShareMultiFamilySignalModel.ComputeComposite(familyScores, _signalSettings);
                    ReplaceLatestScores(scores);
                    ReplaceLatestFamilyScores(familyScores);
                    _latestScoreSpread = GetScoreSpread(scores);
                    scoreSpread = _latestScoreSpread;

                    var kellyState = ComputePortfolioKellyState();
                    _latestKellyScale = kellyState.ExposureScale;

                    _latestRegimeAdjustment = string.Equals(_version, "V6", StringComparison.OrdinalIgnoreCase)
                        ? ComputeMacroRegimeAdjustmentV6(eligibleFactors)
                        : ComputeMacroRegimeAdjustment(eligibleFactors);
                    _latestEffectiveTargetExposure = Math.Min(1m, _targetPortfolioExposure * _latestKellyScale * _latestRegimeAdjustment);

                    LogSignalRanking(sessionDate, eligibleFactors.Count, scores);
                    Log(
                        $"[kelly] trade_date={sessionDate:yyyyMMdd} closed_trades={kellyState.ClosedTrades} " +
                        $"win_rate={kellyState.WinRate:P1} payoff={kellyState.PayoffRatio:F2} raw={kellyState.RawKellyFraction:F2} " +
                        $"scale={_latestKellyScale:F2} regime={_latestRegimeAdjustment:F2} effective_exposure={_latestEffectiveTargetExposure:F2}");

                    var targets = AShareMultiFamilySignalModel.SelectPortfolio(
                        scores,
                        familyScores,
                        eligibleFactors,
                        _topN,
                        _minScoreSpread,
                        _latestEffectiveTargetExposure,
                        _signalSettings.WeightingMode,
                        _signalSettings);

                    if (targets.Count == 0)
                    {
                        _latestTargetWeightsByUnderlying.Clear();
                        Log($"{sessionDate:yyyy-MM-dd} rebalance skipped: eligible={eligibleFactors.Count} scoreSpread={scoreSpread:F4} threshold={_minScoreSpread:F4}");
                    }
                    else
                    {
                        ReplaceLatestTargets(targets);
                        var tradeCountBeforeRebalance = _tradeRows.Count;
                        turnover += ApplyTargetPortfolio(sessionDate, targets, prices);
                        var executedTrades = _tradeRows.Count > tradeCountBeforeRebalance;
                        if (LiveMode)
                        {
                            rebalance = executedTrades;
                            if (executedTrades)
                            {
                                _syntheticRebalanceCount += 1;
                                _lastRebalanceDate = sessionDate;
                            }

                            Log(
                                $"{sessionDate:yyyy-MM-dd} live-cycle -> eligible={eligibleFactors.Count} selected={targets.Count} " +
                                $"turnover={turnover:P2} scoreSpread={scoreSpread:F4} kelly={_latestKellyScale:F2} regime={_latestRegimeAdjustment:F2} executed={(executedTrades ? 1 : 0)}");
                        }
                        else
                        {
                            rebalance = true;
                            _syntheticRebalanceCount += 1;
                            _lastRebalanceDate = sessionDate;
                            Log(
                                $"{sessionDate:yyyy-MM-dd} rebalance -> eligible={eligibleFactors.Count} selected={targets.Count} " +
                                $"turnover={turnover:P2} scoreSpread={scoreSpread:F4} kelly={_latestKellyScale:F2} regime={_latestRegimeAdjustment:F2}");
                        }
                        LogTargetPreview(sessionDate, targets);
                    }
                }
            }

            if (!LiveMode && !rebalance && _latestTargetWeightsByUnderlying.Count > 0)
            {
                var retainedTargets = BuildTargetsFromLatestWeights(sessionDate);
                if (retainedTargets.Count > 0)
                {
                    var tradeCountBeforeAlignment = _tradeRows.Count;
                    turnover += ApplyTargetPortfolio(sessionDate, retainedTargets, prices);
                    liveAligned = _tradeRows.Count > tradeCountBeforeAlignment;
                    if (liveAligned)
                    {
                        Log(
                            $"{sessionDate:yyyy-MM-dd} live-align -> selected={retainedTargets.Count} " +
                            $"turnover={turnover:P2} scoreSpread={scoreSpread:F4} kelly={_latestKellyScale:F2}");
                    }
                }
            }

            scoreSpread = _latestScoreSpread;
            var selectedCount = _latestTargetWeightsByUnderlying.Count > 0
                ? _latestTargetWeightsByUnderlying.Count
                : _positions.Count;
            var equity = ComputeEquity(prices);
            var invested = ComputeInvestedValue(prices);
            var grossReturn = _previousEquity > 0m ? equity / _previousEquity - 1m : 0m;
            var weightMap = BuildCurrentWeightMap(prices, equity);
            UpsertAllocations(sessionDate, prices);
            var familyExposure = AShareMultiFamilySignalModel.ComputeFamilyExposure(weightMap, _latestFamilyScoresByUnderlying);
            if (_syncLeanPortfolio)
            {
                SyncLeanPortfolioState(prices);
            }

            UpsertDailySummary(new DailySummaryRow
            {
                TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Equity = equity,
                Cash = _syntheticCash,
                Invested = invested,
                GrossReturn = grossReturn,
                NetReturn = grossReturn,
                Holdings = _positions.Count,
                EligibleSymbols = eligibleFactors.Count,
                SelectedSymbols = selectedCount,
                Turnover = turnover,
                ScoreSpread = scoreSpread,
                Rebalanced = rebalance || liveAligned || riskExitSummary.TotalExits > 0,
                KellyScale = _latestKellyScale,
                EffectiveExposure = _latestEffectiveTargetExposure,
                RegimeAdjustment = _latestRegimeAdjustment,
                StopLossExits = riskExitSummary.StopLossExits,
                TrailingStopExits = riskExitSummary.TrailingStopExits,
                BasisComposite = _latestSentimentData?.BasisComposite,
                PcrComposite = _latestSentimentData?.PcrComposite,
                VixComposite = _latestSentimentData?.VixComposite,
                DynamicStopLoss = _dynamicStopLossPct,
                DynamicTrailingStop = _dynamicTrailingStopPct
            });

            UpsertFamilyExposure(new FamilyExposureRow
            {
                TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Holdings = _positions.Count,
                MomentumReversal = familyExposure["momentum_reversal"],
                ValueQuality = familyExposure["value_quality"],
                MoneyFlow = familyExposure["money_flow"],
                EarningsSurprise = familyExposure["earnings_surprise"],
                ChipCost = familyExposure["chip_cost"],
                EtfPremium = familyExposure["etf_premium"],
                SectorRotation = familyExposure["sector_rotation"],
                MarginSignal = familyExposure["margin_signal"],
                NorthboundFlow = familyExposure["northbound_flow"],
                MultiFactor = familyExposure["multi_factor"],
                AnalystSignal = familyExposure["analyst_signal"],
                MacroRate = familyExposure["macro_rate"],
                BarraMomentum = familyExposure["barra_momentum"],
                BarraValue = familyExposure["barra_value"],
                BarraQuality = familyExposure["barra_quality"],
                LowVolatility = familyExposure["low_volatility"],
                SizeTilt = familyExposure["size_tilt"],
                LiquidityPremium = familyExposure["liquidity_premium"],
                ChipConcentration = familyExposure["chip_concentration"],
                RateSensitivity = familyExposure["rate_sensitivity"],
                BasisSentiment = familyExposure["basis_sentiment"],
                OptionsPcr = familyExposure["options_pcr"],
                MarginShortRatio = familyExposure["margin_short_ratio"],
                BarraBeta = familyExposure["barra_beta"],
                BarraNlsize = familyExposure["barra_nlsize"],
                BarraResvol = familyExposure["barra_resvol"],
                BarraLiquidity = familyExposure["barra_liquidity"]
            });

            SetRuntimeStatistic("Syn Equity", equity.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", _positions.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Last Session", sessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Signals", selectedCount.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Score Spr", scoreSpread.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Turnover", turnover.ToString("P1", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Kelly", _latestKellyScale.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Target Exp", _latestEffectiveTargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Regime", _latestRegimeAdjustment.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Risk Exit", riskExitSummary.TotalExits.ToString(CultureInfo.InvariantCulture));
            if (ShouldPersistRuntimeOutputs(rebalance || liveAligned || riskExitSummary.TotalExits > 0, firstUpdateOfSession))
            {
                PersistOutputs();
                _lastRuntimePersistUtc = UtcTime;
            }

            if (ShouldEmitRuntimeStatusLog(rebalance || liveAligned || riskExitSummary.TotalExits > 0, firstUpdateOfSession))
            {
                LogDailySnapshot(
                    sessionDate,
                    equity,
                    invested,
                    eligibleFactors.Count,
                    selectedCount,
                    scoreSpread,
                    turnover,
                    rebalance || liveAligned || riskExitSummary.TotalExits > 0,
                    riskExitSummary,
                    _latestKellyScale,
                    _latestEffectiveTargetExposure,
                    _latestRegimeAdjustment);
                LogPortfolioSnapshot(sessionDate, prices, weightMap, equity);
                _lastPortfolioLogUtc = UtcTime;
            }

            if (LiveMode || force)
            {
                _lastLiveProcessingTime = ResolveLiveProcessingTimestamp();
            }
            _previousEquity = equity;
        }

        private RiskExitSummary ApplyRiskManagementExits(DateTime sessionDate, IReadOnlyDictionary<Symbol, decimal> prices)
        {
            var summary = new RiskExitSummary();
            if (_positions.Count == 0 || prices == null || prices.Count == 0)
            {
                _latestStopLossExitCount = 0;
                _latestTrailingStopExitCount = 0;
                return summary;
            }

            var equityBeforeExits = Math.Max(ComputeEquity(prices), 1m);
            var exits = new List<SyntheticOrderChange>();

            foreach (var pair in _positions.ToList())
            {
                if (!prices.TryGetValue(pair.Key, out var price) || price <= 0m)
                {
                    continue;
                }

                var position = pair.Value;
                position.LastPrice = price;
                position.PeakPrice = Math.Max(position.PeakPrice, price);
                _positions[pair.Key] = position;

                var reason = ResolveRiskExitReason(position, price);
                if (reason == null)
                {
                    continue;
                }

                exits.Add(new SyntheticOrderChange
                {
                    Symbol = pair.Key,
                    Price = price,
                    CurrentQuantity = position.Quantity,
                    TargetQuantity = 0,
                    DeltaQuantity = -position.Quantity,
                    Score = _latestScoresByUnderlying.GetValueOrDefault(pair.Key),
                    Reason = reason
                });
            }

            foreach (var exit in exits.OrderBy(change => change.Symbol.Value, StringComparer.Ordinal))
            {
                if (exit.CurrentQuantity <= 0)
                {
                    continue;
                }

                summary.Turnover += exit.CurrentQuantity * exit.Price / equityBeforeExits;
                ExecuteOrderChange(sessionDate, exit);
                _riskCooldownUntilBySymbol[exit.Symbol] = sessionDate.AddDays(_riskExitCooldownDays);
                if (string.Equals(exit.Reason, "STOP_LOSS", StringComparison.Ordinal))
                {
                    summary.StopLossExits += 1;
                }
                else if (string.Equals(exit.Reason, "TRAILING_STOP", StringComparison.Ordinal))
                {
                    summary.TrailingStopExits += 1;
                }
            }

            summary.TotalExits = summary.StopLossExits + summary.TrailingStopExits;
            _latestStopLossExitCount = summary.StopLossExits;
            _latestTrailingStopExitCount = summary.TrailingStopExits;

            if (summary.TotalExits > 0)
            {
                Log(
                    $"[risk exits] trade_date={sessionDate:yyyyMMdd} stop_loss={summary.StopLossExits} " +
                    $"trailing_stop={summary.TrailingStopExits} turnover={summary.Turnover:P2}");
            }

            return summary;
        }

        private string ResolveRiskExitReason(SyntheticPosition position, decimal currentPrice)
        {
            if (position == null || position.Quantity <= 0 || currentPrice <= 0m)
            {
                return null;
            }

            var averagePrice = position.AveragePrice > 0m ? position.AveragePrice : currentPrice;
            if (averagePrice <= 0m)
            {
                return null;
            }

            if (_dynamicRiskControls && _stopLossPct > 0m && currentPrice <= averagePrice * (1m - _dynamicStopLossPct))
            {
                return "STOP_LOSS";
            }

            var profitActivated =
                _profitActivationPct > 0m &&
                position.HoldingDays >= _minHoldDaysForProfitProtection &&
                position.PeakPrice >= averagePrice * (1m + _profitActivationPct);
            if (profitActivated && _dynamicRiskControls && _dynamicTrailingStopPct > 0m && currentPrice <= position.PeakPrice * (1m - _dynamicTrailingStopPct))
            {
                return "TRAILING_STOP";
            }

            if (profitActivated && !_dynamicRiskControls && _trailingStopPct > 0m && currentPrice <= position.PeakPrice * (1m - _trailingStopPct))
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

        private bool IsEligibleFactor(AShareTushareFactorData factor)
        {
            if (factor == null)
            {
                return false;
            }

            // Min price filter
            var close = factor.Close;
            if (close.HasValue && close.Value < _signalSettings.MinPrice)
            {
                return false;
            }

            // Min circ_mv filter
            var circMv = factor.CircMv;
            if (circMv.HasValue && circMv.Value < _signalSettings.MinCircMv)
            {
                return false;
            }

            // Min turnover rate filter
            var turnoverRate = factor.TurnoverRate;
            if (turnoverRate.HasValue && turnoverRate.Value < _signalSettings.MinTurnoverRate)
            {
                return false;
            }

            // Min present families filter
            if (factor.PresentFieldCount < _minPresentFamilies)
            {
                return false;
            }

            return true;
        }

        private KellySizingState ComputePortfolioKellyState()
        {
            var realizedReturns = ExtractClosedTradeReturns();
            var recentReturns = realizedReturns
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

            var wins = recentReturns.Where(value => value > 0m).ToList();
            var losses = recentReturns.Where(value => value < 0m).ToList();
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
            var rawKelly = payoffRatio > 0m
                ? winRate - (1m - winRate) / payoffRatio
                : 0m;
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

        private List<decimal> ExtractClosedTradeReturns()
        {
            var closedReturns = new List<decimal>();
            var openLotsBySymbol = new Dictionary<string, Queue<SyntheticOpenLot>>(StringComparer.Ordinal);

            foreach (var row in _tradeRows)
            {
                if (row == null || row.Quantity <= 0 || row.Price <= 0m || string.IsNullOrWhiteSpace(row.Symbol))
                {
                    continue;
                }

                if (string.Equals(row.Action, "BUY", StringComparison.OrdinalIgnoreCase))
                {
                    if (!openLotsBySymbol.TryGetValue(row.Symbol, out var openLots))
                    {
                        openLots = new Queue<SyntheticOpenLot>();
                        openLotsBySymbol[row.Symbol] = openLots;
                    }

                    openLots.Enqueue(new SyntheticOpenLot
                    {
                        Quantity = row.Quantity,
                        Price = row.Price,
                        FeePerShare = row.Fee / row.Quantity
                    });
                    continue;
                }

                if (!string.Equals(row.Action, "SELL", StringComparison.OrdinalIgnoreCase) ||
                    !openLotsBySymbol.TryGetValue(row.Symbol, out var queuedLots) ||
                    queuedLots.Count == 0)
                {
                    continue;
                }

                var sellQuantityRemaining = row.Quantity;
                var sellFeePerShare = row.Fee / row.Quantity;
                while (sellQuantityRemaining > 0 && queuedLots.Count > 0)
                {
                    var lot = queuedLots.Peek();
                    var matchedQuantity = Math.Min(sellQuantityRemaining, lot.Quantity);
                    var matchedQuantityDecimal = matchedQuantity;
                    var costBasis = Math.Max(1m, matchedQuantityDecimal * lot.Price);
                    var profitLoss =
                        (row.Price - lot.Price) * matchedQuantityDecimal -
                        (lot.FeePerShare + sellFeePerShare) * matchedQuantityDecimal;
                    closedReturns.Add(profitLoss / costBasis);

                    lot.Quantity -= matchedQuantity;
                    sellQuantityRemaining -= matchedQuantity;
                    if (lot.Quantity <= 0)
                    {
                        queuedLots.Dequeue();
                    }
                }
            }

            return closedReturns;
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue)
            {
                return minValue;
            }
            return value > maxValue ? maxValue : value;
        }

        private bool ShouldRunLiveProcessingCycle(DateTime sessionDate)
        {
            if (!LiveMode)
            {
                return true;
            }

            if (_lastProcessedDate != sessionDate || _lastLiveProcessingTime == default)
            {
                return true;
            }

            var currentTimestamp = ResolveLiveProcessingTimestamp();
            if (currentTimestamp <= _lastLiveProcessingTime)
            {
                return false;
            }

            return currentTimestamp - _lastLiveProcessingTime >= _liveSignalInterval;
        }

        private DateTime ResolveLiveProcessingTimestamp()
        {
            if (_anchorSymbol != null && Securities.TryGetValue(_anchorSymbol, out var security) && security.LocalTime != default)
            {
                return security.LocalTime;
            }

            if (Time != default)
            {
                return Time;
            }

            return DateTime.UtcNow;
        }

        private DateTime ResolveLiveSessionDate()
        {
            if (_anchorSymbol != null && Securities.TryGetValue(_anchorSymbol, out var security))
            {
                var localTime = security.LocalTime;
                if (localTime != default)
                {
                    return localTime.Date;
                }
            }

            return Time.Date;
        }

        private DateTime ResolveBootstrapSessionDate()
        {
            if (!string.IsNullOrWhiteSpace(_livePriceSnapshotPath) && File.Exists(_livePriceSnapshotPath))
            {
                try
                {
                    var payloadText = File.ReadAllText(_livePriceSnapshotPath);
                    var payload = JsonConvert.DeserializeObject<LivePriceSnapshotPayload>(payloadText) ?? new LivePriceSnapshotPayload();
                    if (TryParseTradeDate(payload.TradeDate, out var parsedTradeDate))
                    {
                        return parsedTradeDate;
                    }

                    foreach (var quote in payload.Quotes ?? new List<LivePriceSnapshotQuote>())
                    {
                        if (TryParseTradeDate(quote.TradeDate, out parsedTradeDate))
                        {
                            return parsedTradeDate;
                        }
                    }
                }
                catch (Exception ex)
                {
                    Log($"[bootstrap] failed to parse live snapshot {_livePriceSnapshotPath}: {ex.Message}");
                }
            }

            if (Time != default)
            {
                return Time.Date;
            }

            return DateTime.UtcNow.Date;
        }

        private int EnsureLiveFactorSnapshots(DateTime sessionDate, bool forceRefresh = false)
        {
            if (!LiveMode)
            {
                return 0;
            }

            var freshFactorCount = _latestFactorsByUnderlying.Values.Count(factor => IsFreshFactorSnapshot(factor, sessionDate));
            if (!forceRefresh && _lastLiveFactorDiskRefreshDate == sessionDate && freshFactorCount > 0)
            {
                return freshFactorCount;
            }

            var loadedCount = 0;
            var primaryCount = 0;
            var fallbackCount = 0;
            var carryForwardCount = 0;
            foreach (var underlying in _factorToUnderlying.Values.Distinct())
            {
                if (TryLoadFactorSnapshotFromDisk(underlying, sessionDate, out var factor, out var usedFallback, out var carryForwarded))
                {
                    _latestFactorsByUnderlying[underlying] = factor;
                    loadedCount += 1;
                    if (usedFallback)
                    {
                        fallbackCount += 1;
                    }
                    else
                    {
                        primaryCount += 1;
                    }
                    if (carryForwarded)
                    {
                        carryForwardCount += 1;
                    }
                }
            }

            _lastLiveFactorDiskRefreshDate = loadedCount > 0 ? sessionDate : default;
            Log(
                $"[factor sync] trade_date={sessionDate:yyyyMMdd} loaded={loadedCount} " +
                $"primary={primaryCount} fallback={fallbackCount} carry_forward={carryForwardCount} " +
                $"fresh_before={freshFactorCount} source=disk forced={(forceRefresh ? 1 : 0)}");
            return loadedCount;
        }

        private bool TryLoadFactorSnapshotFromDisk(
            Symbol underlying,
            DateTime sessionDate,
            out AShareTushareFactorData factor,
            out bool usedFallback,
            out bool carryForwarded)
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

        private bool TryLoadFactorSnapshotFromPath(
            Symbol underlying,
            DateTime sessionDate,
            string factorRootPath,
            out AShareTushareFactorData factor,
            out DateTime matchedTradeDate)
        {
            factor = null;
            matchedTradeDate = default;
            if (string.IsNullOrWhiteSpace(factorRootPath))
            {
                return false;
            }

            var factorPath = AShareTushareFactorData.ResolveSourcePath(underlying, factorRootPath);
            if (!File.Exists(factorPath))
            {
                return false;
            }

            var targetTradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            string matchedLine = null;
            string headerLine = null;
            foreach (var line in File.ReadLines(factorPath).Reverse())
            {
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }

                if (line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
                {
                    if (headerLine == null)
                    {
                        headerLine = line;
                    }
                    continue;
                }

                var separatorIndex = line.IndexOf(',');
                if (separatorIndex <= 0)
                {
                    continue;
                }

                var lineTradeDate = line.Substring(0, separatorIndex);
                if (string.CompareOrdinal(lineTradeDate, targetTradeDate) > 0)
                {
                    continue;
                }

                matchedLine = line;
                break;
            }

            if (string.IsNullOrWhiteSpace(matchedLine))
            {
                return false;
            }

            // Parse using the generic reader logic: header-discovery + Fields dictionary
            if (!TryParseTradeDate(matchedLine.Substring(0, matchedLine.IndexOf(',')), out matchedTradeDate))
            {
                return false;
            }

            // Parse header
            string[] header = null;
            if (!string.IsNullOrWhiteSpace(headerLine))
            {
                header = headerLine.Split(',');
            }

            var csv = matchedLine.Split(',');
            if (csv.Length < 2)
            {
                return false;
            }

            var fields = new Dictionary<string, decimal?>(StringComparer.Ordinal);
            var closeValue = 0m;

            for (var i = 1; i < csv.Length && (header == null || i < header.Length); i++)
            {
                var colName = header != null ? header[i] : $"col{i}";
                if (string.Equals(colName, "trade_date", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(colName, "sector_code", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var parsed = ParseNullableDecimal(csv[i]);
                fields[colName] = parsed;

                if (string.Equals(colName, "close", StringComparison.OrdinalIgnoreCase) && parsed.HasValue)
                {
                    closeValue = parsed.Value;
                }
            }

            factor = new AShareTushareFactorData
            {
                Symbol = underlying,
                Time = sessionDate.Date,
                EndTime = sessionDate.Date,
                Value = closeValue,
                Fields = fields
            };
            return true;
        }

        private void ReplaceLatestScores(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            _latestScoresByUnderlying.Clear();
            if (scores == null)
            {
                return;
            }

            foreach (var pair in scores)
            {
                _latestScoresByUnderlying[pair.Key] = pair.Value;
            }
        }

        private void ReplaceLatestFamilyScores(IReadOnlyDictionary<Symbol, Dictionary<string, decimal>> familyScores)
        {
            _latestFamilyScoresByUnderlying.Clear();
            if (familyScores == null)
            {
                return;
            }

            foreach (var pair in familyScores)
            {
                _latestFamilyScoresByUnderlying[pair.Key] = new Dictionary<string, decimal>(pair.Value, StringComparer.Ordinal);
            }
        }

        private void ReplaceLatestTargets(IReadOnlyList<AShareMultiFamilyTarget> targets)
        {
            _latestTargetWeightsByUnderlying.Clear();
            if (targets == null)
            {
                return;
            }

            foreach (var target in targets)
            {
                _latestTargetWeightsByUnderlying[target.Symbol] = target.Weight;
            }
        }

        private List<AShareMultiFamilyTarget> BuildTargetsFromLatestWeights(DateTime sessionDate)
        {
            return _latestTargetWeightsByUnderlying
                .Where(pair => !IsSymbolInRiskCooldown(pair.Key, sessionDate))
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                .Select(pair => new AShareMultiFamilyTarget
                {
                    Symbol = pair.Key,
                    Weight = pair.Value,
                    Score = _latestScoresByUnderlying.TryGetValue(pair.Key, out var score) ? score : 0m
                })
                .ToList();
        }

        private decimal ApplyTargetPortfolio(DateTime sessionDate, IReadOnlyList<AShareMultiFamilyTarget> targets, IReadOnlyDictionary<Symbol, decimal> prices)
        {
            var currentEquity = ComputeEquity(prices);
            var currentValues = _positions.ToDictionary(
                pair => pair.Key,
                pair => prices.TryGetValue(pair.Key, out var price) ? pair.Value.Quantity * price : 0m);
            var targetValues = targets.ToDictionary(target => target.Symbol, target => currentEquity * target.Weight);

            var turnover = 0m;
            var allSymbols = new HashSet<Symbol>(_positions.Keys);
            foreach (var symbol in targetValues.Keys)
            {
                allSymbols.Add(symbol);
            }

            var orders = new List<SyntheticOrderChange>();
            foreach (var symbol in allSymbols)
            {
                if (!prices.TryGetValue(symbol, out var price) || price <= 0m)
                {
                    continue;
                }

                var currentQuantity = _positions.TryGetValue(symbol, out var position) ? position.Quantity : 0;
                var currentValue = currentValues.TryGetValue(symbol, out var existingValue) ? existingValue : 0m;
                var targetValue = targetValues.TryGetValue(symbol, out var desiredValue) ? desiredValue : 0m;
                var targetQuantity = CalculateTargetQuantity(targetValue, price);
                var deltaQuantity = targetQuantity - currentQuantity;
                if (deltaQuantity == 0)
                {
                    continue;
                }

                turnover += Math.Abs(targetValue - currentValue) / Math.Max(currentEquity, 1m);
                orders.Add(new SyntheticOrderChange
                {
                    Symbol = symbol,
                    Price = price,
                    CurrentQuantity = currentQuantity,
                    TargetQuantity = targetQuantity,
                    DeltaQuantity = deltaQuantity,
                    Score = targets.FirstOrDefault(target => target.Symbol == symbol)?.Score ?? 0m,
                    Reason = "TARGET_ALIGN"
                });
            }

            foreach (var order in orders.Where(change => change.DeltaQuantity < 0).OrderBy(change => change.Symbol.Value, StringComparer.Ordinal))
            {
                ExecuteOrderChange(sessionDate, order);
            }

            foreach (var order in orders.Where(change => change.DeltaQuantity > 0).OrderBy(change => change.Symbol.Value, StringComparer.Ordinal))
            {
                ExecuteOrderChange(sessionDate, order);
            }

            if (_syncLeanPortfolio)
            {
                SyncLeanPortfolioState(prices);
            }

            return turnover;
        }

        private void ExecuteOrderChange(DateTime sessionDate, SyntheticOrderChange order)
        {
            var deltaQuantity = order.DeltaQuantity;
            if (deltaQuantity == 0 || order.Price <= 0m)
            {
                return;
            }

            if (deltaQuantity > 0)
            {
                deltaQuantity = ClampBuyQuantityToCash(order.Symbol, deltaQuantity, order.Price);
            }
            else
            {
                // T+1 enforcement: cannot sell same-day purchases
                if (_positions.TryGetValue(order.Symbol, out var existingPos))
                {
                    ReleaseT1Restricted(existingPos, sessionDate);
                    var maxSellable = existingPos.AvailableQuantity;
                    if (maxSellable <= 0)
                    {
                        // Entire position was bought today — cannot sell any
                        Log($"[T+1 block] {ToTsCode(order.Symbol)} sell blocked: entire position bought on {existingPos.BuyDate:yyyyMMdd}, available=0");
                        return;
                    }
                    if (Math.Abs(deltaQuantity) > maxSellable)
                    {
                        var originalSell = Math.Abs(deltaQuantity);
                        deltaQuantity = -maxSellable;
                        Log($"[T+1 partial] {ToTsCode(order.Symbol)} sell reduced: {originalSell} -> {maxSellable} (T+1 restricted={existingPos.T1RestrictedQuantity})");
                    }
                }

                if (order.CurrentQuantity <= 0)
                {
                    deltaQuantity = 0;
                }
                else if (Math.Abs(deltaQuantity) > order.CurrentQuantity)
                {
                    deltaQuantity = -order.CurrentQuantity;
                }
            }

            if (deltaQuantity == 0)
            {
                return;
            }

            var isSell = deltaQuantity < 0;
            var absoluteQuantity = Math.Abs(deltaQuantity);
            var tradeValue = absoluteQuantity * order.Price;
            var fee = EstimateTradeFee(order.Symbol, tradeValue, isSell);

            if (isSell)
            {
                _syntheticCash += tradeValue - fee;
            }
            else
            {
                _syntheticCash -= tradeValue + fee;
            }

            if (!_positions.TryGetValue(order.Symbol, out var position))
            {
                position = new SyntheticPosition
                {
                    Symbol = order.Symbol,
                    Quantity = 0,
                    AveragePrice = 0m,
                    LastPrice = order.Price,
                    PeakPrice = order.Price,
                    HoldingDays = 0,
                    BuyDate = DateTime.MinValue,
                    T1RestrictedQuantity = 0
                };
            }

            var quantityBefore = position.Quantity;
            if (deltaQuantity > 0)
            {
                // T+1 tracking: newly bought shares are restricted until next trading day
                ReleaseT1Restricted(position, sessionDate);
                var quantityAfterBuy = Math.Max(0, position.Quantity + deltaQuantity);
                var costBefore = position.AveragePrice > 0m
                    ? position.AveragePrice * position.Quantity
                    : 0m;
                var totalCost = costBefore + (order.Price * deltaQuantity);
                position.Quantity = quantityAfterBuy;
                position.AveragePrice = position.Quantity > 0
                    ? totalCost / position.Quantity
                    : 0m;
                position.PeakPrice = Math.Max(position.PeakPrice, order.Price);
                position.T1RestrictedQuantity += deltaQuantity;
                position.BuyDate = sessionDate;
            }
            else
            {
                ReleaseT1Restricted(position, sessionDate);
                position.Quantity = Math.Max(0, position.Quantity + deltaQuantity);
                if (position.Quantity == 0)
                {
                    position.AveragePrice = 0m;
                    position.PeakPrice = 0m;
                    position.T1RestrictedQuantity = 0;
                    position.BuyDate = DateTime.MinValue;
                }
                else if (position.T1RestrictedQuantity > position.Quantity)
                {
                    position.T1RestrictedQuantity = position.Quantity;
                }
            }
            position.LastPrice = order.Price;
            if (deltaQuantity > 0 && position.Quantity > 0 && !_positions.ContainsKey(order.Symbol))
            {
                position.HoldingDays = 0;
                position.PeakPrice = order.Price;
            }
            if (deltaQuantity > 0)
            {
                _riskCooldownUntilBySymbol.Remove(order.Symbol);
            }
            var quantityAfter = position.Quantity;

            if (position.Quantity == 0)
            {
                _positions.Remove(order.Symbol);
            }
            else
            {
                _positions[order.Symbol] = position;
            }

            _tradeRows.Add(new TradeRow
            {
                TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                ExecutedAt = ResolveTradeTimestamp(order.Symbol).ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture),
                Symbol = ToTsCode(order.Symbol),
                Action = isSell ? "SELL" : "BUY",
                Quantity = absoluteQuantity,
                QuantityBefore = quantityBefore,
                QuantityAfter = quantityAfter,
                Price = order.Price,
                TradeValue = tradeValue,
                Fee = fee,
                Score = order.Score,
                Reason = order.Reason
            });

            Log(
                $"[synthetic order] trade_date={sessionDate:yyyyMMdd} " +
                $"executed_at={ResolveTradeTimestamp(order.Symbol):yyyy-MM-dd HH:mm:ss} " +
                $"symbol={ToTsCode(order.Symbol)} action={(isSell ? "SELL" : "BUY")} " +
                $"quantity={absoluteQuantity} before={quantityBefore} after={quantityAfter} " +
                $"price={order.Price:F6} trade_value={tradeValue:F6} fee={fee:F6} " +
                $"score={order.Score:F6} reason={order.Reason ?? string.Empty}");
        }

        /// <summary>
        /// Releases T+1 restricted quantities once the session date has advanced past the buy date.
        /// Called before any sell or position update to keep T1RestrictedQuantity accurate.
        /// </summary>
        private void ReleaseT1Restricted(SyntheticPosition position, DateTime sessionDate)
        {
            if (position.T1RestrictedQuantity > 0 && position.BuyDate < sessionDate)
            {
                position.T1RestrictedQuantity = 0;
            }
        }

        private int ClampBuyQuantityToCash(Symbol symbol, int quantity, decimal price)
        {
            var desired = Math.Abs(quantity);
            while (desired > 0)
            {
                var tradeValue = desired * price;
                var fee = EstimateTradeFee(symbol, tradeValue, false);
                if (tradeValue + fee <= _syntheticCash)
                {
                    return desired;
                }
                desired -= 100;
            }

            return 0;
        }

        private void UpsertAllocations(DateTime sessionDate, IReadOnlyDictionary<Symbol, decimal> prices)
        {
            var tradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            _allocationRows.RemoveAll(row => string.Equals(row.TradeDate, tradeDate, StringComparison.Ordinal));

            var equity = ComputeEquity(prices);
            if (equity <= 0m)
            {
                return;
            }

            foreach (var pair in _positions.OrderBy(pair => pair.Key.Value, StringComparer.Ordinal))
            {
                if (!prices.TryGetValue(pair.Key, out var marketPrice) || marketPrice <= 0m)
                {
                    continue;
                }

                var marketValue = pair.Value.Quantity * marketPrice;
                var weight = marketValue / equity;
                var score = _latestScoresByUnderlying.GetValueOrDefault(pair.Key);
                _allocationRows.Add(new AllocationRow
                {
                    TradeDate = tradeDate,
                    Symbol = ToTsCode(pair.Key),
                    Weight = weight,
                    Quantity = pair.Value.Quantity,
                    Price = pair.Value.AveragePrice > 0m ? pair.Value.AveragePrice : marketPrice,
                    MarketPrice = marketPrice,
                    MarketValue = marketValue,
                    Score = score
                });
            }
        }

        private void UpsertDailySummary(DailySummaryRow row)
        {
            var existingIndex = _dailyRows.FindIndex(existing => string.Equals(existing.TradeDate, row.TradeDate, StringComparison.Ordinal));
            if (existingIndex < 0)
            {
                _dailyRows.Add(row);
                return;
            }

            var existing = _dailyRows[existingIndex];
            row.Turnover = Math.Max(existing.Turnover, row.Turnover);
            row.Rebalanced = existing.Rebalanced || row.Rebalanced;
            row.ScoreSpread = Math.Max(existing.ScoreSpread, row.ScoreSpread);
            row.KellyScale = row.KellyScale > 0m ? row.KellyScale : existing.KellyScale;
            row.EffectiveExposure = row.EffectiveExposure > 0m ? row.EffectiveExposure : existing.EffectiveExposure;
            row.RegimeAdjustment = row.RegimeAdjustment > 0m ? row.RegimeAdjustment : existing.RegimeAdjustment;
            row.StopLossExits = Math.Max(existing.StopLossExits, row.StopLossExits);
            row.TrailingStopExits = Math.Max(existing.TrailingStopExits, row.TrailingStopExits);
            _dailyRows[existingIndex] = row;
        }

        private void UpsertFamilyExposure(FamilyExposureRow row)
        {
            _familyExposureRows.RemoveAll(existing => string.Equals(existing.TradeDate, row.TradeDate, StringComparison.Ordinal));
            _familyExposureRows.Add(row);
        }

        private IReadOnlyDictionary<Symbol, decimal> BuildPriceMap()
        {
            var result = new Dictionary<Symbol, decimal>();
            var snapshotPrices = LiveMode ? LoadLiveSnapshotPrices() : null;
            foreach (var symbol in _factorToUnderlying.Values)
            {
                if (snapshotPrices != null && snapshotPrices.TryGetValue(symbol, out var snapshotPrice) && snapshotPrice > 0m)
                {
                    result[symbol] = snapshotPrice;
                    continue;
                }

                if (Securities.TryGetValue(symbol, out var security) && security.Price > 0m)
                {
                    result[symbol] = security.Price;
                }
            }

            foreach (var position in _positions.Values)
            {
                if (!result.ContainsKey(position.Symbol) && position.LastPrice > 0m)
                {
                    result[position.Symbol] = position.LastPrice;
                }
            }

            return result;
        }

        private IReadOnlyDictionary<Symbol, decimal> LoadLiveSnapshotPrices()
        {
            if (!LiveMode || string.IsNullOrWhiteSpace(_livePriceSnapshotPath) || !File.Exists(_livePriceSnapshotPath))
            {
                return _liveSnapshotPricesBySymbol;
            }

            var writeTimeUtc = File.GetLastWriteTimeUtc(_livePriceSnapshotPath);
            if (_liveSnapshotLastWriteTimeUtc == writeTimeUtc && _liveSnapshotPricesBySymbol.Count > 0)
            {
                return _liveSnapshotPricesBySymbol;
            }

            try
            {
                var payloadText = File.ReadAllText(_livePriceSnapshotPath);
                var payload = JsonConvert.DeserializeObject<LivePriceSnapshotPayload>(payloadText) ?? new LivePriceSnapshotPayload();
                var prices = new Dictionary<Symbol, decimal>();
                foreach (var quote in payload.Quotes ?? new List<LivePriceSnapshotQuote>())
                {
                    if (!TryParseTsCode(quote.TsCode, out var symbol))
                    {
                        continue;
                    }

                    var resolvedPrice = quote.Close ?? quote.Price ?? 0m;
                    if (resolvedPrice > 0m)
                    {
                        prices[symbol] = resolvedPrice;
                    }
                }

                _liveSnapshotPricesBySymbol = prices;
                _liveSnapshotLastWriteTimeUtc = writeTimeUtc;
            }
            catch (Exception ex)
            {
                Log($"[snapshot sync] failed to read {_livePriceSnapshotPath}: {ex.Message}");
            }

            return _liveSnapshotPricesBySymbol;
        }

        private decimal ComputeEquity(IReadOnlyDictionary<Symbol, decimal> prices)
        {
            return _syntheticCash + ComputeInvestedValue(prices);
        }

        private decimal ComputeInvestedValue(IReadOnlyDictionary<Symbol, decimal> prices)
        {
            var total = 0m;
            foreach (var pair in _positions)
            {
                if (prices.TryGetValue(pair.Key, out var price) && price > 0m)
                {
                    total += pair.Value.Quantity * price;
                }
            }
            return total;
        }

        private Dictionary<Symbol, decimal> BuildCurrentWeightMap(IReadOnlyDictionary<Symbol, decimal> prices, decimal equity)
        {
            var weights = new Dictionary<Symbol, decimal>();
            if (equity <= 0m)
            {
                return weights;
            }

            foreach (var pair in _positions)
            {
                if (!prices.TryGetValue(pair.Key, out var price) || price <= 0m)
                {
                    continue;
                }

                weights[pair.Key] = pair.Value.Quantity * price / equity;
            }

            return weights;
        }

        private bool ShouldEmitRuntimeStatusLog(bool rebalanced, bool firstUpdateOfSession)
        {
            if (!LiveMode || rebalanced || firstUpdateOfSession)
            {
                return true;
            }

            if (_lastPortfolioLogUtc == default)
            {
                return true;
            }

            return UtcTime - _lastPortfolioLogUtc >= TimeSpan.FromMinutes(1);
        }

        private bool ShouldPersistRuntimeOutputs(bool rebalanced, bool firstUpdateOfSession)
        {
            if (!LiveMode)
            {
                return false;
            }

            if (rebalanced || firstUpdateOfSession || _lastRuntimePersistUtc == default)
            {
                return true;
            }

            return UtcTime - _lastRuntimePersistUtc >= TimeSpan.FromMinutes(1);
        }

        private void LogSignalRanking(DateTime sessionDate, int eligibleCount, IReadOnlyDictionary<Symbol, decimal> scores)
        {
            var rankingPreview = scores == null
                ? "none"
                : string.Join(", ",
                    scores
                        .OrderByDescending(pair => pair.Value)
                        .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                        .Take(5)
                        .Select(pair => $"{ToTsCode(pair.Key)}={pair.Value:F4}"));

            if (string.IsNullOrWhiteSpace(rankingPreview))
            {
                rankingPreview = "none";
            }

            Log($"[signal ranking] trade_date={sessionDate:yyyyMMdd} eligible={eligibleCount} preview={rankingPreview}");
        }

        private void LogTargetPreview(DateTime sessionDate, IReadOnlyList<AShareMultiFamilyTarget> targets)
        {
            var preview = targets == null
                ? "none"
                : string.Join(", ",
                    targets
                        .OrderByDescending(target => target.Score)
                        .ThenBy(target => target.Symbol.Value, StringComparer.Ordinal)
                        .Take(5)
                        .Select(target => $"{ToTsCode(target.Symbol)} weight={target.Weight:P2} score={target.Score:F4}"));

            if (string.IsNullOrWhiteSpace(preview))
            {
                preview = "none";
            }

            Log($"[signal targets] trade_date={sessionDate:yyyyMMdd} selected={targets?.Count ?? 0} preview={preview}");
        }

        private void LogDailySnapshot(
            DateTime sessionDate,
            decimal equity,
            decimal invested,
            int eligibleCount,
            int selectedCount,
            decimal scoreSpread,
            decimal turnover,
            bool rebalanced,
            RiskExitSummary riskExitSummary,
            decimal kellyScale,
            decimal effectiveExposure,
            decimal regimeAdjustment)
        {
            Log(
                $"[daily] trade_date={sessionDate:yyyyMMdd} equity={equity:F2} cash={_syntheticCash:F2} invested={invested:F2} " +
                $"holdings={_positions.Count} eligible={eligibleCount} selected={selectedCount} " +
                $"turnover={turnover:P2} scoreSpread={scoreSpread:F4} kelly={kellyScale:F2} " +
                $"targetExposure={effectiveExposure:F2} regime={regimeAdjustment:F2} " +
                $"stopLossExits={riskExitSummary?.StopLossExits ?? 0} " +
                $"trailingExits={riskExitSummary?.TrailingStopExits ?? 0} rebalanced={(rebalanced ? 1 : 0)}");
        }

        private void LogPortfolioSnapshot(
            DateTime sessionDate,
            IReadOnlyDictionary<Symbol, decimal> prices,
            IReadOnlyDictionary<Symbol, decimal> weightMap,
            decimal equity)
        {
            var holdingsPreview = weightMap == null || weightMap.Count == 0
                ? "none"
                : string.Join(", ",
                    weightMap
                        .OrderByDescending(pair => Math.Abs(pair.Value))
                        .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                        .Take(5)
                        .Select(pair =>
                        {
                            var price = prices != null && prices.TryGetValue(pair.Key, out var resolvedPrice)
                                ? resolvedPrice
                                : 0m;
                            return $"{ToTsCode(pair.Key)}={pair.Value:P2}@{price:F2}";
                        }));

            Log(
                $"[portfolio] trade_date={sessionDate:yyyyMMdd} equity={equity:F2} cash={_syntheticCash:F2} " +
                $"holdings={_positions.Count} top={holdingsPreview}");
        }

        private IEnumerable<Symbol> DiscoverUniverse()
        {
            var explicitSymbols = ParseUniverse(GetParameter("symbols")).ToList();
            if (explicitSymbols.Count > 0)
            {
                foreach (var tsCode in explicitSymbols)
                {
                    if (TryParseTsCode(tsCode, out var symbol))
                    {
                        yield return symbol;
                    }
                }
                yield break;
            }

            foreach (var marketDirectory in new[] { "sse", "szse" })
            {
                var directory = Path.Combine(_factorDataPath, marketDirectory, "daily");
                if (!Directory.Exists(directory))
                {
                    continue;
                }

                var market = marketDirectory == "sse" ? Market.SSE : Market.SZSE;
                foreach (var file in Directory.EnumerateFiles(directory, "*.csv", SearchOption.TopDirectoryOnly).OrderBy(path => path, StringComparer.Ordinal))
                {
                    var ticker = Path.GetFileNameWithoutExtension(file);
                    if (string.IsNullOrWhiteSpace(ticker))
                    {
                        continue;
                    }

                    yield return global::QuantConnect.Symbol.Create(ticker, SecurityType.Equity, market);
                }
            }
        }

        private bool HasRequiredLocalPriceData(Symbol symbol)
        {
            var dailyZipPath = AShareStockData.GetLeanDataPath(Globals.DataFolder, symbol, StartDate, Resolution.Daily, TickType.Trade);
            var factorPath = AShareTushareFactorData.ResolveSourcePath(symbol, _factorDataPath);
            return File.Exists(dailyZipPath) && File.Exists(factorPath);
        }

        private static int CalculateTargetQuantity(decimal targetValue, decimal price)
        {
            if (price <= 0m || targetValue <= 0m)
            {
                return 0;
            }

            var lots = Math.Floor(targetValue / price / 100m);
            return (int)lots * 100;
        }

        private static decimal EstimateTradeFee(Symbol symbol, decimal tradeValue, bool isSell)
        {
            var commission = Math.Max(tradeValue * CommissionRate, MinimumCommission);
            var stampDuty = isSell ? tradeValue * StampDutyRate : 0m;
            var transferFee = tradeValue * TransferFeeRate;
            return commission + stampDuty + transferFee;
        }

        private static decimal GetScoreSpread(IReadOnlyDictionary<Symbol, decimal> scores)
        {
            if (scores == null || scores.Count == 0)
            {
                return 0m;
            }

            var ordered = scores.Values.OrderBy(value => value).ToList();
            var median = ordered.Count % 2 == 0
                ? (ordered[ordered.Count / 2 - 1] + ordered[ordered.Count / 2]) / 2m
                : ordered[ordered.Count / 2];
            return ordered[^1] - median;
        }

        private DateTime ResolveTradeTimestamp(Symbol symbol)
        {
            if (Securities.TryGetValue(symbol, out var security) && security.LocalTime != default)
            {
                return security.LocalTime;
            }

            if (_anchorSymbol != null && Securities.TryGetValue(_anchorSymbol, out var anchorSecurity) && anchorSecurity.LocalTime != default)
            {
                return anchorSecurity.LocalTime;
            }

            if (Time != default)
            {
                return Time;
            }

            return DateTime.UtcNow;
        }

        private void SyncLeanPortfolioState(IReadOnlyDictionary<Symbol, decimal> prices)
        {
            foreach (var symbol in _factorToUnderlying.Values.Distinct())
            {
                if (!Securities.TryGetValue(symbol, out var security))
                {
                    continue;
                }

                var quantity = _positions.TryGetValue(symbol, out var position) ? position.Quantity : 0;
                var averagePrice = 0m;
                if (position != null && position.AveragePrice > 0m)
                {
                    averagePrice = position.AveragePrice;
                }
                else if (position != null && position.LastPrice > 0m)
                {
                    averagePrice = position.LastPrice;
                }
                else if (prices.TryGetValue(symbol, out var price) && price > 0m)
                {
                    averagePrice = price;
                }
                else if (security.Price > 0m)
                {
                    averagePrice = security.Price;
                }

                if (prices.TryGetValue(symbol, out var marketPrice) && marketPrice > 0m)
                {
                    var localTime = security.LocalTime != default ? security.LocalTime : Time;
                    security.SetMarketPrice(new TradeBar
                    {
                        Symbol = symbol,
                        Time = localTime,
                        EndTime = localTime,
                        Open = marketPrice,
                        High = marketPrice,
                        Low = marketPrice,
                        Close = marketPrice,
                        Value = marketPrice,
                        Volume = 0,
                        Period = TimeSpan.Zero,
                        DataType = MarketDataType.TradeBar
                    });
                }

                security.Holdings.SetHoldings(averagePrice, quantity);
            }

            Portfolio.CashBook[AccountCurrency].SetAmount(_syntheticCash);
            if (Portfolio.UnsettledCashBook.TryGetValue(AccountCurrency, out var unsettledCash))
            {
                unsettledCash.SetAmount(0m);
            }
        }

        private static bool TryParseTsCode(string tsCode, out Symbol symbol)
        {
            symbol = global::QuantConnect.Symbol.Empty;
            if (string.IsNullOrWhiteSpace(tsCode))
            {
                return false;
            }

            var parts = tsCode.Trim().Split('.');
            if (parts.Length != 2)
            {
                return false;
            }

            var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
            symbol = global::QuantConnect.Symbol.Create(parts[0], SecurityType.Equity, market);
            return true;
        }

        private static IEnumerable<string> ParseUniverse(string parameter)
        {
            if (string.IsNullOrWhiteSpace(parameter))
            {
                return Enumerable.Empty<string>();
            }

            return parameter
                .Split(new[] { ',', '\n', '\r', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(value => value.Trim())
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase);
        }

        private static decimal? ParseNullableDecimal(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return null;
            }

            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }

        private static int? ParseNullableInt(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return null;
            }

            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }

        private static bool TryParseTradeDate(string value, out DateTime tradeDate)
        {
            tradeDate = default;
            if (string.IsNullOrWhiteSpace(value))
            {
                return false;
            }

            return DateTime.TryParseExact(value.Trim(), "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out tradeDate);
        }

        private string ResolveOptionalFactorDataPath(string parameterValue)
        {
            if (string.IsNullOrWhiteSpace(parameterValue))
            {
                return null;
            }

            return ResolveFactorDataPath(parameterValue);
        }

        private string ResolveFactorDataPath(string parameterValue)
        {
            if (string.IsNullOrWhiteSpace(parameterValue))
            {
                return Path.Combine(Globals.DataFolder, "alternative", "ashare-multi-family-features");
            }

            return Path.IsPathRooted(parameterValue)
                ? parameterValue
                : Path.GetFullPath(Path.Combine(Globals.DataFolder, parameterValue));
        }

        private string ResolveOutputPath(string value, string defaultFileName)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                return Path.IsPathRooted(value)
                    ? value
                    : Path.GetFullPath(Path.Combine(Globals.ResultsDestinationFolder, value));
            }

            return Path.Combine(Globals.ResultsDestinationFolder, defaultFileName);
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var value = GetParameter(name);
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            var value = GetParameter(name);
            return int.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private bool GetBoolParameter(string name, bool defaultValue)
        {
            var value = GetParameter(name);
            return bool.TryParse(value, out var parsed)
                ? parsed
                : defaultValue;
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            var value = GetParameter(name);
            return DateTime.TryParseExact(value, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed)
                ? parsed
                : defaultValue;
        }

        private void ResetSyntheticState()
        {
            _positions.Clear();
            _riskCooldownUntilBySymbol.Clear();
            _tradeRows.Clear();
            _dailyRows.Clear();
            _allocationRows.Clear();
            _familyExposureRows.Clear();
            _latestScoresByUnderlying.Clear();
            _latestTargetWeightsByUnderlying.Clear();
            _latestFamilyScoresByUnderlying.Clear();
            _syntheticCash = _initialSyntheticCash;
            _previousEquity = _initialSyntheticCash;
            _syntheticRebalanceCount = 0;
            _latestKellyScale = _kellyFallbackScale;
            _latestEffectiveTargetExposure = _targetPortfolioExposure * _latestKellyScale;
            _latestRegimeAdjustment = 1.0m;
            _latestStopLossExitCount = 0;
            _latestTrailingStopExitCount = 0;
            _lastProcessedDate = default;
            _lastSignalEvaluationDate = default;
            _lastRebalanceDate = default;
            _lastLiveProcessingTime = default;
        }

        private void RestoreSyntheticPortfolioState()
        {
            if (!LiveMode || string.IsNullOrWhiteSpace(_portfolioStatePath) || !File.Exists(_portfolioStatePath))
            {
                return;
            }

            SyntheticPortfolioState state;
            try
            {
                state = JsonConvert.DeserializeObject<SyntheticPortfolioState>(File.ReadAllText(_portfolioStatePath));
            }
            catch (Exception ex)
            {
                Log($"[state] failed to restore {_portfolioStatePath}: {ex.Message}");
                ResetSyntheticState();
                return;
            }

            if (state == null)
            {
                return;
            }

            if (Math.Abs(state.InitialCash - _initialSyntheticCash) > 0.01m)
            {
                Log(
                    $"[state] ignored {_portfolioStatePath}: initial_cash mismatch " +
                    $"state={state.InitialCash:F2} current={_initialSyntheticCash:F2}");
                ResetSyntheticState();
                return;
            }

            ResetSyntheticState();
            var allowedSymbols = new HashSet<Symbol>(_factorToUnderlying.Values);
            _syntheticCash = Math.Max(0m, state.Cash);
            _previousEquity = state.PreviousEquity > 0m ? state.PreviousEquity : _syntheticCash;
            _syntheticRebalanceCount = Math.Max(0, state.SyntheticRebalanceCount);

            if (TryParseTradeDate(state.LastProcessedDate, out var lastProcessedDate))
            {
                _lastProcessedDate = lastProcessedDate;
            }
            if (TryParseTradeDate(state.LastSignalEvaluationDate, out var lastSignalEvaluationDate))
            {
                _lastSignalEvaluationDate = lastSignalEvaluationDate;
            }
            if (TryParseTradeDate(state.LastRebalanceDate, out var lastRebalanceDate))
            {
                _lastRebalanceDate = lastRebalanceDate;
            }

            foreach (var entry in state.Positions ?? new List<SyntheticStatePosition>())
            {
                if (!TryParseTsCode(entry.Symbol, out var symbol) || !allowedSymbols.Contains(symbol) || entry.Quantity <= 0)
                {
                    continue;
                }

                _positions[symbol] = new SyntheticPosition
                {
                    Symbol = symbol,
                    Quantity = Math.Max(0, entry.Quantity),
                    AveragePrice = entry.AveragePrice > 0m ? entry.AveragePrice : Math.Max(0m, entry.LastPrice),
                    LastPrice = Math.Max(0m, entry.LastPrice),
                    PeakPrice = Math.Max(0m, entry.PeakPrice),
                    HoldingDays = Math.Max(0, entry.HoldingDays)
                };
            }

            foreach (var entry in state.LatestScores ?? new List<SyntheticStateDecimalValue>())
            {
                if (TryParseTsCode(entry.Symbol, out var symbol) && allowedSymbols.Contains(symbol))
                {
                    _latestScoresByUnderlying[symbol] = entry.Value;
                }
            }

            foreach (var entry in state.LatestTargetWeights ?? new List<SyntheticStateDecimalValue>())
            {
                if (TryParseTsCode(entry.Symbol, out var symbol) && allowedSymbols.Contains(symbol))
                {
                    _latestTargetWeightsByUnderlying[symbol] = Math.Max(0m, entry.Value);
                }
            }

            foreach (var entry in state.RiskCooldowns ?? new List<SyntheticStateDateValue>())
            {
                if (TryParseTsCode(entry.Symbol, out var symbol) &&
                    allowedSymbols.Contains(symbol) &&
                    TryParseTradeDate(entry.Date, out var cooldownUntil))
                {
                    _riskCooldownUntilBySymbol[symbol] = cooldownUntil;
                }
            }

            _tradeRows.AddRange(state.Trades ?? new List<TradeRow>());
            _dailyRows.AddRange(state.DailyRows ?? new List<DailySummaryRow>());
            _allocationRows.AddRange(state.AllocationRows ?? new List<AllocationRow>());
            _familyExposureRows.AddRange(state.FamilyExposureRows ?? new List<FamilyExposureRow>());
            var latestDaily = _dailyRows
                .OrderBy(row => row.TradeDate, StringComparer.Ordinal)
                .LastOrDefault();
            if (latestDaily != null)
            {
                _latestKellyScale = latestDaily.KellyScale > 0m ? latestDaily.KellyScale : _kellyFallbackScale;
                _latestEffectiveTargetExposure = latestDaily.EffectiveExposure > 0m
                    ? latestDaily.EffectiveExposure
                    : _targetPortfolioExposure * _latestKellyScale;
                _latestRegimeAdjustment = latestDaily.RegimeAdjustment > 0m ? latestDaily.RegimeAdjustment : 1.0m;
            }

            Log(
                $"[state] restored path={_portfolioStatePath} cash={_syntheticCash:F2} " +
                $"holdings={_positions.Count} trades={_tradeRows.Count} " +
                $"last_rebalance={(_lastRebalanceDate == default ? "-" : _lastRebalanceDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture))}");
        }

        private void WritePortfolioState()
        {
            if (string.IsNullOrWhiteSpace(_portfolioStatePath))
            {
                return;
            }

            var payload = new SyntheticPortfolioState
            {
                SchemaVersion = 2,
                SavedAtUtc = (UtcTime == default ? DateTime.UtcNow : UtcTime),
                InitialCash = _initialSyntheticCash,
                Cash = _syntheticCash,
                PreviousEquity = _previousEquity,
                SyntheticRebalanceCount = _syntheticRebalanceCount,
                LastProcessedDate = _lastProcessedDate == default ? null : _lastProcessedDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                LastSignalEvaluationDate = _lastSignalEvaluationDate == default ? null : _lastSignalEvaluationDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                LastRebalanceDate = _lastRebalanceDate == default ? null : _lastRebalanceDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Positions = _positions
                    .OrderBy(pair => pair.Key.Value, StringComparer.Ordinal)
                    .Select(pair => new SyntheticStatePosition
                    {
                        Symbol = ToTsCode(pair.Key),
                        Quantity = Math.Max(0, pair.Value.Quantity),
                        AveragePrice = Math.Max(0m, pair.Value.AveragePrice),
                        LastPrice = Math.Max(0m, pair.Value.LastPrice),
                        PeakPrice = Math.Max(0m, pair.Value.PeakPrice),
                        HoldingDays = Math.Max(0, pair.Value.HoldingDays)
                    })
                    .ToList(),
                LatestScores = _latestScoresByUnderlying
                    .OrderBy(pair => pair.Key.Value, StringComparer.Ordinal)
                    .Select(pair => new SyntheticStateDecimalValue { Symbol = ToTsCode(pair.Key), Value = pair.Value })
                    .ToList(),
                LatestTargetWeights = _latestTargetWeightsByUnderlying
                    .OrderBy(pair => pair.Key.Value, StringComparer.Ordinal)
                    .Select(pair => new SyntheticStateDecimalValue { Symbol = ToTsCode(pair.Key), Value = pair.Value })
                    .ToList(),
                RiskCooldowns = _riskCooldownUntilBySymbol
                    .OrderBy(pair => pair.Key.Value, StringComparer.Ordinal)
                    .Select(pair => new SyntheticStateDateValue
                    {
                        Symbol = ToTsCode(pair.Key),
                        Date = pair.Value.ToString("yyyyMMdd", CultureInfo.InvariantCulture)
                    })
                    .ToList(),
                Trades = _tradeRows.ToList(),
                DailyRows = _dailyRows.ToList(),
                AllocationRows = _allocationRows.ToList(),
                FamilyExposureRows = _familyExposureRows.ToList()
            };

            try
            {
                WriteFile(_portfolioStatePath, JsonConvert.SerializeObject(payload, Formatting.Indented));
            }
            catch (Exception ex)
            {
                Log($"[state] failed to persist {_portfolioStatePath}: {ex.Message}");
            }
        }

        private void PersistOutputs()
        {
            WriteTradeReport();
            WriteDailySummary();
            WriteAllocationReport();
            WriteFamilyExposureReport();
            WritePortfolioState();
        }

        private void PublishSyntheticSummaryStatistics()
        {
            var finalEquity = ComputeEquity(BuildPriceMap());
            var netProfit = _initialSyntheticCash > 0m ? finalEquity / _initialSyntheticCash - 1m : 0m;
            var totalFees = _tradeRows.Sum(row => Math.Max(0m, row.Fee));
            var syntheticTradeStatistics = ComputeSyntheticTradeStatistics();

            SetSummaryStatistic("Execution Mode", _syncLeanPortfolio ? "Synthetic + Lean stats sync" : "Synthetic only");
            SetSummaryStatistic("Total Orders", syntheticTradeStatistics.TotalOrders);
            SetSummaryStatistic("Average Win", FormatSummaryPercent(syntheticTradeStatistics.AverageWinRate, 2));
            SetSummaryStatistic("Average Loss", FormatSummaryPercent(syntheticTradeStatistics.AverageLossRate, 2));
            SetSummaryStatistic("Expectancy", Math.Round(syntheticTradeStatistics.Expectancy, 3).ToString(CultureInfo.InvariantCulture));
            SetSummaryStatistic("Loss Rate", FormatSummaryPercent(syntheticTradeStatistics.LossRate, 0));
            SetSummaryStatistic("Win Rate", FormatSummaryPercent(syntheticTradeStatistics.WinRate, 0));
            SetSummaryStatistic("Profit-Loss Ratio", Math.Round(syntheticTradeStatistics.ProfitLossRatio, 2).ToString(CultureInfo.InvariantCulture));
            SetSummaryStatistic("Portfolio Turnover", FormatSummaryPercent(syntheticTradeStatistics.PortfolioTurnover, 2));
            SetSummaryStatistic("Synthetic Trades", _tradeRows.Count);
            SetSummaryStatistic("Synthetic Closed Trades", syntheticTradeStatistics.ClosedTrades);
            SetSummaryStatistic("Synthetic Rebalances", _syntheticRebalanceCount);
            SetSummaryStatistic("Total Fees", $"¥{totalFees:N2}");
            SetSummaryStatistic("Synthetic End Equity", finalEquity.ToString("F2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Synthetic Net Profit", netProfit.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Min Present Families", _minPresentFamilies.ToString(CultureInfo.InvariantCulture));
            SetSummaryStatistic("Regime Adjustment", _latestRegimeAdjustment.ToString("F2", CultureInfo.InvariantCulture));

            PublishMonteCarloSummaryStatistics();
        }

        private SyntheticTradeStatistics ComputeSyntheticTradeStatistics()
        {
            var statistics = new SyntheticTradeStatistics
            {
                TotalOrders = _tradeRows.Count,
                PortfolioTurnover = _dailyRows.Count == 0 ? 0m : _dailyRows.Average(row => Math.Max(0m, row.Turnover))
            };

            if (_tradeRows.Count == 0)
            {
                return statistics;
            }

            var openLotsBySymbol = new Dictionary<string, Queue<SyntheticOpenLot>>(StringComparer.Ordinal);
            var runningCapital = _initialSyntheticCash > 0m ? _initialSyntheticCash : 1m;

            foreach (var row in _tradeRows)
            {
                if (row == null || row.Quantity <= 0 || row.Price <= 0m || string.IsNullOrWhiteSpace(row.Symbol))
                {
                    continue;
                }

                if (string.Equals(row.Action, "BUY", StringComparison.OrdinalIgnoreCase))
                {
                    if (!openLotsBySymbol.TryGetValue(row.Symbol, out var openLots))
                    {
                        openLots = new Queue<SyntheticOpenLot>();
                        openLotsBySymbol[row.Symbol] = openLots;
                    }

                    openLots.Enqueue(new SyntheticOpenLot
                    {
                        Quantity = row.Quantity,
                        Price = row.Price,
                        FeePerShare = row.Fee / row.Quantity
                    });
                    continue;
                }

                if (!string.Equals(row.Action, "SELL", StringComparison.OrdinalIgnoreCase) ||
                    !openLotsBySymbol.TryGetValue(row.Symbol, out var queuedLots) ||
                    queuedLots.Count == 0)
                {
                    continue;
                }

                var sellQuantityRemaining = row.Quantity;
                var sellFeePerShare = row.Fee / row.Quantity;
                while (sellQuantityRemaining > 0 && queuedLots.Count > 0)
                {
                    var lot = queuedLots.Peek();
                    var matchedQuantity = Math.Min(sellQuantityRemaining, lot.Quantity);
                    var matchedQuantityDecimal = matchedQuantity;
                    var profitLoss =
                        (row.Price - lot.Price) * matchedQuantityDecimal -
                        (lot.FeePerShare + sellFeePerShare) * matchedQuantityDecimal;

                    statistics.ClosedTrades += 1;
                    var capitalBase = Math.Max(runningCapital, 1m);
                    var returnRate = profitLoss / capitalBase;
                    if (profitLoss > 0m)
                    {
                        statistics.WinningTrades += 1;
                        statistics.TotalWinningReturn += returnRate;
                    }
                    else
                    {
                        statistics.LosingTrades += 1;
                        statistics.TotalLosingReturn += returnRate;
                    }

                    runningCapital += profitLoss;
                    lot.Quantity -= matchedQuantity;
                    sellQuantityRemaining -= matchedQuantity;
                    if (lot.Quantity <= 0)
                    {
                        queuedLots.Dequeue();
                    }
                }
            }

            if (statistics.WinningTrades > 0)
            {
                statistics.AverageWinRate = statistics.TotalWinningReturn / statistics.WinningTrades;
            }

            if (statistics.LosingTrades > 0)
            {
                statistics.AverageLossRate = statistics.TotalLosingReturn / statistics.LosingTrades;
            }

            if (statistics.AverageLossRate != 0m)
            {
                statistics.ProfitLossRatio = statistics.AverageWinRate / Math.Abs(statistics.AverageLossRate);
            }

            if (statistics.ClosedTrades > 0)
            {
                statistics.WinRate = statistics.WinningTrades / (decimal)statistics.ClosedTrades;
                statistics.LossRate = statistics.LosingTrades / (decimal)statistics.ClosedTrades;
            }

            statistics.Expectancy = statistics.WinRate * statistics.ProfitLossRatio - statistics.LossRate;
            return statistics;
        }

        private static string FormatSummaryPercent(decimal value, int decimals)
        {
            var roundedPercent = Math.Round(value * 100m, decimals);
            return roundedPercent.ToString($"F{decimals}", CultureInfo.InvariantCulture) + "%";
        }

        private void PublishMonteCarloSummaryStatistics()
        {
            if (!_monteCarloEnabled)
            {
                return;
            }

            var dailyReturns = _dailyRows
                .Select(row => new StrategyMonteCarloDailyReturn
                {
                    TradeDate = ParseTradeDate(row.TradeDate),
                    NetReturn = (double)row.NetReturn
                })
                .Where(row => row.TradeDate != default)
                .ToList();
            if (dailyReturns.Count == 0)
            {
                Log("Monte Carlo summary skipped: daily summary rows are empty.");
                return;
            }

            // Map family exposures to factor exposures for Monte Carlo
            var factorExposures = _familyExposureRows
                .Select(row => new StrategyMonteCarloFactorExposure
                {
                    TradeDate = ParseTradeDate(row.TradeDate),
                    Beta = (double)row.MomentumReversal,
                    Momentum = (double)row.ValueQuality,
                    Size = (double)row.MoneyFlow,
                    EarningsYield = (double)row.EarningsSurprise,
                    ResidualVolatility = (double)row.ChipCost,
                    Growth = (double)row.SectorRotation,
                    BookToPrice = (double)row.MarginSignal,
                    Leverage = (double)row.NorthboundFlow,
                    Liquidity = (double)row.MultiFactor,
                    NonLinearSize = (double)row.AnalystSignal
                })
                .Where(row => row.TradeDate != default)
                .ToList();

            var summary = StrategyMonteCarloStatistics.Compute(
                new StrategyMonteCarloConfig
                {
                    Enabled = true,
                    Trials = _monteCarloTrials,
                    HorizonDays = _monteCarloHorizonDays,
                    BlockSize = _monteCarloBlockSize,
                    Seed = _monteCarloSeed,
                    FactorPerturbationScale = (double)_monteCarloFactorPerturbationScale
                },
                dailyReturns,
                factorExposures);
            if (!summary.HasData)
            {
                Log("Monte Carlo summary skipped: insufficient inputs to generate simulation paths.");
                return;
            }

            foreach (var statistic in summary.ToSummaryStatistics())
            {
                SetSummaryStatistic(statistic.Key, statistic.Value);
            }
        }

        private static DateTime ParseTradeDate(string value)
        {
            return DateTime.TryParseExact(
                value ?? string.Empty,
                "yyyyMMdd",
                CultureInfo.InvariantCulture,
                DateTimeStyles.None,
                out var parsed)
                ? parsed
                : default;
        }

        private void WriteTradeReport()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,executed_at,symbol,action,quantity,quantity_before,quantity_after,price,trade_value,fee,score,reason");
            foreach (var row in _tradeRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.ExecutedAt,
                    row.Symbol,
                    row.Action,
                    row.Quantity.ToString(CultureInfo.InvariantCulture),
                    row.QuantityBefore.ToString(CultureInfo.InvariantCulture),
                    row.QuantityAfter.ToString(CultureInfo.InvariantCulture),
                    row.Price.ToString("F6", CultureInfo.InvariantCulture),
                    row.TradeValue.ToString("F6", CultureInfo.InvariantCulture),
                    row.Fee.ToString("F6", CultureInfo.InvariantCulture),
                    row.Score.ToString("F6", CultureInfo.InvariantCulture),
                    row.Reason ?? string.Empty));
            }
            WriteFile(_tradeReportPath, builder.ToString());
        }

        private void WriteDailySummary()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,equity,cash,invested,gross_return,net_return,holdings,eligible_symbols,selected_symbols,turnover,score_spread,rebalanced,kelly_scale,effective_exposure,regime_adjustment,stop_loss_exits,trailing_stop_exits,basis_composite,pcr_composite,vix_composite,dynamic_stop_loss,dynamic_trailing_stop");
            foreach (var row in _dailyRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.Equity.ToString("F6", CultureInfo.InvariantCulture),
                    row.Cash.ToString("F6", CultureInfo.InvariantCulture),
                    row.Invested.ToString("F6", CultureInfo.InvariantCulture),
                    row.GrossReturn.ToString("F8", CultureInfo.InvariantCulture),
                    row.NetReturn.ToString("F8", CultureInfo.InvariantCulture),
                    row.Holdings.ToString(CultureInfo.InvariantCulture),
                    row.EligibleSymbols.ToString(CultureInfo.InvariantCulture),
                    row.SelectedSymbols.ToString(CultureInfo.InvariantCulture),
                    row.Turnover.ToString("F8", CultureInfo.InvariantCulture),
                    row.ScoreSpread.ToString("F8", CultureInfo.InvariantCulture),
                    row.Rebalanced ? "1" : "0",
                    row.KellyScale.ToString("F6", CultureInfo.InvariantCulture),
                    row.EffectiveExposure.ToString("F6", CultureInfo.InvariantCulture),
                    row.RegimeAdjustment.ToString("F6", CultureInfo.InvariantCulture),
                    row.StopLossExits.ToString(CultureInfo.InvariantCulture),
                    row.TrailingStopExits.ToString(CultureInfo.InvariantCulture),
                    row.BasisComposite.HasValue ? row.BasisComposite.Value.ToString("F8", CultureInfo.InvariantCulture) : "",
                    row.PcrComposite.HasValue ? row.PcrComposite.Value.ToString("F8", CultureInfo.InvariantCulture) : "",
                    row.VixComposite.HasValue ? row.VixComposite.Value.ToString("F8", CultureInfo.InvariantCulture) : "",
                    row.DynamicStopLoss.ToString("F6", CultureInfo.InvariantCulture),
                    row.DynamicTrailingStop.ToString("F6", CultureInfo.InvariantCulture)));
            }
            WriteFile(_dailySummaryPath, builder.ToString());
        }

        private void WriteAllocationReport()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,symbol,weight,quantity,price,market_price,market_value,score");
            foreach (var row in _allocationRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.Symbol,
                    row.Weight.ToString("F8", CultureInfo.InvariantCulture),
                    row.Quantity.ToString(CultureInfo.InvariantCulture),
                    row.Price.ToString("F6", CultureInfo.InvariantCulture),
                    row.MarketPrice.ToString("F6", CultureInfo.InvariantCulture),
                    row.MarketValue.ToString("F6", CultureInfo.InvariantCulture),
                    row.Score.ToString("F6", CultureInfo.InvariantCulture)));
            }
            WriteFile(_allocationReportPath, builder.ToString());
        }

        private void WriteFamilyExposureReport()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,holdings,momentum_reversal,value_quality,money_flow,earnings_surprise,chip_cost,etf_premium,sector_rotation,margin_signal,northbound_flow,multi_factor,analyst_signal,macro_rate,barra_momentum,barra_value,barra_quality,low_volatility,size_tilt,liquidity_premium,chip_concentration,rate_sensitivity,basis_sentiment,options_pcr,margin_short_ratio,barra_beta,barra_nlsize,barra_resvol,barra_liquidity");
            foreach (var row in _familyExposureRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.Holdings.ToString(CultureInfo.InvariantCulture),
                    row.MomentumReversal.ToString("F8", CultureInfo.InvariantCulture),
                    row.ValueQuality.ToString("F8", CultureInfo.InvariantCulture),
                    row.MoneyFlow.ToString("F8", CultureInfo.InvariantCulture),
                    row.EarningsSurprise.ToString("F8", CultureInfo.InvariantCulture),
                    row.ChipCost.ToString("F8", CultureInfo.InvariantCulture),
                    row.EtfPremium.ToString("F8", CultureInfo.InvariantCulture),
                    row.SectorRotation.ToString("F8", CultureInfo.InvariantCulture),
                    row.MarginSignal.ToString("F8", CultureInfo.InvariantCulture),
                    row.NorthboundFlow.ToString("F8", CultureInfo.InvariantCulture),
                    row.MultiFactor.ToString("F8", CultureInfo.InvariantCulture),
                    row.AnalystSignal.ToString("F8", CultureInfo.InvariantCulture),
                    row.MacroRate.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraMomentum.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraValue.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraQuality.ToString("F8", CultureInfo.InvariantCulture),
                    row.LowVolatility.ToString("F8", CultureInfo.InvariantCulture),
                    row.SizeTilt.ToString("F8", CultureInfo.InvariantCulture),
                    row.LiquidityPremium.ToString("F8", CultureInfo.InvariantCulture),
                    row.ChipConcentration.ToString("F8", CultureInfo.InvariantCulture),
                    row.RateSensitivity.ToString("F8", CultureInfo.InvariantCulture),
                    row.BasisSentiment.ToString("F8", CultureInfo.InvariantCulture),
                    row.OptionsPcr.ToString("F8", CultureInfo.InvariantCulture),
                    row.MarginShortRatio.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraBeta.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraNlsize.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraResvol.ToString("F8", CultureInfo.InvariantCulture),
                    row.BarraLiquidity.ToString("F8", CultureInfo.InvariantCulture)));
            }
            WriteFile(_familyExposureReportPath, builder.ToString());
        }

        private static void WriteFile(string path, string contents)
        {
            var directory = Path.GetDirectoryName(path);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }
            File.WriteAllText(path, contents, Encoding.UTF8);
        }

        private static string ToTsCode(Symbol symbol)
        {
            var suffix = symbol.ID.Market == Market.SSE ? "SH" : "SZ";
            return $"{symbol.Value}.{suffix}";
        }

        // Inner data classes

        private sealed class SyntheticPosition
        {
            public Symbol Symbol { get; set; }
            public int Quantity { get; set; }
            public decimal AveragePrice { get; set; }
            public decimal LastPrice { get; set; }
            public decimal PeakPrice { get; set; }
            public int HoldingDays { get; set; }
            /// <summary>T+1: quantities bought on this date cannot be sold until the next trading day.</summary>
            public DateTime BuyDate { get; set; }
            /// <summary>T+1: quantity purchased on BuyDate that is not yet sellable today.</summary>
            public int T1RestrictedQuantity { get; set; }
            /// <summary>Gets the quantity available to sell (T+1 rule: same-day buys are restricted).</summary>
            public int AvailableQuantity => Quantity - T1RestrictedQuantity;
        }

        private sealed class SyntheticOrderChange
        {
            public Symbol Symbol { get; set; }
            public decimal Price { get; set; }
            public int CurrentQuantity { get; set; }
            public int TargetQuantity { get; set; }
            public int DeltaQuantity { get; set; }
            public decimal Score { get; set; }
            public string Reason { get; set; }
        }

        private sealed class TradeRow
        {
            public string TradeDate { get; set; }
            public string ExecutedAt { get; set; }
            public string Symbol { get; set; }
            public string Action { get; set; }
            public int Quantity { get; set; }
            public int QuantityBefore { get; set; }
            public int QuantityAfter { get; set; }
            public decimal Price { get; set; }
            public decimal TradeValue { get; set; }
            public decimal Fee { get; set; }
            public decimal Score { get; set; }
            public string Reason { get; set; }
        }

        private sealed class DailySummaryRow
        {
            public string TradeDate { get; set; }
            public decimal Equity { get; set; }
            public decimal Cash { get; set; }
            public decimal Invested { get; set; }
            public decimal GrossReturn { get; set; }
            public decimal NetReturn { get; set; }
            public int Holdings { get; set; }
            public int EligibleSymbols { get; set; }
            public int SelectedSymbols { get; set; }
            public decimal Turnover { get; set; }
            public decimal ScoreSpread { get; set; }
            public bool Rebalanced { get; set; }
            public decimal KellyScale { get; set; }
            public decimal EffectiveExposure { get; set; }
            public decimal RegimeAdjustment { get; set; }
            public int StopLossExits { get; set; }
            public int TrailingStopExits { get; set; }
            public decimal? BasisComposite { get; set; }
            public decimal? PcrComposite { get; set; }
            public decimal? VixComposite { get; set; }
            public decimal DynamicStopLoss { get; set; }
            public decimal DynamicTrailingStop { get; set; }
        }

        private sealed class AllocationRow
        {
            public string TradeDate { get; set; }
            public string Symbol { get; set; }
            public decimal Weight { get; set; }
            public int Quantity { get; set; }
            public decimal Price { get; set; }
            public decimal MarketPrice { get; set; }
            public decimal MarketValue { get; set; }
            public decimal Score { get; set; }
        }

        private sealed class FamilyExposureRow
        {
            public string TradeDate { get; set; }
            public int Holdings { get; set; }
            public decimal MomentumReversal { get; set; }
            public decimal ValueQuality { get; set; }
            public decimal MoneyFlow { get; set; }
            public decimal EarningsSurprise { get; set; }
            public decimal ChipCost { get; set; }
            public decimal EtfPremium { get; set; }
            public decimal SectorRotation { get; set; }
            public decimal MarginSignal { get; set; }
            public decimal NorthboundFlow { get; set; }
            public decimal MultiFactor { get; set; }
            public decimal AnalystSignal { get; set; }
            public decimal MacroRate { get; set; }
            public decimal BarraMomentum { get; set; }
            public decimal BarraValue { get; set; }
            public decimal BarraQuality { get; set; }
            public decimal LowVolatility { get; set; }
            public decimal SizeTilt { get; set; }
            public decimal LiquidityPremium { get; set; }
            public decimal ChipConcentration { get; set; }
            public decimal RateSensitivity { get; set; }
            public decimal BasisSentiment { get; set; }
            public decimal OptionsPcr { get; set; }
            public decimal MarginShortRatio { get; set; }
            public decimal BarraBeta { get; set; }
            public decimal BarraNlsize { get; set; }
            public decimal BarraResvol { get; set; }
            public decimal BarraLiquidity { get; set; }
        }

        private sealed class SyntheticOpenLot
        {
            public int Quantity { get; set; }
            public decimal Price { get; set; }
            public decimal FeePerShare { get; set; }
        }

        private sealed class SyntheticTradeStatistics
        {
            public int TotalOrders { get; set; }
            public int ClosedTrades { get; set; }
            public int WinningTrades { get; set; }
            public int LosingTrades { get; set; }
            public decimal TotalWinningReturn { get; set; }
            public decimal TotalLosingReturn { get; set; }
            public decimal AverageWinRate { get; set; }
            public decimal AverageLossRate { get; set; }
            public decimal ProfitLossRatio { get; set; }
            public decimal WinRate { get; set; }
            public decimal LossRate { get; set; }
            public decimal Expectancy { get; set; }
            public decimal PortfolioTurnover { get; set; }
        }

        private sealed class KellySizingState
        {
            public int ClosedTrades { get; set; }
            public decimal WinRate { get; set; }
            public decimal PayoffRatio { get; set; }
            public decimal RawKellyFraction { get; set; }
            public decimal ExposureScale { get; set; }
        }

        private sealed class RiskExitSummary
        {
            public int StopLossExits { get; set; }
            public int TrailingStopExits { get; set; }
            public int TotalExits { get; set; }
            public decimal Turnover { get; set; }
        }

        private sealed class SyntheticPortfolioState
        {
            [JsonProperty("schema_version")]
            public int SchemaVersion { get; set; }

            [JsonProperty("saved_at_utc")]
            public DateTime SavedAtUtc { get; set; }

            [JsonProperty("initial_cash")]
            public decimal InitialCash { get; set; }

            [JsonProperty("cash")]
            public decimal Cash { get; set; }

            [JsonProperty("previous_equity")]
            public decimal PreviousEquity { get; set; }

            [JsonProperty("synthetic_rebalance_count")]
            public int SyntheticRebalanceCount { get; set; }

            [JsonProperty("last_processed_date")]
            public string LastProcessedDate { get; set; }

            [JsonProperty("last_signal_evaluation_date")]
            public string LastSignalEvaluationDate { get; set; }

            [JsonProperty("last_rebalance_date")]
            public string LastRebalanceDate { get; set; }

            [JsonProperty("positions")]
            public List<SyntheticStatePosition> Positions { get; set; } = new();

            [JsonProperty("latest_scores")]
            public List<SyntheticStateDecimalValue> LatestScores { get; set; } = new();

            [JsonProperty("latest_target_weights")]
            public List<SyntheticStateDecimalValue> LatestTargetWeights { get; set; } = new();

            [JsonProperty("risk_cooldowns")]
            public List<SyntheticStateDateValue> RiskCooldowns { get; set; } = new();

            [JsonProperty("trades")]
            public List<TradeRow> Trades { get; set; } = new();

            [JsonProperty("daily_rows")]
            public List<DailySummaryRow> DailyRows { get; set; } = new();

            [JsonProperty("allocation_rows")]
            public List<AllocationRow> AllocationRows { get; set; } = new();

            [JsonProperty("family_exposure_rows")]
            public List<FamilyExposureRow> FamilyExposureRows { get; set; } = new();
        }

        private sealed class SyntheticStatePosition
        {
            [JsonProperty("symbol")]
            public string Symbol { get; set; }

            [JsonProperty("quantity")]
            public int Quantity { get; set; }

            [JsonProperty("average_price")]
            public decimal AveragePrice { get; set; }

            [JsonProperty("last_price")]
            public decimal LastPrice { get; set; }

            [JsonProperty("peak_price")]
            public decimal PeakPrice { get; set; }

            [JsonProperty("holding_days")]
            public int HoldingDays { get; set; }
        }

        private sealed class SyntheticStateDecimalValue
        {
            [JsonProperty("symbol")]
            public string Symbol { get; set; }

            [JsonProperty("value")]
            public decimal Value { get; set; }
        }

        private sealed class SyntheticStateDateValue
        {
            [JsonProperty("symbol")]
            public string Symbol { get; set; }

            [JsonProperty("date")]
            public string Date { get; set; }
        }

        private sealed class LivePriceSnapshotPayload
        {
            [JsonProperty("trade_date")]
            public string TradeDate { get; set; }

            [JsonProperty("quotes")]
            public List<LivePriceSnapshotQuote> Quotes { get; set; } = new();
        }

        private sealed class LivePriceSnapshotQuote
        {
            [JsonProperty("trade_date")]
            public string TradeDate { get; set; }

            [JsonProperty("ts_code")]
            public string TsCode { get; set; }

            [JsonProperty("close")]
            public decimal? Close { get; set; }

            [JsonProperty("price")]
            public decimal? Price { get; set; }
        }
    }
}
