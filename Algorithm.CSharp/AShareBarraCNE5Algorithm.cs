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
    /// Synthetic Barra CNE5 stock strategy that consumes precomputed factor CSV files
    /// and local LEAN A-share daily bars without touching the existing ETF T+0 workflows.
    /// </summary>
    public class AShareBarraCNE5Algorithm : QCAlgorithm
    {
        private const decimal CommissionRate = 0.0003m;
        private const decimal MinimumCommission = 5m;
        private const decimal StampDutyRate = 0.001m;
        private const decimal ShanghaiTransferFeeRate = 0.00002m;

        private readonly Dictionary<Symbol, Symbol> _factorToUnderlying = new();
        private readonly Dictionary<Symbol, AShareBarraCNE5FactorData> _latestFactorsByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestScoresByUnderlying = new();
        private readonly Dictionary<Symbol, decimal> _latestTargetWeightsByUnderlying = new();
        private readonly Dictionary<Symbol, SyntheticPosition> _positions = new();
        private readonly List<TradeRow> _tradeRows = new();
        private readonly List<DailySummaryRow> _dailyRows = new();
        private readonly List<AllocationRow> _allocationRows = new();
        private readonly List<FactorExposureRow> _factorExposureRows = new();

        private Symbol _anchorSymbol;
        private string _factorDataPath;
        private string _fallbackFactorDataPath;
        private string _livePriceSnapshotPath;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _allocationReportPath;
        private string _factorExposureReportPath;
        private string _portfolioStatePath;
        private string _rebalanceFrequency;
        private decimal _initialSyntheticCash;
        private decimal _syntheticCash;
        private decimal _previousEquity;
        private decimal _targetPortfolioExposure;
        private decimal _minScoreSpread;
        private decimal _minTurnoverRate;
        private decimal? _minTotalMv;
        private int _topN;
        private int _minListedDays;
        private int _maxMissingFactorCount;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private int _syntheticRebalanceCount;
        private DateTime _lastProcessedDate;
        private DateTime _lastSignalEvaluationDate;
        private DateTime _lastRebalanceDate;
        private DateTime _lastPortfolioLogUtc;
        private DateTime _lastRuntimePersistUtc;
        private DateTime _lastLiveFactorDiskRefreshDate;
        private DateTime _liveSnapshotLastWriteTimeUtc;
        private bool _syncLeanPortfolio;
        private bool _monteCarloEnabled;
        private decimal _latestScoreSpread;
        private decimal _monteCarloFactorPerturbationScale;
        private AShareBarraCNE5SignalSettings _signalSettings;
        private Dictionary<Symbol, decimal> _liveSnapshotPricesBySymbol = new();

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2020, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            var initialCash = GetDecimalParameter("initial-cash", 1000000m);

            _factorDataPath = ResolveFactorDataPath(GetParameter("factor-data-path"));
            _fallbackFactorDataPath = ResolveOptionalFactorDataPath(GetParameter("external-factor-path"));
            _livePriceSnapshotPath = ResolveOutputPath(GetParameter("live-price-snapshot-file"), "barra-cne5-live-price-snapshot.json");
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "barra-cne5-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "barra-cne5-daily-summary.csv");
            _allocationReportPath = ResolveOutputPath(GetParameter("allocation-report-file"), "barra-cne5-allocation.csv");
            _factorExposureReportPath = ResolveOutputPath(GetParameter("factor-exposure-file"), "barra-cne5-factor-exposure.csv");
            _portfolioStatePath = ResolveOutputPath(GetParameter("portfolio-state-file"), "barra-cne5-live-state.json");
            _rebalanceFrequency = (GetParameter("rebalance-frequency") ?? "monthly").Trim().ToLowerInvariant();
            _topN = GetIntParameter("top-n", 30);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.5m);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.95m);
            _minListedDays = GetIntParameter("min-listed-days", 250);
            _maxMissingFactorCount = GetIntParameter("max-missing-factor-count", 3);
            _minTurnoverRate = GetDecimalParameter("min-turnover-rate", 0m);
            _minTotalMv = GetOptionalDecimalParameter("min-total-mv");
            _signalSettings = new AShareBarraCNE5SignalSettings
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
                MinimumPresentFactors = GetIntParameter("minimum-present-factors", 6),
                WeightingMode = (GetParameter("weighting-mode") ?? "equal").Trim()
            };

            SetStartDate(startDate);
            SetEndDate(endDate);
            SetAccountCurrency(Currencies.CNY);
            SetCash(initialCash);
            SetBenchmark(_ => 0m);
            _initialSyntheticCash = initialCash;
            _syntheticCash = initialCash;
            _previousEquity = initialCash;
            _syncLeanPortfolio = GetBoolParameter("sync-lean-portfolio", !LiveMode);
            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", false);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);

            AShareBarraCNE5FactorData.SetBaseDirectory(_factorDataPath);

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

                var factorSecurity = AddData<AShareBarraCNE5FactorData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _factorToUnderlying[factorSecurity.Symbol] = equity.Symbol;
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No Barra CNE5 symbols were configured from factor path {_factorDataPath}");
            }

            Log($"AShareBarraCNE5Algorithm initialized with {_factorToUnderlying.Count} factor subscriptions");
            Log(_syncLeanPortfolio
                ? "Execution mode: synthetic portfolio with Lean portfolio sync for backtest statistics."
                : "Execution mode: synthetic-only portfolio; no Lean orders or Lean portfolio sync.");
            Log(
                $"Synthetic execution enabled: factor path={_factorDataPath} fallback={_fallbackFactorDataPath ?? "-"} rebalance={_rebalanceFrequency} " +
                $"topN={_topN} exposure={_targetPortfolioExposure:F2} minScoreSpread={_minScoreSpread:F2}");
            if (_monteCarloEnabled)
            {
                Log(
                    $"Monte Carlo summary enabled: trials={_monteCarloTrials} horizonDays={_monteCarloHorizonDays} " +
                    $"blockSize={_monteCarloBlockSize} factorScale={_monteCarloFactorPerturbationScale:F2} seed={_monteCarloSeed}");
            }
            if (LiveMode)
            {
                Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.Every(TimeSpan.FromMinutes(1)), RunLiveMonitoringCycle);
                Log("Live monitoring schedule: every 1 minute using rt_k snapshot prices and Barra factor CSV refresh.");
            }
            SetRuntimeStatistic("Exec Mode", _syncLeanPortfolio ? "synthetic+sync" : "synthetic");
            SetRuntimeStatistic("Syn Equity", _syntheticCash.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", "0");
            if (LiveMode)
            {
                BootstrapLiveSessionState();
            }
        }

        public override void OnData(Slice slice)
        {
            var sessionDate = DateTime.MinValue;
            foreach (var pair in slice.Get<AShareBarraCNE5FactorData>())
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

            if (sessionDate == DateTime.MinValue && _anchorSymbol != null && slice.Bars.TryGetValue(_anchorSymbol, out var anchorBar))
            {
                sessionDate = anchorBar.EndTime.Date;
            }

            if (sessionDate != DateTime.MinValue)
            {
                if (LiveMode)
                {
                    EnsureLiveFactorSnapshots(sessionDate.Date);
                }
                ProcessSession(sessionDate.Date);
            }
        }

        public override void OnEndOfAlgorithm()
        {
            PublishSyntheticSummaryStatistics();
            PersistOutputs();
            Log($"Saved Barra outputs: trades={_tradeReportPath}, daily={_dailySummaryPath}, allocations={_allocationReportPath}, exposures={_factorExposureReportPath}");
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

        public static bool IsFreshFactorSnapshot(AShareBarraCNE5FactorData factor, DateTime sessionDate)
        {
            return factor != null && factor.EndTime.Date == sessionDate.Date && factor.PresentFactorCount > 0;
        }

        private void RunLiveMonitoringCycle()
        {
            if (!LiveMode)
            {
                return;
            }

            var sessionDate = ResolveLiveSessionDate();
            EnsureLiveFactorSnapshots(sessionDate);
            ProcessSession(sessionDate);
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
            ProcessSession(bootstrapDate);
        }

        private void ProcessSession(DateTime sessionDate)
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

            var prices = BuildPriceMap();
            var eligibleFactors = _latestFactorsByUnderlying
                .Where(pair => IsFreshFactorSnapshot(pair.Value, sessionDate) && IsEligibleFactor(pair.Value))
                .ToDictionary(pair => pair.Key, pair => pair.Value);

            var rebalance = false;
            var liveAligned = false;
            var scoreSpread = _latestScoreSpread;
            var turnover = 0m;
            if (_lastSignalEvaluationDate != sessionDate)
            {
                _lastSignalEvaluationDate = sessionDate;
                if (ShouldRebalance(sessionDate, _lastRebalanceDate, _rebalanceFrequency))
                {
                    var scores = AShareBarraCNE5SignalModel.ComputeScores(eligibleFactors, _signalSettings);
                    ReplaceLatestScores(scores);
                    _latestScoreSpread = GetScoreSpread(scores);
                    scoreSpread = _latestScoreSpread;
                    LogSignalRanking(sessionDate, eligibleFactors.Count, scores);

                    var targets = AShareBarraCNE5SignalModel.SelectPortfolio(
                        scores,
                        eligibleFactors,
                        _topN,
                        _minScoreSpread,
                        _targetPortfolioExposure,
                        _signalSettings.WeightingMode);

                    if (targets.Count == 0)
                    {
                        _latestTargetWeightsByUnderlying.Clear();
                        Log($"{sessionDate:yyyy-MM-dd} rebalance skipped: eligible={eligibleFactors.Count} scoreSpread={scoreSpread:F4} threshold={_minScoreSpread:F4}");
                    }
                    else
                    {
                        rebalance = true;
                        ReplaceLatestTargets(targets);
                        turnover = ApplyTargetPortfolio(sessionDate, targets, prices);
                        _syntheticRebalanceCount += 1;
                        _lastRebalanceDate = sessionDate;
                        Log($"{sessionDate:yyyy-MM-dd} rebalance -> eligible={eligibleFactors.Count} selected={targets.Count} turnover={turnover:P2} scoreSpread={scoreSpread:F4}");
                        LogTargetPreview(sessionDate, targets);
                    }
                }
            }

            if (LiveMode && !rebalance && _latestTargetWeightsByUnderlying.Count > 0)
            {
                var retainedTargets = BuildTargetsFromLatestWeights();
                if (retainedTargets.Count > 0)
                {
                    var tradeCountBeforeAlignment = _tradeRows.Count;
                    turnover = ApplyTargetPortfolio(sessionDate, retainedTargets, prices);
                    liveAligned = _tradeRows.Count > tradeCountBeforeAlignment;
                    if (liveAligned)
                    {
                        Log(
                            $"{sessionDate:yyyy-MM-dd} live-align -> selected={retainedTargets.Count} " +
                            $"turnover={turnover:P2} scoreSpread={scoreSpread:F4}");
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
            var exposure = AShareBarraCNE5SignalModel.ComputePortfolioExposure(weightMap, _latestFactorsByUnderlying);

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
                Rebalanced = rebalance || liveAligned
            });

            UpsertFactorExposure(new FactorExposureRow
            {
                TradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Holdings = _positions.Count,
                Beta = exposure["beta"],
                Momentum = exposure["momentum"],
                Size = exposure["size"],
                EarningsYield = exposure["earnyld"],
                ResidualVolatility = exposure["resvol"],
                Growth = exposure["growth"],
                BookToPrice = exposure["btop"],
                Leverage = exposure["leverage"],
                Liquidity = exposure["liquidity"],
                NonLinearSize = exposure["nlsize"]
            });

            SetRuntimeStatistic("Syn Equity", equity.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", _positions.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Last Session", sessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Signals", selectedCount.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Score Spr", scoreSpread.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Turnover", turnover.ToString("P1", CultureInfo.InvariantCulture));
            if (ShouldPersistRuntimeOutputs(rebalance || liveAligned, firstUpdateOfSession))
            {
                PersistOutputs();
                _lastRuntimePersistUtc = UtcTime;
            }

            if (ShouldEmitRuntimeStatusLog(rebalance || liveAligned, firstUpdateOfSession))
            {
                LogDailySnapshot(sessionDate, equity, invested, eligibleFactors.Count, selectedCount, scoreSpread, turnover, rebalance || liveAligned);
                LogPortfolioSnapshot(sessionDate, prices, weightMap, equity);
                _lastPortfolioLogUtc = UtcTime;
            }

            _previousEquity = equity;
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
            out AShareBarraCNE5FactorData factor,
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
            out AShareBarraCNE5FactorData factor,
            out DateTime matchedTradeDate)
        {
            factor = null;
            matchedTradeDate = default;
            if (string.IsNullOrWhiteSpace(factorRootPath))
            {
                return false;
            }

            var factorPath = AShareBarraCNE5FactorData.ResolveSourcePath(underlying, factorRootPath);
            if (!File.Exists(factorPath))
            {
                return false;
            }

            var targetTradeDate = sessionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            string matchedLine = null;
            foreach (var line in File.ReadLines(factorPath).Reverse())
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
                {
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

            var csv = matchedLine.Split(',');
            if (csv.Length < 16 || !TryParseTradeDate(csv[0], out matchedTradeDate))
            {
                return false;
            }

            factor = new AShareBarraCNE5FactorData
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
                TotalMv = ParseNullableDecimal(csv[11]),
                TurnoverRate = ParseNullableDecimal(csv[12]),
                ListedDays = ParseNullableInt(csv[13]),
                MissingFactorCount = ParseNullableInt(csv[14]),
                IsSt = ParseNullableInt(csv[15]).GetValueOrDefault() != 0
            };
            return true;
        }

        private bool IsEligibleFactor(AShareBarraCNE5FactorData factor)
        {
            if (factor == null || factor.IsSt)
            {
                return false;
            }
            if (factor.ListedDays.HasValue && factor.ListedDays.Value < _minListedDays)
            {
                return false;
            }
            if (factor.MissingFactorCount.HasValue && factor.MissingFactorCount.Value > _maxMissingFactorCount)
            {
                return false;
            }
            if (factor.TurnoverRate.HasValue && factor.TurnoverRate.Value < _minTurnoverRate)
            {
                return false;
            }
            if (_minTotalMv.HasValue && factor.TotalMv.HasValue && factor.TotalMv.Value < _minTotalMv.Value)
            {
                return false;
            }
            return factor.PresentFactorCount >= _signalSettings.MinimumPresentFactors;
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

        private void ReplaceLatestTargets(IReadOnlyList<AShareBarraCNE5Target> targets)
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

        private List<AShareBarraCNE5Target> BuildTargetsFromLatestWeights()
        {
            return _latestTargetWeightsByUnderlying
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                .Select(pair => new AShareBarraCNE5Target
                {
                    Symbol = pair.Key,
                    Weight = pair.Value,
                    Score = _latestScoresByUnderlying.TryGetValue(pair.Key, out var score) ? score : 0m
                })
                .ToList();
        }

        private decimal ApplyTargetPortfolio(DateTime sessionDate, IReadOnlyList<AShareBarraCNE5Target> targets, IReadOnlyDictionary<Symbol, decimal> prices)
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
                    Score = targets.FirstOrDefault(target => target.Symbol == symbol)?.Score ?? 0m
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
            else if (order.CurrentQuantity <= 0)
            {
                deltaQuantity = 0;
            }
            else if (Math.Abs(deltaQuantity) > order.CurrentQuantity)
            {
                deltaQuantity = -order.CurrentQuantity;
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
                    LastPrice = order.Price,
                    HoldingDays = 0
                };
            }

            var quantityBefore = position.Quantity;
            position.Quantity = Math.Max(0, position.Quantity + deltaQuantity);
            position.LastPrice = order.Price;
            if (deltaQuantity > 0 && position.Quantity > 0 && !_positions.ContainsKey(order.Symbol))
            {
                position.HoldingDays = 0;
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
                Score = order.Score
            });
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
                if (!prices.TryGetValue(pair.Key, out var price) || price <= 0m)
                {
                    continue;
                }

                var weight = pair.Value.Quantity * price / equity;
                var score = _latestScoresByUnderlying.GetValueOrDefault(pair.Key);
                _allocationRows.Add(new AllocationRow
                {
                    TradeDate = tradeDate,
                    Symbol = ToTsCode(pair.Key),
                    Weight = weight,
                    Quantity = pair.Value.Quantity,
                    Price = price,
                    Score = score
                });
            }
        }

        private void UpsertDailySummary(DailySummaryRow row)
        {
            _dailyRows.RemoveAll(existing => string.Equals(existing.TradeDate, row.TradeDate, StringComparison.Ordinal));
            _dailyRows.Add(row);
        }

        private void UpsertFactorExposure(FactorExposureRow row)
        {
            _factorExposureRows.RemoveAll(existing => string.Equals(existing.TradeDate, row.TradeDate, StringComparison.Ordinal));
            _factorExposureRows.Add(row);
        }

        private IReadOnlyDictionary<Symbol, decimal> BuildPriceMap()
        {
            var result = new Dictionary<Symbol, decimal>();
            var snapshotPrices = LiveMode ? LoadLiveSnapshotPrices() : null;
            foreach (var symbol in _factorToUnderlying.Values)
            {
                if (Securities.TryGetValue(symbol, out var security) && security.Price > 0m)
                {
                    result[symbol] = security.Price;
                    continue;
                }

                if (snapshotPrices != null && snapshotPrices.TryGetValue(symbol, out var snapshotPrice) && snapshotPrice > 0m)
                {
                    result[symbol] = snapshotPrice;
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

        private void LogTargetPreview(DateTime sessionDate, IReadOnlyList<AShareBarraCNE5Target> targets)
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
            bool rebalanced)
        {
            Log(
                $"[daily] trade_date={sessionDate:yyyyMMdd} equity={equity:F2} cash={_syntheticCash:F2} invested={invested:F2} " +
                $"holdings={_positions.Count} eligible={eligibleCount} selected={selectedCount} " +
                $"turnover={turnover:P2} scoreSpread={scoreSpread:F4} rebalanced={(rebalanced ? 1 : 0)}");
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
            var factorPath = AShareBarraCNE5FactorData.ResolveSourcePath(symbol, _factorDataPath);
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
            var transferFee = symbol.ID.Market == Market.SSE ? tradeValue * ShanghaiTransferFeeRate : 0m;
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
                if (position != null && position.LastPrice > 0m)
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
                return Path.Combine(Globals.DataFolder, "alternative", "barra-cne5-factors");
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

        private decimal? GetOptionalDecimalParameter(string name)
        {
            var value = GetParameter(name);
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : (decimal?)null;
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
            _tradeRows.Clear();
            _dailyRows.Clear();
            _allocationRows.Clear();
            _factorExposureRows.Clear();
            _latestScoresByUnderlying.Clear();
            _latestTargetWeightsByUnderlying.Clear();
            _syntheticCash = _initialSyntheticCash;
            _previousEquity = _initialSyntheticCash;
            _syntheticRebalanceCount = 0;
            _lastProcessedDate = default;
            _lastSignalEvaluationDate = default;
            _lastRebalanceDate = default;
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
                    LastPrice = Math.Max(0m, entry.LastPrice),
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

            _tradeRows.AddRange(state.Trades ?? new List<TradeRow>());
            _dailyRows.AddRange(state.DailyRows ?? new List<DailySummaryRow>());
            _allocationRows.AddRange(state.AllocationRows ?? new List<AllocationRow>());
            _factorExposureRows.AddRange(state.FactorExposureRows ?? new List<FactorExposureRow>());

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
                SchemaVersion = 1,
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
                        LastPrice = Math.Max(0m, pair.Value.LastPrice),
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
                Trades = _tradeRows.ToList(),
                DailyRows = _dailyRows.ToList(),
                AllocationRows = _allocationRows.ToList(),
                FactorExposureRows = _factorExposureRows.ToList()
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
            WriteFactorExposureReport();
            WritePortfolioState();
        }

        private void PublishSyntheticSummaryStatistics()
        {
            var finalEquity = ComputeEquity(BuildPriceMap());
            var netProfit = _initialSyntheticCash > 0m ? finalEquity / _initialSyntheticCash - 1m : 0m;

            SetSummaryStatistic("Execution Mode", _syncLeanPortfolio ? "Synthetic + Lean stats sync" : "Synthetic only");
            SetSummaryStatistic("Synthetic Trades", _tradeRows.Count);
            SetSummaryStatistic("Synthetic Rebalances", _syntheticRebalanceCount);
            SetSummaryStatistic("Synthetic End Equity", finalEquity.ToString("F2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Synthetic Net Profit", netProfit.ToString("P2", CultureInfo.InvariantCulture));

            PublishMonteCarloSummaryStatistics();
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

            var factorExposures = _factorExposureRows
                .Select(row => new StrategyMonteCarloFactorExposure
                {
                    TradeDate = ParseTradeDate(row.TradeDate),
                    Beta = (double)row.Beta,
                    Momentum = (double)row.Momentum,
                    Size = (double)row.Size,
                    EarningsYield = (double)row.EarningsYield,
                    ResidualVolatility = (double)row.ResidualVolatility,
                    Growth = (double)row.Growth,
                    BookToPrice = (double)row.BookToPrice,
                    Leverage = (double)row.Leverage,
                    Liquidity = (double)row.Liquidity,
                    NonLinearSize = (double)row.NonLinearSize
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
            builder.AppendLine("trade_date,executed_at,symbol,action,quantity,quantity_before,quantity_after,price,trade_value,fee,score");
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
                    row.Score.ToString("F6", CultureInfo.InvariantCulture)));
            }
            WriteFile(_tradeReportPath, builder.ToString());
        }

        private void WriteDailySummary()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,equity,cash,invested,gross_return,net_return,holdings,eligible_symbols,selected_symbols,turnover,score_spread,rebalanced");
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
                    row.Rebalanced ? "1" : "0"));
            }
            WriteFile(_dailySummaryPath, builder.ToString());
        }

        private void WriteAllocationReport()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,symbol,weight,quantity,price,score");
            foreach (var row in _allocationRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.Symbol,
                    row.Weight.ToString("F8", CultureInfo.InvariantCulture),
                    row.Quantity.ToString(CultureInfo.InvariantCulture),
                    row.Price.ToString("F6", CultureInfo.InvariantCulture),
                    row.Score.ToString("F6", CultureInfo.InvariantCulture)));
            }
            WriteFile(_allocationReportPath, builder.ToString());
        }

        private void WriteFactorExposureReport()
        {
            var builder = new StringBuilder();
            builder.AppendLine("trade_date,holdings,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize");
            foreach (var row in _factorExposureRows)
            {
                builder.AppendLine(string.Join(",",
                    row.TradeDate,
                    row.Holdings.ToString(CultureInfo.InvariantCulture),
                    row.Beta.ToString("F8", CultureInfo.InvariantCulture),
                    row.Momentum.ToString("F8", CultureInfo.InvariantCulture),
                    row.Size.ToString("F8", CultureInfo.InvariantCulture),
                    row.EarningsYield.ToString("F8", CultureInfo.InvariantCulture),
                    row.ResidualVolatility.ToString("F8", CultureInfo.InvariantCulture),
                    row.Growth.ToString("F8", CultureInfo.InvariantCulture),
                    row.BookToPrice.ToString("F8", CultureInfo.InvariantCulture),
                    row.Leverage.ToString("F8", CultureInfo.InvariantCulture),
                    row.Liquidity.ToString("F8", CultureInfo.InvariantCulture),
                    row.NonLinearSize.ToString("F8", CultureInfo.InvariantCulture)));
            }
            WriteFile(_factorExposureReportPath, builder.ToString());
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

        private sealed class SyntheticPosition
        {
            public Symbol Symbol { get; set; }
            public int Quantity { get; set; }
            public decimal LastPrice { get; set; }
            public int HoldingDays { get; set; }
        }

        private sealed class SyntheticOrderChange
        {
            public Symbol Symbol { get; set; }
            public decimal Price { get; set; }
            public int CurrentQuantity { get; set; }
            public int TargetQuantity { get; set; }
            public int DeltaQuantity { get; set; }
            public decimal Score { get; set; }
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
        }

        private sealed class AllocationRow
        {
            public string TradeDate { get; set; }
            public string Symbol { get; set; }
            public decimal Weight { get; set; }
            public int Quantity { get; set; }
            public decimal Price { get; set; }
            public decimal Score { get; set; }
        }

        private sealed class FactorExposureRow
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

            [JsonProperty("trades")]
            public List<TradeRow> Trades { get; set; } = new();

            [JsonProperty("daily_rows")]
            public List<DailySummaryRow> DailyRows { get; set; } = new();

            [JsonProperty("allocation_rows")]
            public List<AllocationRow> AllocationRows { get; set; } = new();

            [JsonProperty("factor_exposure_rows")]
            public List<FactorExposureRow> FactorExposureRows { get; set; } = new();
        }

        private sealed class SyntheticStatePosition
        {
            [JsonProperty("symbol")]
            public string Symbol { get; set; }

            [JsonProperty("quantity")]
            public int Quantity { get; set; }

            [JsonProperty("last_price")]
            public decimal LastPrice { get; set; }

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
