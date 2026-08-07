using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Custom;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Standard LEAN A-share multi-factor strategy using exported Tushare feature snapshots.
    /// </summary>
    public class AShareLlmQuantLeanAlgorithm : QCAlgorithm
    {
        private const decimal AShareLotSize = 100m;
        private const int FeatureHistoryDepth = 10;
        private readonly Dictionary<Symbol, Symbol> _featureToUnderlying = new();
        private readonly Dictionary<Symbol, LinkedList<AShareLlmQuantFeatureData>> _featureHistoryByUnderlying = new();
        private readonly Dictionary<Symbol, AShareLlmQuantFeatureData> _latestFeaturesByUnderlying = new();
        private readonly List<TradeRow> _tradeRows = new();
        private readonly List<RebalanceRow> _rebalanceRows = new();
        private readonly List<DailySummaryRow> _dailyRows = new();
        private readonly List<Symbol> _underlyingSymbols = new();
        private readonly Dictionary<DateTime, BenchmarkRow> _benchmarkRowsByDate = new();
        private readonly List<DateTime> _benchmarkDates = new();

        private Symbol _anchorSymbol;
        private string _featureDataPath;
        private string _benchmarkFilePath;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _rebalanceReportPath;
        private string _comparisonSummaryPath;
        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private decimal _initialCapital;
        private decimal _minRegimeExposure;
        private decimal _riskOnExposure;
        private decimal _riskOffExposure;
        private decimal _benchmarkMomentumGate;
        private decimal _targetWeightBuffer;
        private decimal _maxExposureStepUp;
        private decimal _maxExposureStepDown;
        private decimal _recoveryDrawdownEnter;
        private decimal _recoveryDrawdownExit;
        private decimal _recoveryExposureCap;
        private decimal _monteCarloFactorPerturbationScale;
        private decimal _lastTargetExposure;
        private decimal _portfolioPeakEquity;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private DateTime _benchmarkFileLastWriteTimeUtc;
        private DateTime _lastRebalanceDate;
        private DateTime _lastDailySummaryDate;
        private DateTime _lastLiveEvaluationTime;
        private DateTime _lastPersistTime;
        private bool _monteCarloEnabled;
        private bool _recoveryMode;
        private string _rebalanceFrequency;
        private string _lastLiveRebalanceKey;
        private TimeSpan _liveSignalInterval;
        private TimeSpan _liveSnapshotInterval;
        private AShareLlmQuantSignalSettings _signalSettings;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            _initialCapital = GetDecimalParameter("initial-capital", 1000000m);
            SetCash(_initialCapital);

            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2025, 12, 31)));

            _featureDataPath = ResolvePath(GetParameter("feature-data-path"), Path.Combine(Globals.DataFolder, "alternative", "ashare-llm-quant-features"));
            _benchmarkFilePath = ResolvePath(GetParameter("benchmark-file"), Path.Combine(_featureDataPath, "benchmark", "000300.SH.csv"));
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "ashare-llm-quant-lean-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "ashare-llm-quant-lean-daily.csv");
            _rebalanceReportPath = ResolveOutputPath(GetParameter("rebalance-report-file"), "ashare-llm-quant-lean-rebalances.csv");
            _comparisonSummaryPath = ResolveOutputPath(GetParameter("comparison-summary-file"), "ashare-llm-quant-lean-comparison.json");
            _signalFilePath = ResolveOutputPath(GetParameter("signal-file"), "ashare-llm-quant-live-signals.json");
            _portfolioSnapshotPath = ResolveOutputPath(GetParameter("portfolio-snapshot-file"), "ashare-llm-quant-live-portfolio.json");
            _minRegimeExposure = GetDecimalParameter("min-regime-exposure", 0.55m);
            _riskOnExposure = GetDecimalParameter("risk-on-exposure", 1.0m);
            _riskOffExposure = GetDecimalParameter("risk-off-exposure", 0.4m);
            _benchmarkMomentumGate = GetDecimalParameter("benchmark-momentum-gate", -0.02m);
            _targetWeightBuffer = GetDecimalParameter("target-weight-buffer", 0.985m);
            _maxExposureStepUp = GetDecimalParameter("max-exposure-step-up", 0.30m);
            _maxExposureStepDown = GetDecimalParameter("max-exposure-step-down", 0.45m);
            _recoveryDrawdownEnter = GetDecimalParameter("recovery-drawdown-enter", -0.16m);
            _recoveryDrawdownExit = GetDecimalParameter("recovery-drawdown-exit", -0.05m);
            _recoveryExposureCap = GetDecimalParameter("recovery-exposure-cap", 0.90m);
            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", false);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);
            _rebalanceFrequency = NormalizeRebalanceFrequency(GetParameter("rebalance-frequency"));
            _liveSignalInterval = TimeSpan.FromMinutes(Math.Max(1, GetIntParameter("live-signal-interval-minutes", 5)));
            _liveSnapshotInterval = TimeSpan.FromMinutes(Math.Max(1, GetIntParameter("live-snapshot-interval-minutes", GetIntParameter("live-signal-interval-minutes", 5))));

            _signalSettings = new AShareLlmQuantSignalSettings
            {
                TopN = GetIntParameter("top-n", 10),
                RetentionBuffer = GetIntParameter("retention-buffer", 6),
                MinPrice = GetDecimalParameter("min-price", 5m),
                MinCircMv = GetDecimalParameter("min-circ-mv", 1000000m),
                MinTurnoverRateF = GetDecimalParameter("min-turnover-rate-f", 0.3m),
                MaxVolatility20 = GetOptionalDecimalParameter("max-vol20"),
                RequirePositiveFlow = GetBoolParameter("require-positive-flow", true),
                RequirePositiveMomentum = GetBoolParameter("require-positive-momentum", false),
                MomentumWeight = GetDecimalParameter("factor-weight-momentum", 1.2m),
                ValuePbWeight = GetDecimalParameter("factor-weight-value-pb", 0.6m),
                ValuePsWeight = GetDecimalParameter("factor-weight-value-ps", 0.2m),
                DividendWeight = GetDecimalParameter("factor-weight-dividend", 0.2m),
                FlowWeight = GetDecimalParameter("factor-weight-flow", 0.8m),
                BigFlowWeight = GetDecimalParameter("factor-weight-big-flow", 0.6m),
                LowVolWeight = GetDecimalParameter("factor-weight-low-vol", 1.0m),
                ShortReversalWeight = GetDecimalParameter("factor-weight-short-reversal", 0.2m)
            };
            _portfolioPeakEquity = _initialCapital;
            _lastTargetExposure = _riskOffExposure;

            LoadBenchmarkRows();
            SetBenchmark(time => GetBenchmarkClose(time));
            AShareLlmQuantFeatureData.SetBaseDirectory(_featureDataPath);

            foreach (var underlying in DiscoverUniverse())
            {
                if (!HasRequiredLocalPriceData(underlying))
                {
                    continue;
                }

                var equity = AddEquity(underlying.Value, Resolution.Daily, underlying.ID.Market);
                equity.FeeModel = new AShareStockFeeModel();
                equity.FillModel = new AShareStockFillModel();
                equity.BuyingPowerModel = new AShareStockBuyingPowerModel();
                equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                equity.Session.Size = 2;

                var featureSecurity = AddData<AShareLlmQuantFeatureData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _featureToUnderlying[featureSecurity.Symbol] = equity.Symbol;
                _featureHistoryByUnderlying[equity.Symbol] = new LinkedList<AShareLlmQuantFeatureData>();
                _underlyingSymbols.Add(equity.Symbol);
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No A-share feature files were found under {_featureDataPath}");
            }

            if (!LiveMode)
            {
                Schedule.On(DateRules.MonthStart(_anchorSymbol), TimeRules.BeforeMarketClose(_anchorSymbol, 1), RebalancePortfolio);
                Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.AfterMarketClose(_anchorSymbol, 0), CaptureDailySummary);
            }

            SetRuntimeStatistic("Universe", _underlyingSymbols.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Top N", _signalSettings.TopN.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Risk Off", _riskOffExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Retain Buf", _signalSettings.RetentionBuffer.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Rebalance", _rebalanceFrequency.ToUpperInvariant());
            Log($"AShareLlmQuantLeanAlgorithm initialized with {_underlyingSymbols.Count} symbols from {_featureDataPath}");
            if (_monteCarloEnabled)
            {
                Log(
                    $"Monte Carlo summary enabled: trials={_monteCarloTrials} horizonDays={_monteCarloHorizonDays} " +
                    $"blockSize={_monteCarloBlockSize} factorScale={_monteCarloFactorPerturbationScale:F2} seed={_monteCarloSeed}");
            }
        }

        public override void OnData(Slice slice)
        {
            foreach (var pair in slice.Get<AShareLlmQuantFeatureData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                if (_featureToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    var feature = pair.Value.Copy();
                    _latestFeaturesByUnderlying[underlying] = feature;
                    UpdateFeatureHistory(underlying, feature);
                }
            }

            if (!LiveMode || slice == null || slice.Count == 0)
            {
                return;
            }

            CaptureDailySummary();
            TryRunLiveEvaluation();
            if (ShouldPersistLiveSnapshot())
            {
                PersistOutputs();
            }
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent == null)
            {
                return;
            }

            if (orderEvent.Status != OrderStatus.Filled && orderEvent.Status != OrderStatus.PartiallyFilled)
            {
                return;
            }

            var order = Transactions.GetOrderById(orderEvent.OrderId);
            var localTime = orderEvent.UtcTime.ConvertFromUtc(TimeZones.Shanghai);
            _tradeRows.Add(new TradeRow
            {
                Timestamp = localTime.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture),
                TradeDate = localTime.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Symbol = ToTsCode(orderEvent.Symbol),
                Action = orderEvent.Direction == OrderDirection.Buy ? "BUY" : "SELL",
                Quantity = Math.Abs(orderEvent.FillQuantity),
                FillPrice = orderEvent.FillPrice,
                FillValue = Math.Abs(orderEvent.FillQuantity * orderEvent.FillPrice),
                Fee = orderEvent.OrderFee.Value.Amount,
                Status = orderEvent.Status.ToString(),
                Tag = order?.Tag ?? string.Empty
            });
        }

        public override void OnEndOfAlgorithm()
        {
            PublishMonteCarloSummaryStatistics();
            PersistOutputs();
        }

        private void RebalancePortfolio()
        {
            if (_lastRebalanceDate == Time.Date)
            {
                return;
            }

            EnsureBenchmarkRowsFresh();
            var signalDate = GetPreviousBenchmarkDate(Time.Date);
            if (signalDate == default || !_benchmarkRowsByDate.TryGetValue(signalDate, out var benchmark))
            {
                return;
            }

            var featureSnapshot = GetFeatureSnapshot(signalDate);
            if (featureSnapshot.Count == 0)
            {
                Log($"Skipping rebalance on {Time:yyyy-MM-dd}: no feature snapshot available for signal date {signalDate:yyyy-MM-dd}");
                return;
            }

            ExecuteRebalanceForSignalDate(signalDate, benchmark, featureSnapshot);
        }

        private void ExecuteRebalanceForSignalDate(
            DateTime signalDate,
            BenchmarkRow benchmark,
            IReadOnlyDictionary<Symbol, AShareLlmQuantFeatureData> featureSnapshot)
        {
            if (benchmark == null || featureSnapshot == null)
            {
                return;
            }

            var scores = AShareLlmQuantSignalModel.ComputeScores(featureSnapshot, _signalSettings);
            var currentHoldings = Portfolio.Values
                .Where(holding => holding.Invested)
                .Select(holding => holding.Symbol)
                .ToList();
            var marketState = EvaluateMarketState(benchmark, featureSnapshot);
            var targets = AShareLlmQuantSignalModel.SelectPortfolio(scores, _signalSettings, marketState.TargetExposure, currentHoldings);
            var scoreSpread = targets.Count >= 2
                ? targets[0].Score - targets[targets.Count - 1].Score
                : (targets.Count == 1 ? targets[0].Score : 0m);
            var tag =
                $"signal_date={signalDate:yyyyMMdd};risk={marketState.Label};target_exposure={marketState.TargetExposure:F2};" +
                $"regime_score={marketState.RegimeScore:F3};recovery={(_recoveryMode ? 1 : 0)}";
            ExecuteRebalanceOrders(targets, tag);

            _rebalanceRows.Add(new RebalanceRow
            {
                SignalDate = signalDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                ExecutionDate = Time.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                RiskState = marketState.Label,
                TargetExposure = marketState.TargetExposure,
                CandidateCount = scores.Count,
                SelectedCount = targets.Count,
                ScoreSpread = scoreSpread,
                BenchmarkClose = benchmark.Close,
                BenchmarkMa = benchmark.Ma120,
                BenchmarkMomentum = benchmark.Momentum60,
                BenchmarkVolatility20 = benchmark.Volatility20,
                BenchmarkDrawdown252 = benchmark.Drawdown252,
                RegimeScore = marketState.RegimeScore,
                StrategyDrawdown = marketState.StrategyDrawdown,
                MomentumBreadth = marketState.MomentumBreadth,
                FlowBreadth = marketState.FlowBreadth,
                RecoveryMode = _recoveryMode,
                SelectedSymbols = string.Join(";", targets.Select(target => $"{ToTsCode(target.Symbol)}@{target.Weight:F4}|{target.Score:F4}"))
            });

            _lastRebalanceDate = Time.Date;
            _lastTargetExposure = marketState.TargetExposure;
            SetRuntimeStatistic("Last Rebalance", Time.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Risk State", marketState.Label);
            SetRuntimeStatistic("Exposure", marketState.TargetExposure.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Regime Score", marketState.RegimeScore.ToString("F2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Holdings", targets.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Equity", Portfolio.TotalPortfolioValue.ToString("F0", CultureInfo.InvariantCulture));
        }

        private void TryRunLiveEvaluation()
        {
            if (!LiveMode || !EnsureBenchmarkRowsFresh())
            {
                return;
            }

            var signalDate = GetLatestBenchmarkDate();
            if (signalDate == default || !_benchmarkRowsByDate.TryGetValue(signalDate, out var benchmark))
            {
                return;
            }

            if (!ShouldRunLiveEvaluation(signalDate))
            {
                return;
            }

            var featureSnapshot = GetFeatureSnapshot(signalDate);
            if (featureSnapshot.Count == 0)
            {
                Log($"[live] skipping evaluation at {Time:yyyy-MM-dd HH:mm:ss}: no feature snapshot for {signalDate:yyyy-MM-dd}");
                _lastLiveEvaluationTime = Time;
                return;
            }

            ExecuteRebalanceForSignalDate(signalDate, benchmark, featureSnapshot);
            _lastLiveEvaluationTime = Time;
            _lastLiveRebalanceKey = BuildLiveRebalanceKey(signalDate);
            PersistOutputs();
        }

        private void CaptureDailySummary()
        {
            if (_lastDailySummaryDate == Time.Date)
            {
                return;
            }

            _portfolioPeakEquity = Math.Max(_portfolioPeakEquity, Portfolio.TotalPortfolioValue);
            var benchmarkClose = GetBenchmarkClose(Time.Date);
            _dailyRows.Add(new DailySummaryRow
            {
                TradeDate = Time.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Equity = Portfolio.TotalPortfolioValue,
                Cash = Portfolio.CashBook[Currencies.CNY].Amount,
                HoldingsValue = Portfolio.TotalHoldingsValue,
                PositionCount = Portfolio.Values.Count(holding => holding.Invested),
                BenchmarkClose = benchmarkClose
            });
            _lastDailySummaryDate = Time.Date;
        }

        private Dictionary<Symbol, AShareLlmQuantFeatureData> GetFeatureSnapshot(DateTime signalDate)
        {
            if (LiveMode)
            {
                var liveSnapshot = GetFeatureSnapshotFromFiles(signalDate);
                if (liveSnapshot.Count > 0)
                {
                    return liveSnapshot;
                }
            }

            var snapshot = new Dictionary<Symbol, AShareLlmQuantFeatureData>();
            foreach (var pair in _featureHistoryByUnderlying)
            {
                var node = pair.Value.Last;
                while (node != null)
                {
                    var feature = node.Value;
                    if (feature != null && feature.EndTime.Date <= signalDate)
                    {
                        snapshot[pair.Key] = feature;
                        break;
                    }

                    node = node.Previous;
                }
            }

            if (snapshot.Count == 0)
            {
                foreach (var pair in _latestFeaturesByUnderlying)
                {
                    if (pair.Value != null && pair.Value.EndTime.Date <= signalDate)
                    {
                        snapshot[pair.Key] = pair.Value;
                    }
                }
            }

            return snapshot;
        }

        private Dictionary<Symbol, AShareLlmQuantFeatureData> GetFeatureSnapshotFromFiles(DateTime signalDate)
        {
            var snapshot = new Dictionary<Symbol, AShareLlmQuantFeatureData>();
            foreach (var symbol in _underlyingSymbols)
            {
                var path = AShareLlmQuantFeatureData.ResolveSourcePath(symbol, _featureDataPath);
                var feature = TryLoadFeatureSnapshot(symbol, path, signalDate);
                if (feature != null)
                {
                    snapshot[symbol] = feature;
                }
            }

            return snapshot;
        }

        private static AShareLlmQuantFeatureData TryLoadFeatureSnapshot(Symbol underlying, string path, DateTime signalDate)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
            {
                return null;
            }

            string selectedLine = null;
            foreach (var line in File.ReadLines(path))
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var csv = line.Split(',');
                if (csv.Length < 14)
                {
                    continue;
                }

                if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
                {
                    continue;
                }

                if (tradeDate.Date > signalDate.Date)
                {
                    continue;
                }

                selectedLine = line;
            }

            if (selectedLine == null)
            {
                return null;
            }

            var row = selectedLine.Split(',');
            if (!DateTime.TryParseExact(row[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var snapshotDate))
            {
                return null;
            }

            return new AShareLlmQuantFeatureData
            {
                Symbol = underlying,
                Time = snapshotDate.Date,
                EndTime = snapshotDate.Date,
                Value = ParseNullableDecimal(row[1]) ?? 0m,
                Close = ParseNullableDecimal(row[1]),
                PctChg = ParseNullableDecimal(row[2]),
                Pb = ParseNullableDecimal(row[3]),
                PsTtm = ParseNullableDecimal(row[4]),
                DvTtm = ParseNullableDecimal(row[5]),
                TurnoverRateF = ParseNullableDecimal(row[6]),
                CircMv = ParseNullableDecimal(row[7]),
                Momentum12020 = ParseNullableDecimal(row[8]),
                Return5 = ParseNullableDecimal(row[9]),
                Volatility20 = ParseNullableDecimal(row[10]),
                FlowRatio = ParseNullableDecimal(row[11]),
                BigFlowRatio = ParseNullableDecimal(row[12]),
                InUniverse = int.TryParse(row[13], NumberStyles.Any, CultureInfo.InvariantCulture, out var parsedUniverse) && parsedUniverse != 0
            };
        }

        private MarketState EvaluateMarketState(
            BenchmarkRow benchmark,
            IReadOnlyDictionary<Symbol, AShareLlmQuantFeatureData> featureSnapshot)
        {
            _portfolioPeakEquity = Math.Max(_portfolioPeakEquity, Portfolio.TotalPortfolioValue);
            var strategyDrawdown = _portfolioPeakEquity > 0m
                ? Portfolio.TotalPortfolioValue / _portfolioPeakEquity - 1m
                : 0m;

            var broadUniverse = featureSnapshot.Values
                .Where(IsBroadUniverseFeature)
                .ToList();
            var denominator = broadUniverse.Count;
            var momentumBreadth = denominator > 0
                ? broadUniverse.Count(feature => feature.Momentum12020.HasValue && feature.Momentum12020.Value > 0m) / (decimal)denominator
                : 0.50m;
            var flowBreadth = denominator > 0
                ? broadUniverse.Count(feature => feature.FlowRatio.HasValue && feature.FlowRatio.Value > 0m) / (decimal)denominator
                : 0.50m;
            var lowVolGate = _signalSettings.MaxVolatility20 ?? 0.06m;
            var lowVolBreadth = denominator > 0
                ? broadUniverse.Count(feature => feature.Volatility20.HasValue && feature.Volatility20.Value <= lowVolGate) / (decimal)denominator
                : 0.50m;

            var trendScore =
                ScoreTrend(benchmark.Close, benchmark.Ma20, 0.12m) +
                ScoreTrend(benchmark.Close, benchmark.Ma60, 0.18m) +
                ScoreTrend(benchmark.Close, benchmark.Ma120, 0.25m);
            var momentumScore =
                ScoreCentered(benchmark.Momentum20 ?? 0m, 0.00m, 0.08m, 0.12m) +
                ScoreCentered(benchmark.Momentum60 ?? 0m, _benchmarkMomentumGate, 0.12m, 0.18m);
            var breadthScore =
                ScoreCentered(momentumBreadth, 0.52m, 0.18m, 0.15m) +
                ScoreCentered(flowBreadth, 0.50m, 0.15m, 0.10m) +
                ScoreCentered(lowVolBreadth, 0.55m, 0.20m, 0.08m);
            var volatilityScore = ScoreCentered(0.025m - (benchmark.Volatility20 ?? 0.025m), 0m, 0.02m, 0.18m);
            var drawdownScore = ScoreCentered(benchmark.Drawdown252 ?? 0m, -0.08m, 0.12m, 0.22m);
            var regimeScore = trendScore + momentumScore + breadthScore + volatilityScore + drawdownScore;

            var rawTargetExposure = GetRegimeExposure(regimeScore);
            var riskCap = Math.Min(
                GetStrategyDrawdownCap(strategyDrawdown),
                GetBenchmarkStressCap(benchmark));

            if (!_recoveryMode && strategyDrawdown <= _recoveryDrawdownEnter)
            {
                _recoveryMode = true;
            }
            else if (_recoveryMode && strategyDrawdown >= _recoveryDrawdownExit && regimeScore >= 0.20m)
            {
                _recoveryMode = false;
            }

            var cappedExposure = Math.Min(rawTargetExposure, riskCap);
            if (_recoveryMode)
            {
                cappedExposure = Math.Min(cappedExposure, _recoveryExposureCap);
            }

            var previousExposure = _lastTargetExposure > 0m ? _lastTargetExposure : _riskOffExposure;
            var targetExposure = ApplyExposurePathControl(previousExposure, cappedExposure);
            return new MarketState
            {
                Label = BuildRiskStateLabel(regimeScore, targetExposure),
                RegimeScore = regimeScore,
                TargetExposure = targetExposure,
                StrategyDrawdown = strategyDrawdown,
                MomentumBreadth = momentumBreadth,
                FlowBreadth = flowBreadth
            };
        }

        private void UpdateFeatureHistory(Symbol underlying, AShareLlmQuantFeatureData feature)
        {
            if (!_featureHistoryByUnderlying.TryGetValue(underlying, out var history))
            {
                history = new LinkedList<AShareLlmQuantFeatureData>();
                _featureHistoryByUnderlying[underlying] = history;
            }

            if (history.Last?.Value?.EndTime.Date == feature.EndTime.Date)
            {
                history.Last.Value = feature;
            }
            else
            {
                history.AddLast(feature);
                while (history.Count > FeatureHistoryDepth)
                {
                    history.RemoveFirst();
                }
            }
        }

        private void ExecuteRebalanceOrders(IReadOnlyList<AShareLlmQuantTarget> targets, string tag)
        {
            var desiredQuantities = BuildTargetQuantities(targets);
            var currentSymbols = _underlyingSymbols
                .Where(symbol => Portfolio[symbol].Quantity != 0m || desiredQuantities.ContainsKey(symbol))
                .ToList();

            foreach (var symbol in currentSymbols)
            {
                var currentQuantity = Portfolio[symbol].Quantity;
                var desiredQuantity = desiredQuantities.TryGetValue(symbol, out var quantity) ? quantity : 0m;
                var delta = desiredQuantity - currentQuantity;
                if (delta >= 0m)
                {
                    continue;
                }

                var sellQuantity = NormalizeLotQuantity(Math.Abs(delta), AShareLotSize);
                if (sellQuantity > 0m)
                {
                    MarketOrder(symbol, -sellQuantity, false, $"{tag};phase=reduce");
                }
            }

            foreach (var target in targets.OrderByDescending(item => item.Score))
            {
                if (!desiredQuantities.TryGetValue(target.Symbol, out var desiredQuantity))
                {
                    continue;
                }

                var currentQuantity = Portfolio[target.Symbol].Quantity;
                var delta = desiredQuantity - currentQuantity;
                if (delta <= 0m)
                {
                    continue;
                }

                var affordableQuantity = GetAffordableBuyQuantity(target.Symbol, delta);
                if (affordableQuantity > 0m)
                {
                    MarketOrder(target.Symbol, affordableQuantity, false, $"{tag};phase=add");
                }
            }

            foreach (var symbol in _underlyingSymbols.Where(symbol => Portfolio[symbol].Invested))
            {
                if (desiredQuantities.ContainsKey(symbol))
                {
                    continue;
                }

                if (Portfolio[symbol].Quantity > 0m)
                {
                    Liquidate(symbol, $"{tag};phase=clear");
                }
            }
        }

        private Dictionary<Symbol, decimal> BuildTargetQuantities(IReadOnlyList<AShareLlmQuantTarget> targets)
        {
            var desired = new Dictionary<Symbol, decimal>();
            if (targets == null || targets.Count == 0)
            {
                return desired;
            }

            var investableValue = Portfolio.TotalPortfolioValue * _targetWeightBuffer;
            foreach (var target in targets)
            {
                if (!Securities.TryGetValue(target.Symbol, out var security))
                {
                    continue;
                }

                var price = security.Price;
                if (price <= 0m)
                {
                    continue;
                }

                var targetValue = investableValue * target.Weight;
                var rawQuantity = targetValue / price;
                var quantity = NormalizeLotQuantity(rawQuantity, AShareLotSize);
                if (quantity > 0m)
                {
                    desired[target.Symbol] = quantity;
                }
            }

            return desired;
        }

        private decimal GetAffordableBuyQuantity(Symbol symbol, decimal desiredQuantity)
        {
            if (!Securities.TryGetValue(symbol, out var security))
            {
                return 0m;
            }

            var price = security.Price;
            if (price <= 0m)
            {
                return 0m;
            }

            var quantity = NormalizeLotQuantity(desiredQuantity, AShareLotSize);
            while (quantity >= AShareLotSize)
            {
                var estimatedCost = EstimateBuyOrderCost(symbol, quantity, price);
                if (estimatedCost <= Portfolio.CashBook[Currencies.CNY].Amount)
                {
                    return quantity;
                }

                quantity -= AShareLotSize;
            }

            return 0m;
        }

        private decimal GetRegimeExposure(decimal regimeScore)
        {
            var floor = Clamp(_minRegimeExposure, 0m, _riskOffExposure);
            if (regimeScore >= 0.25m)
            {
                var normalized = Clamp((regimeScore - 0.25m) / 0.55m, 0m, 1m);
                return Lerp(_riskOffExposure, _riskOnExposure, normalized);
            }

            var defensiveNormalized = Clamp((regimeScore + 0.65m) / 0.90m, 0m, 1m);
            return Lerp(floor, _riskOffExposure, defensiveNormalized);
        }

        private decimal GetStrategyDrawdownCap(decimal strategyDrawdown)
        {
            if (strategyDrawdown <= -0.18m)
            {
                return 0.45m;
            }

            if (strategyDrawdown <= -0.12m)
            {
                return 0.60m;
            }

            if (strategyDrawdown <= -0.08m)
            {
                return 0.75m;
            }

            return _riskOnExposure;
        }

        private decimal GetBenchmarkStressCap(BenchmarkRow benchmark)
        {
            var drawdown = benchmark.Drawdown252 ?? 0m;
            var volatility = benchmark.Volatility20 ?? 0.025m;
            if (drawdown <= -0.20m || volatility >= 0.045m)
            {
                return 0.45m;
            }

            if (drawdown <= -0.12m || volatility >= 0.035m)
            {
                return 0.65m;
            }

            if (drawdown <= -0.08m || volatility >= 0.028m)
            {
                return 0.80m;
            }

            return _riskOnExposure;
        }

        private decimal ApplyExposurePathControl(decimal previousExposure, decimal targetExposure)
        {
            if (targetExposure >= previousExposure)
            {
                return Math.Min(targetExposure, previousExposure + _maxExposureStepUp);
            }

            return Math.Max(targetExposure, previousExposure - _maxExposureStepDown);
        }

        private decimal EstimateBuyOrderCost(Symbol symbol, decimal quantity, decimal price)
        {
            if (quantity <= 0m || price <= 0m)
            {
                return 0m;
            }

            var orderValue = quantity * price;
            var commission = Math.Max(orderValue * AShareStockFeeModel.DefaultCommissionRate, AShareStockFeeModel.DefaultMinimumCommission);
            var transferFee = orderValue * AShareStockFeeModel.DefaultTransferFeeRate;
            return orderValue + commission + transferFee;
        }

        private static decimal NormalizeLotQuantity(decimal quantity, decimal lotSize)
        {
            if (quantity <= 0m || lotSize <= 0m)
            {
                return 0m;
            }

            return Math.Floor(quantity / lotSize) * lotSize;
        }

        private void LoadBenchmarkRows()
        {
            if (!File.Exists(_benchmarkFilePath))
            {
                throw new FileNotFoundException($"Missing benchmark file: {_benchmarkFilePath}");
            }

            _benchmarkRowsByDate.Clear();
            _benchmarkDates.Clear();
            _benchmarkFileLastWriteTimeUtc = File.GetLastWriteTimeUtc(_benchmarkFilePath);
            var rows = new List<BenchmarkRow>();
            foreach (var line in File.ReadLines(_benchmarkFilePath))
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var csv = line.Split(',');
                if (csv.Length < 5 || !DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
                {
                    continue;
                }

                rows.Add(new BenchmarkRow
                {
                    TradeDate = tradeDate.Date,
                    Close = ParseDecimal(csv[1]),
                    PctChg = ParseNullableDecimal(csv[2]),
                    Ma120 = ParseNullableDecimal(csv[3]),
                    Momentum60 = ParseNullableDecimal(csv[4]),
                });
            }

            var orderedRows = rows.OrderBy(row => row.TradeDate).ToList();
            for (var index = 0; index < orderedRows.Count; index++)
            {
                var row = orderedRows[index];
                row.Ma20 = ComputeRollingAverage(orderedRows, index, 20);
                row.Ma60 = ComputeRollingAverage(orderedRows, index, 60);
                row.Ma120 ??= ComputeRollingAverage(orderedRows, index, 120);
                row.Momentum20 = ComputeMomentum(orderedRows, index, 20);
                row.Momentum60 ??= ComputeMomentum(orderedRows, index, 60);
                row.Volatility20 = ComputeVolatility(orderedRows, index, 20);
                row.Drawdown120 = ComputeRollingDrawdown(orderedRows, index, 120);
                row.Drawdown252 = ComputeRollingDrawdown(orderedRows, index, 252);
                _benchmarkRowsByDate[row.TradeDate] = row;
            }

            _benchmarkDates.AddRange(_benchmarkRowsByDate.Keys.OrderBy(date => date));
            if (_benchmarkDates.Count == 0)
            {
                throw new InvalidOperationException($"Benchmark file {_benchmarkFilePath} does not contain any rows.");
            }
        }

        private IEnumerable<Symbol> DiscoverUniverse()
        {
            foreach (var pair in new[] { ("sse", Market.SSE), ("szse", Market.SZSE) })
            {
                var dailyDirectory = Path.Combine(_featureDataPath, pair.Item1, "daily");
                if (!Directory.Exists(dailyDirectory))
                {
                    continue;
                }

                foreach (var file in Directory.EnumerateFiles(dailyDirectory, "*.csv", SearchOption.TopDirectoryOnly))
                {
                    var ticker = Path.GetFileNameWithoutExtension(file);
                    if (string.IsNullOrWhiteSpace(ticker))
                    {
                        continue;
                    }

                    yield return QuantConnect.Symbol.Create(ticker, SecurityType.Equity, pair.Item2);
                }
            }
        }

        private bool HasRequiredLocalPriceData(Symbol symbol)
        {
            if (LiveMode)
            {
                return true;
            }

            var zipPath = AShareStockData.GetLeanDataPath(Globals.DataFolder, symbol, StartDate, Resolution.Daily, TickType.Trade);
            var csvPath = Path.ChangeExtension(zipPath, ".csv");
            return File.Exists(zipPath) || File.Exists(csvPath);
        }

        private decimal GetBenchmarkClose(DateTime time)
        {
            EnsureBenchmarkRowsFresh();
            if (_benchmarkDates.Count == 0)
            {
                return 0m;
            }

            var date = time.Date;
            var index = _benchmarkDates.BinarySearch(date);
            if (index >= 0)
            {
                return _benchmarkRowsByDate[_benchmarkDates[index]].Close;
            }

            index = ~index - 1;
            if (index < 0)
            {
                return _benchmarkRowsByDate[_benchmarkDates[0]].Close;
            }

            return _benchmarkRowsByDate[_benchmarkDates[index]].Close;
        }

        private DateTime GetPreviousBenchmarkDate(DateTime date)
        {
            EnsureBenchmarkRowsFresh();
            if (_benchmarkDates.Count == 0)
            {
                return default;
            }

            var index = _benchmarkDates.BinarySearch(date.Date);
            if (index >= 0)
            {
                index -= 1;
            }
            else
            {
                index = ~index - 1;
            }

            return index >= 0 ? _benchmarkDates[index] : default;
        }

        private DateTime GetLatestBenchmarkDate()
        {
            EnsureBenchmarkRowsFresh();
            return _benchmarkDates.Count > 0 ? _benchmarkDates[_benchmarkDates.Count - 1] : default;
        }

        private bool EnsureBenchmarkRowsFresh()
        {
            if (!LiveMode)
            {
                return _benchmarkDates.Count > 0;
            }

            if (!File.Exists(_benchmarkFilePath))
            {
                return false;
            }

            var lastWriteTimeUtc = File.GetLastWriteTimeUtc(_benchmarkFilePath);
            if (_benchmarkDates.Count == 0 || _benchmarkFileLastWriteTimeUtc != lastWriteTimeUtc)
            {
                LoadBenchmarkRows();
            }

            return _benchmarkDates.Count > 0;
        }

        private static decimal? ComputeRollingAverage(IReadOnlyList<BenchmarkRow> rows, int endIndex, int window)
        {
            if (rows == null || window <= 0 || endIndex < window - 1)
            {
                return null;
            }

            decimal sum = 0m;
            for (var index = endIndex - window + 1; index <= endIndex; index++)
            {
                sum += rows[index].Close;
            }

            return sum / window;
        }

        private static decimal? ComputeMomentum(IReadOnlyList<BenchmarkRow> rows, int endIndex, int lookback)
        {
            if (rows == null || lookback <= 0 || endIndex < lookback)
            {
                return null;
            }

            var anchor = rows[endIndex - lookback].Close;
            if (anchor <= 0m)
            {
                return null;
            }

            return rows[endIndex].Close / anchor - 1m;
        }

        private static decimal? ComputeVolatility(IReadOnlyList<BenchmarkRow> rows, int endIndex, int window)
        {
            if (rows == null || window <= 1 || endIndex < window - 1)
            {
                return null;
            }

            var values = new List<decimal>(window);
            for (var index = endIndex - window + 1; index <= endIndex; index++)
            {
                var pctChg = rows[index].PctChg;
                if (!pctChg.HasValue)
                {
                    return null;
                }

                values.Add(pctChg.Value / 100m);
            }

            var mean = values.Average();
            var variance = values
                .Select(value => (double)((value - mean) * (value - mean)))
                .Average();
            return (decimal)Math.Sqrt(variance);
        }

        private static decimal? ComputeRollingDrawdown(IReadOnlyList<BenchmarkRow> rows, int endIndex, int window)
        {
            if (rows == null || window <= 0 || endIndex < 0)
            {
                return null;
            }

            var startIndex = Math.Max(0, endIndex - window + 1);
            var peak = rows.Skip(startIndex).Take(endIndex - startIndex + 1).Max(row => row.Close);
            if (peak <= 0m)
            {
                return null;
            }

            return rows[endIndex].Close / peak - 1m;
        }

        private void PersistOutputs()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_tradeReportPath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_dailySummaryPath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_rebalanceReportPath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_comparisonSummaryPath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_signalFilePath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_portfolioSnapshotPath) ?? ".");

            File.WriteAllLines(_tradeReportPath, BuildTradeReportLines());
            File.WriteAllLines(_rebalanceReportPath, BuildRebalanceReportLines());

            var dailySummaryRows = BuildDailySummaryRows();
            var dailyLines = new List<string>
            {
                "trade_date,equity,cash,holdings_value,position_count,benchmark_close,strategy_drawdown,benchmark_nav,benchmark_drawdown"
            };
            dailyLines.AddRange(dailySummaryRows.Select(row => row.ToCsv()));
            File.WriteAllLines(_dailySummaryPath, dailyLines);

            var comparison = BuildComparisonSummary(dailySummaryRows);
            File.WriteAllText(_comparisonSummaryPath, JsonConvert.SerializeObject(comparison, Formatting.Indented), System.Text.Encoding.UTF8);
            File.WriteAllText(_signalFilePath, JsonConvert.SerializeObject(BuildSignalSnapshotPayload(), Formatting.Indented), System.Text.Encoding.UTF8);
            File.WriteAllText(_portfolioSnapshotPath, JsonConvert.SerializeObject(BuildPortfolioSnapshotPayload(), Formatting.Indented), System.Text.Encoding.UTF8);
            SetSummaryStatistic("Benchmark Total Return", comparison.BenchmarkTotalReturn.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Excess Return vs CSI300", comparison.ExcessReturn.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Benchmark Max Drawdown", comparison.BenchmarkMaxDrawdown.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Drawdown Advantage vs CSI300", comparison.DrawdownAdvantage.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Rebalance Count", _rebalanceRows.Count.ToString(CultureInfo.InvariantCulture));
            SetSummaryStatistic("Universe Size", _underlyingSymbols.Count.ToString(CultureInfo.InvariantCulture));
            _lastPersistTime = Time;
            Log($"Saved LEAN backtest outputs: trades={_tradeReportPath}, daily={_dailySummaryPath}, rebalances={_rebalanceReportPath}, compare={_comparisonSummaryPath}");
        }

        private object BuildSignalSnapshotPayload()
        {
            var lastRebalance = _rebalanceRows.LastOrDefault();
            return new
            {
                timestamp = Time,
                live_mode = LiveMode,
                rebalance_frequency = _rebalanceFrequency,
                signal_interval_minutes = (int)_liveSignalInterval.TotalMinutes,
                last_rebalance = lastRebalance == null ? null : new
                {
                    signal_date = lastRebalance.SignalDate,
                    execution_date = lastRebalance.ExecutionDate,
                    risk_state = lastRebalance.RiskState,
                    target_exposure = lastRebalance.TargetExposure,
                    selected_count = lastRebalance.SelectedCount,
                    score_spread = lastRebalance.ScoreSpread,
                    regime_score = lastRebalance.RegimeScore,
                    selected_symbols = lastRebalance.SelectedSymbols,
                },
                recent_trades = _tradeRows.TakeLast(20).ToList(),
            };
        }

        private object BuildPortfolioSnapshotPayload()
        {
            return new
            {
                timestamp = Time,
                initial_capital = _initialCapital,
                cash = Portfolio.CashBook[Currencies.CNY].Amount,
                holdings_value = Portfolio.TotalHoldingsValue,
                total_value = Portfolio.TotalPortfolioValue,
                total_pnl = Portfolio.TotalPortfolioValue - _initialCapital,
                total_return = _initialCapital == 0m ? 0m : Portfolio.TotalPortfolioValue / _initialCapital - 1m,
                holdings = Portfolio.Values
                    .Where(holding => holding.Invested)
                    .OrderByDescending(holding => holding.HoldingsValue)
                    .Select(holding => new
                    {
                        symbol = ToTsCode(holding.Symbol),
                        quantity = holding.Quantity,
                        average_price = holding.AveragePrice,
                        market_price = holding.Price,
                        market_value = holding.HoldingsValue,
                        unrealized_pnl = holding.UnrealizedProfit,
                        unrealized_return = holding.AbsoluteHoldingsCost == 0m ? 0m : holding.UnrealizedProfit / holding.AbsoluteHoldingsCost,
                    })
                    .ToList(),
            };
        }

        private void PublishMonteCarloSummaryStatistics()
        {
            if (!_monteCarloEnabled)
            {
                return;
            }

            var orderedRows = _dailyRows
                .OrderBy(row => row.TradeDate, StringComparer.Ordinal)
                .ToList();
            if (orderedRows.Count == 0)
            {
                Log("Monte Carlo summary skipped: daily summary rows are empty.");
                return;
            }

            var previousEquity = _initialCapital;
            var dailyReturns = new List<StrategyMonteCarloDailyReturn>(orderedRows.Count);
            foreach (var row in orderedRows)
            {
                var tradeDate = ParseTradeDate(row.TradeDate);
                if (tradeDate == default)
                {
                    continue;
                }

                var netReturn = previousEquity > 0m ? (double)(row.Equity / previousEquity - 1m) : 0d;
                dailyReturns.Add(new StrategyMonteCarloDailyReturn
                {
                    TradeDate = tradeDate,
                    NetReturn = netReturn
                });
                previousEquity = row.Equity;
            }

            if (dailyReturns.Count == 0)
            {
                Log("Monte Carlo summary skipped: no valid daily returns were produced.");
                return;
            }

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
                dailyReturns);
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

        private List<string> BuildTradeReportLines()
        {
            var lines = new List<string> { "timestamp,trade_date,symbol,action,quantity,fill_price,fill_value,fee,status,tag" };
            lines.AddRange(_tradeRows.Select(row => string.Join(",",
                row.Timestamp,
                row.TradeDate,
                row.Symbol,
                row.Action,
                row.Quantity.ToString(CultureInfo.InvariantCulture),
                row.FillPrice.ToString(CultureInfo.InvariantCulture),
                row.FillValue.ToString(CultureInfo.InvariantCulture),
                row.Fee.ToString(CultureInfo.InvariantCulture),
                row.Status,
                EscapeCsv(row.Tag))));
            return lines;
        }

        private List<string> BuildRebalanceReportLines()
        {
            var lines = new List<string> { "signal_date,execution_date,risk_state,target_exposure,candidate_count,selected_count,score_spread,benchmark_close,benchmark_ma,benchmark_momentum,benchmark_volatility20,benchmark_drawdown252,regime_score,strategy_drawdown,momentum_breadth,flow_breadth,recovery_mode,selected_symbols" };
            lines.AddRange(_rebalanceRows.Select(row => string.Join(",",
                row.SignalDate,
                row.ExecutionDate,
                row.RiskState,
                row.TargetExposure.ToString(CultureInfo.InvariantCulture),
                row.CandidateCount.ToString(CultureInfo.InvariantCulture),
                row.SelectedCount.ToString(CultureInfo.InvariantCulture),
                row.ScoreSpread.ToString(CultureInfo.InvariantCulture),
                row.BenchmarkClose.ToString(CultureInfo.InvariantCulture),
                row.BenchmarkMa?.ToString(CultureInfo.InvariantCulture) ?? string.Empty,
                row.BenchmarkMomentum?.ToString(CultureInfo.InvariantCulture) ?? string.Empty,
                row.BenchmarkVolatility20?.ToString(CultureInfo.InvariantCulture) ?? string.Empty,
                row.BenchmarkDrawdown252?.ToString(CultureInfo.InvariantCulture) ?? string.Empty,
                row.RegimeScore.ToString(CultureInfo.InvariantCulture),
                row.StrategyDrawdown.ToString(CultureInfo.InvariantCulture),
                row.MomentumBreadth.ToString(CultureInfo.InvariantCulture),
                row.FlowBreadth.ToString(CultureInfo.InvariantCulture),
                row.RecoveryMode ? "1" : "0",
                EscapeCsv(row.SelectedSymbols))));
            return lines;
        }

        private List<DailySummaryCsvRow> BuildDailySummaryRows()
        {
            var rows = new List<DailySummaryCsvRow>();
            var benchmarkStartClose = GetBenchmarkClose(StartDate);
            var strategyPeak = 0m;
            var benchmarkPeak = 0m;
            foreach (var row in _dailyRows.OrderBy(item => item.TradeDate, StringComparer.Ordinal))
            {
                var benchmarkNav = benchmarkStartClose > 0m ? row.BenchmarkClose / benchmarkStartClose : 0m;
                strategyPeak = Math.Max(strategyPeak, row.Equity);
                benchmarkPeak = Math.Max(benchmarkPeak, benchmarkNav);
                rows.Add(new DailySummaryCsvRow
                {
                    TradeDate = row.TradeDate,
                    Equity = row.Equity,
                    Cash = row.Cash,
                    HoldingsValue = row.HoldingsValue,
                    PositionCount = row.PositionCount,
                    BenchmarkClose = row.BenchmarkClose,
                    StrategyDrawdown = strategyPeak > 0m ? row.Equity / strategyPeak - 1m : 0m,
                    BenchmarkNav = benchmarkNav,
                    BenchmarkDrawdown = benchmarkPeak > 0m ? benchmarkNav / benchmarkPeak - 1m : 0m
                });
            }

            if (rows.Count == 0)
            {
                rows.Add(new DailySummaryCsvRow
                {
                    TradeDate = EndDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                    Equity = Portfolio.TotalPortfolioValue,
                    Cash = Portfolio.CashBook[Currencies.CNY].Amount,
                    HoldingsValue = Portfolio.TotalHoldingsValue,
                    PositionCount = Portfolio.Values.Count(holding => holding.Invested),
                    BenchmarkClose = GetBenchmarkClose(EndDate),
                    StrategyDrawdown = 0m,
                    BenchmarkNav = 0m,
                    BenchmarkDrawdown = 0m
                });
            }

            return rows;
        }

        private ComparisonSummary BuildComparisonSummary(IReadOnlyList<DailySummaryCsvRow> dailyRows)
        {
            var strategyTotalReturn = _initialCapital > 0m
                ? Portfolio.TotalPortfolioValue / _initialCapital - 1m
                : 0m;
            var benchmarkStartClose = GetBenchmarkClose(StartDate);
            var benchmarkEndClose = GetBenchmarkClose(EndDate);
            var benchmarkTotalReturn = benchmarkStartClose > 0m
                ? benchmarkEndClose / benchmarkStartClose - 1m
                : 0m;
            var strategyMaxDrawdown = dailyRows.Count > 0
                ? dailyRows.Min(row => row.StrategyDrawdown)
                : 0m;
            var benchmarkMaxDrawdown = dailyRows.Count > 0
                ? dailyRows.Min(row => row.BenchmarkDrawdown)
                : 0m;

            return new ComparisonSummary
            {
                StartDate = StartDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                EndDate = EndDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                FinalEquity = Portfolio.TotalPortfolioValue,
                BenchmarkSymbol = "000300.SH",
                StrategyTotalReturn = strategyTotalReturn,
                BenchmarkTotalReturn = benchmarkTotalReturn,
                ExcessReturn = strategyTotalReturn - benchmarkTotalReturn,
                StrategyMaxDrawdown = strategyMaxDrawdown,
                BenchmarkMaxDrawdown = benchmarkMaxDrawdown,
                DrawdownAdvantage = Math.Abs(benchmarkMaxDrawdown) - Math.Abs(strategyMaxDrawdown),
                RebalanceCount = _rebalanceRows.Count,
                TradeCount = _tradeRows.Count
            };
        }

        private string ResolveOutputPath(string value, string defaultFileName)
        {
            var path = string.IsNullOrWhiteSpace(value) ? defaultFileName : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private string ResolvePath(string value, string defaultValue)
        {
            var path = string.IsNullOrWhiteSpace(value) ? defaultValue : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var parameter = GetParameter(name);
            return decimal.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private decimal? GetOptionalDecimalParameter(string name)
        {
            var parameter = GetParameter(name);
            return decimal.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            var parameter = GetParameter(name);
            return int.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private bool GetBoolParameter(string name, bool defaultValue)
        {
            var parameter = GetParameter(name);
            return bool.TryParse(parameter, out var parsed) ? parsed : defaultValue;
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            var parameter = GetParameter(name);
            return DateTime.TryParse(parameter, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var parsed)
                ? parsed
                : defaultValue;
        }

        private bool ShouldRunLiveEvaluation(DateTime signalDate)
        {
            if (!LiveMode)
            {
                return false;
            }

            if (_lastLiveEvaluationTime != default && Time > _lastLiveEvaluationTime && Time - _lastLiveEvaluationTime < _liveSignalInterval)
            {
                return false;
            }

            if (_rebalanceFrequency == "interval")
            {
                return true;
            }

            var evaluationKey = BuildLiveRebalanceKey(signalDate);
            return !string.Equals(_lastLiveRebalanceKey, evaluationKey, StringComparison.Ordinal);
        }

        private string BuildLiveRebalanceKey(DateTime signalDate)
        {
            return _rebalanceFrequency switch
            {
                "daily" => signalDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                _ => signalDate.ToString("yyyyMM", CultureInfo.InvariantCulture),
            };
        }

        private bool ShouldPersistLiveSnapshot()
        {
            if (!LiveMode)
            {
                return false;
            }

            if (_lastPersistTime == default)
            {
                return true;
            }

            return Time > _lastPersistTime && Time - _lastPersistTime >= _liveSnapshotInterval;
        }

        private static decimal ParseDecimal(string value)
        {
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : 0m;
        }

        private static string NormalizeRebalanceFrequency(string value)
        {
            var normalized = (value ?? string.Empty).Trim().ToLowerInvariant();
            return normalized switch
            {
                "daily" => "daily",
                "interval" => "interval",
                _ => "monthly",
            };
        }

        private static decimal? ParseNullableDecimal(string value)
        {
            return decimal.TryParse(value, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : null;
        }

        private bool IsBroadUniverseFeature(AShareLlmQuantFeatureData feature)
        {
            return feature != null
                && feature.InUniverse
                && feature.Close.HasValue
                && feature.Close.Value >= _signalSettings.MinPrice
                && feature.CircMv.HasValue
                && feature.CircMv.Value >= _signalSettings.MinCircMv
                && feature.TurnoverRateF.HasValue
                && feature.TurnoverRateF.Value >= _signalSettings.MinTurnoverRateF;
        }

        private static decimal ScoreTrend(decimal close, decimal? movingAverage, decimal weight)
        {
            if (!movingAverage.HasValue || movingAverage.Value <= 0m || weight <= 0m)
            {
                return 0m;
            }

            return close >= movingAverage.Value ? weight : -weight;
        }

        private static decimal ScoreCentered(decimal value, decimal center, decimal scale, decimal weight)
        {
            if (scale <= 0m || weight == 0m)
            {
                return 0m;
            }

            var normalized = Clamp((value - center) / scale, -1m, 1m);
            return normalized * weight;
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue)
            {
                return minValue;
            }

            return value > maxValue ? maxValue : value;
        }

        private static decimal Lerp(decimal start, decimal end, decimal amount)
        {
            return start + (end - start) * Clamp(amount, 0m, 1m);
        }

        private string BuildRiskStateLabel(decimal regimeScore, decimal targetExposure)
        {
            string label;
            if (regimeScore >= 0.55m)
            {
                label = "RISK_ON";
            }
            else if (regimeScore >= 0.20m)
            {
                label = "RISK_UP";
            }
            else if (regimeScore >= -0.15m)
            {
                label = "NEUTRAL";
            }
            else if (targetExposure <= 0.50m)
            {
                label = "CAPITAL_PRESERVATION";
            }
            else
            {
                label = "DEFENSIVE";
            }

            if (_recoveryMode)
            {
                label += "+RECOVERY";
            }

            return label;
        }

        private static string ToTsCode(Symbol symbol)
        {
            var suffix = symbol.ID.Market == Market.SSE ? "SH" : "SZ";
            return $"{symbol.Value}.{suffix}";
        }

        private static string EscapeCsv(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }

            if (!value.Contains(',') && !value.Contains('"') && !value.Contains('\n'))
            {
                return value;
            }

            return "\"" + value.Replace("\"", "\"\"") + "\"";
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

        private sealed class BenchmarkRow
        {
            public DateTime TradeDate { get; set; }
            public decimal Close { get; set; }
            public decimal? PctChg { get; set; }
            public decimal? Ma20 { get; set; }
            public decimal? Ma60 { get; set; }
            public decimal? Ma120 { get; set; }
            public decimal? Momentum20 { get; set; }
            public decimal? Momentum60 { get; set; }
            public decimal? Volatility20 { get; set; }
            public decimal? Drawdown120 { get; set; }
            public decimal? Drawdown252 { get; set; }
        }

        private sealed class TradeRow
        {
            public string Timestamp { get; set; }
            public string TradeDate { get; set; }
            public string Symbol { get; set; }
            public string Action { get; set; }
            public decimal Quantity { get; set; }
            public decimal FillPrice { get; set; }
            public decimal FillValue { get; set; }
            public decimal Fee { get; set; }
            public string Status { get; set; }
            public string Tag { get; set; }
        }

        private sealed class RebalanceRow
        {
            public string SignalDate { get; set; }
            public string ExecutionDate { get; set; }
            public string RiskState { get; set; }
            public decimal TargetExposure { get; set; }
            public int CandidateCount { get; set; }
            public int SelectedCount { get; set; }
            public decimal ScoreSpread { get; set; }
            public decimal BenchmarkClose { get; set; }
            public decimal? BenchmarkMa { get; set; }
            public decimal? BenchmarkMomentum { get; set; }
            public decimal? BenchmarkVolatility20 { get; set; }
            public decimal? BenchmarkDrawdown252 { get; set; }
            public decimal RegimeScore { get; set; }
            public decimal StrategyDrawdown { get; set; }
            public decimal MomentumBreadth { get; set; }
            public decimal FlowBreadth { get; set; }
            public bool RecoveryMode { get; set; }
            public string SelectedSymbols { get; set; }
        }

        private sealed class MarketState
        {
            public string Label { get; set; }
            public decimal RegimeScore { get; set; }
            public decimal TargetExposure { get; set; }
            public decimal StrategyDrawdown { get; set; }
            public decimal MomentumBreadth { get; set; }
            public decimal FlowBreadth { get; set; }
        }

        private sealed class DailySummaryRow
        {
            public string TradeDate { get; set; }
            public decimal Equity { get; set; }
            public decimal Cash { get; set; }
            public decimal HoldingsValue { get; set; }
            public int PositionCount { get; set; }
            public decimal BenchmarkClose { get; set; }
        }

        private sealed class DailySummaryCsvRow
        {
            public string TradeDate { get; set; }
            public decimal Equity { get; set; }
            public decimal Cash { get; set; }
            public decimal HoldingsValue { get; set; }
            public int PositionCount { get; set; }
            public decimal BenchmarkClose { get; set; }
            public decimal StrategyDrawdown { get; set; }
            public decimal BenchmarkNav { get; set; }
            public decimal BenchmarkDrawdown { get; set; }

            public string ToCsv()
            {
                return string.Join(",",
                    TradeDate,
                    Equity.ToString(CultureInfo.InvariantCulture),
                    Cash.ToString(CultureInfo.InvariantCulture),
                    HoldingsValue.ToString(CultureInfo.InvariantCulture),
                    PositionCount.ToString(CultureInfo.InvariantCulture),
                    BenchmarkClose.ToString(CultureInfo.InvariantCulture),
                    StrategyDrawdown.ToString(CultureInfo.InvariantCulture),
                    BenchmarkNav.ToString(CultureInfo.InvariantCulture),
                    BenchmarkDrawdown.ToString(CultureInfo.InvariantCulture));
            }
        }

        private sealed class ComparisonSummary
        {
            [JsonProperty("start_date")]
            public string StartDate { get; set; }

            [JsonProperty("end_date")]
            public string EndDate { get; set; }

            [JsonProperty("benchmark_symbol")]
            public string BenchmarkSymbol { get; set; }

            [JsonProperty("final_equity")]
            public decimal FinalEquity { get; set; }

            [JsonProperty("strategy_total_return")]
            public decimal StrategyTotalReturn { get; set; }

            [JsonProperty("benchmark_total_return")]
            public decimal BenchmarkTotalReturn { get; set; }

            [JsonProperty("excess_return")]
            public decimal ExcessReturn { get; set; }

            [JsonProperty("strategy_max_drawdown")]
            public decimal StrategyMaxDrawdown { get; set; }

            [JsonProperty("benchmark_max_drawdown")]
            public decimal BenchmarkMaxDrawdown { get; set; }

            [JsonProperty("drawdown_advantage")]
            public decimal DrawdownAdvantage { get; set; }

            [JsonProperty("rebalance_count")]
            public int RebalanceCount { get; set; }

            [JsonProperty("trade_count")]
            public int TradeCount { get; set; }
        }
    }
}
