using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Standard LEAN A-share industry rotation strategy driven by offline monthly rebalance plans.
    /// </summary>
    public class AShareIndustryRotationPlanAlgorithm : QCAlgorithm
    {
        private const decimal AShareLotSize = 100m;
        private readonly Dictionary<DateTime, IndustryRotationPlanSnapshot> _plansByDate = new();
        private readonly Dictionary<DateTime, decimal> _benchmarkCloseByDate = new();
        private readonly List<DateTime> _benchmarkDates = new();
        private readonly List<IndustryRotationTradeRow> _tradeRows = new();
        private readonly List<IndustryRotationDailyRow> _dailyRows = new();
        private readonly List<IndustryRotationRebalanceRow> _rebalanceRows = new();
        private readonly List<Symbol> _symbols = new();

        private string _planFilePath;
        private string _benchmarkFilePath;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _rebalanceReportPath;
        private string _comparisonSummaryPath;
        private string _variantName;
        private Symbol _anchorSymbol;
        private decimal _initialCapital;
        private decimal _targetWeightBuffer;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private decimal _monteCarloFactorPerturbationScale;
        private DateTime _lastDailySummaryDate;
        private DateTime _lastExecutedPlanDate;
        private DateTime _lastBenchmarkFileWriteTimeUtc;
        private DateTime _lastPlanFileWriteTimeUtc;
        private bool _monteCarloEnabled;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetTimeZone(TimeZones.Shanghai);
            Settings.MinimumOrderMarginPortfolioPercentage = 0m;

            _initialCapital = GetDecimalParameter("initial-capital", 1000000m);
            _targetWeightBuffer = GetDecimalParameter("target-weight-buffer", 0.985m);
            _variantName = GetParameter("variant-name")?.Trim();
            _planFilePath = ResolvePath(GetParameter("plan-file"), Path.Combine(Globals.DataFolder, "alternative", "ashare-industry-rotation", "rotation_resonance.csv"));
            _benchmarkFilePath = ResolvePath(GetParameter("benchmark-file"), Path.Combine(Globals.DataFolder, "alternative", "ashare-industry-rotation", "benchmark", "000300.SH.csv"));
            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "ashare-industry-rotation-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "ashare-industry-rotation-daily.csv");
            _rebalanceReportPath = ResolveOutputPath(GetParameter("rebalance-report-file"), "ashare-industry-rotation-rebalances.csv");
            _comparisonSummaryPath = ResolveOutputPath(GetParameter("comparison-summary-file"), "ashare-industry-rotation-comparison.json");

            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", true);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);

            SetCash(_initialCapital);
            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2025, 12, 31)));

            LoadBenchmarkRows();
            LoadPlans();
            SetBenchmark(time => GetBenchmarkClose(time));
            EnsurePlanSymbolsRegistered();

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException($"No rebalance targets were loaded from {_planFilePath}");
            }

            Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.BeforeMarketClose(_anchorSymbol, 1), ExecuteTodayPlan);
            Schedule.On(DateRules.EveryDay(_anchorSymbol), TimeRules.AfterMarketClose(_anchorSymbol, 0), CaptureDailySummary);

            var resolvedVariant = string.IsNullOrWhiteSpace(_variantName) ? InferVariantName(_planFilePath) : _variantName;
            SetRuntimeStatistic("Variant", resolvedVariant);
            SetRuntimeStatistic("Plan Dates", _plansByDate.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Universe", _symbols.Count.ToString(CultureInfo.InvariantCulture));
            Log($"AShareIndustryRotationPlanAlgorithm initialized variant={resolvedVariant} planDates={_plansByDate.Count} symbols={_symbols.Count}");
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
            _tradeRows.Add(new IndustryRotationTradeRow
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

        private void ExecuteTodayPlan()
        {
            RefreshPlanInputsIfChanged();
            if (_lastExecutedPlanDate == Time.Date)
            {
                return;
            }

            if (!_plansByDate.TryGetValue(Time.Date, out var plan) || plan == null || plan.Rows.Count == 0)
            {
                return;
            }

            ExecuteRebalance(plan);
            _lastExecutedPlanDate = Time.Date;
        }

        private void ExecuteRebalance(IndustryRotationPlanSnapshot plan)
        {
            var desiredQuantities = BuildTargetQuantities(plan.Rows);
            var tag =
                $"variant={plan.Variant};signal_date={plan.SignalDate:yyyyMMdd};execution_date={plan.ExecutionDate:yyyyMMdd};" +
                $"industries={plan.SelectedIndustries}";

            var currentSymbols = _symbols
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

            foreach (var row in plan.Rows.OrderByDescending(item => item.TargetWeight))
            {
                if (!desiredQuantities.TryGetValue(row.Symbol, out var desiredQuantity))
                {
                    continue;
                }

                var currentQuantity = Portfolio[row.Symbol].Quantity;
                var delta = desiredQuantity - currentQuantity;
                if (delta <= 0m)
                {
                    continue;
                }

                var affordable = GetAffordableBuyQuantity(row.Symbol, delta);
                if (affordable > 0m)
                {
                    MarketOrder(row.Symbol, affordable, false, $"{tag};phase=add;industry={row.Industry}");
                }
            }

            foreach (var symbol in _symbols.Where(symbol => Portfolio[symbol].Invested && !desiredQuantities.ContainsKey(symbol)))
            {
                if (Portfolio[symbol].Quantity > 0m)
                {
                    Liquidate(symbol, $"{tag};phase=clear");
                }
            }

            _rebalanceRows.Add(new IndustryRotationRebalanceRow
            {
                SignalDate = plan.SignalDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                ExecutionDate = plan.ExecutionDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Variant = plan.Variant,
                HoldingsCount = plan.Rows.Count,
                SelectedIndustries = plan.SelectedIndustries,
                TargetExposure = plan.Rows.Sum(row => row.TargetWeight),
                SelectedSymbols = string.Join(";", plan.Rows.Select(row => $"{ToTsCode(row.Symbol)}@{row.TargetWeight:F4}|{row.Industry}"))
            });

            SetRuntimeStatistic("Last Rebalance", plan.ExecutionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Industries", plan.SelectedIndustries);
            SetRuntimeStatistic("Holdings", plan.Rows.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Equity", Portfolio.TotalPortfolioValue.ToString("F0", CultureInfo.InvariantCulture));
        }

        private Dictionary<Symbol, decimal> BuildTargetQuantities(IReadOnlyList<IndustryRotationPlanRow> rows)
        {
            var desired = new Dictionary<Symbol, decimal>();
            if (rows == null || rows.Count == 0)
            {
                return desired;
            }

            var investableValue = Portfolio.TotalPortfolioValue * _targetWeightBuffer;
            foreach (var row in rows)
            {
                if (!Securities.TryGetValue(row.Symbol, out var security))
                {
                    continue;
                }

                var price = security.Price;
                if (price <= 0m)
                {
                    continue;
                }

                var targetValue = investableValue * row.TargetWeight;
                var rawQuantity = targetValue / price;
                var quantity = NormalizeLotQuantity(rawQuantity, AShareLotSize);
                if (quantity > 0m)
                {
                    desired[row.Symbol] = quantity;
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

        private decimal EstimateBuyOrderCost(Symbol symbol, decimal quantity, decimal price)
        {
            var orderValue = quantity * price;
            var commission = Math.Max(orderValue * AShareStockFeeModel.DefaultCommissionRate, AShareStockFeeModel.DefaultMinimumCommission);
            var transferFee = orderValue * AShareStockFeeModel.DefaultTransferFeeRate;
            return orderValue + commission + transferFee;
        }

        private void CaptureDailySummary()
        {
            if (_lastDailySummaryDate == Time.Date)
            {
                return;
            }

            _dailyRows.Add(new IndustryRotationDailyRow
            {
                TradeDate = Time.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                Equity = Portfolio.TotalPortfolioValue,
                Cash = Portfolio.CashBook[Currencies.CNY].Amount,
                HoldingsValue = Portfolio.TotalHoldingsValue,
                PositionCount = Portfolio.Values.Count(holding => holding.Invested),
                BenchmarkClose = GetBenchmarkClose(Time.Date)
            });
            _lastDailySummaryDate = Time.Date;
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

        private void PersistOutputs()
        {
            WriteLines(_tradeReportPath, BuildTradeReportLines());

            var dailySummaryRows = BuildDailySummaryRows();
            WriteLines(_dailySummaryPath, BuildDailySummaryLines(dailySummaryRows));
            WriteLines(_rebalanceReportPath, BuildRebalanceReportLines());

            var comparison = BuildComparisonSummary(dailySummaryRows);
            WriteText(_comparisonSummaryPath, JsonConvert.SerializeObject(comparison, Formatting.Indented));
            SetSummaryStatistic("Benchmark Total Return", comparison.BenchmarkTotalReturn.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Excess Return vs CSI300", comparison.ExcessReturn.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Benchmark Max Drawdown", comparison.BenchmarkMaxDrawdown.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Drawdown Advantage vs CSI300", comparison.DrawdownAdvantage.ToString("P2", CultureInfo.InvariantCulture));
            SetSummaryStatistic("Rebalance Count", _rebalanceRows.Count.ToString(CultureInfo.InvariantCulture));
            SetSummaryStatistic("Universe Size", _symbols.Count.ToString(CultureInfo.InvariantCulture));
        }

        private IEnumerable<string> BuildTradeReportLines()
        {
            yield return "timestamp,trade_date,symbol,action,quantity,fill_price,fill_value,fee,status,tag";
            foreach (var row in _tradeRows)
            {
                yield return string.Join(",",
                    row.Timestamp,
                    row.TradeDate,
                    row.Symbol,
                    row.Action,
                    row.Quantity.ToString(CultureInfo.InvariantCulture),
                    row.FillPrice.ToString(CultureInfo.InvariantCulture),
                    row.FillValue.ToString(CultureInfo.InvariantCulture),
                    row.Fee.ToString(CultureInfo.InvariantCulture),
                    row.Status,
                    EscapeCsv(row.Tag));
            }
        }

        private IEnumerable<string> BuildDailySummaryLines(IReadOnlyList<IndustryRotationDailyCsvRow> rows)
        {
            yield return "trade_date,equity,cash,holdings_value,position_count,benchmark_close,strategy_drawdown,benchmark_nav,benchmark_drawdown";
            foreach (var row in rows.OrderBy(item => item.TradeDate, StringComparer.Ordinal))
            {
                yield return row.ToCsv();
            }
        }

        private IEnumerable<string> BuildRebalanceReportLines()
        {
            yield return "signal_date,execution_date,variant,holdings_count,target_exposure,selected_industries,selected_symbols";
            foreach (var row in _rebalanceRows.OrderBy(item => item.ExecutionDate, StringComparer.Ordinal))
            {
                yield return string.Join(",",
                    row.SignalDate,
                    row.ExecutionDate,
                    row.Variant,
                    row.HoldingsCount.ToString(CultureInfo.InvariantCulture),
                    row.TargetExposure.ToString(CultureInfo.InvariantCulture),
                    EscapeCsv(row.SelectedIndustries),
                    EscapeCsv(row.SelectedSymbols));
            }
        }

        private List<IndustryRotationDailyCsvRow> BuildDailySummaryRows()
        {
            var rows = new List<IndustryRotationDailyCsvRow>();
            var benchmarkStartClose = GetBenchmarkBaseClose();
            var strategyPeak = 0m;
            var benchmarkPeak = 0m;

            foreach (var row in _dailyRows.OrderBy(item => item.TradeDate, StringComparer.Ordinal))
            {
                var benchmarkNav = benchmarkStartClose > 0m ? row.BenchmarkClose / benchmarkStartClose : 0m;
                strategyPeak = Math.Max(strategyPeak, row.Equity);
                benchmarkPeak = Math.Max(benchmarkPeak, benchmarkNav);
                rows.Add(new IndustryRotationDailyCsvRow
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
                var benchmarkClose = GetBenchmarkClose(EndDate);
                rows.Add(new IndustryRotationDailyCsvRow
                {
                    TradeDate = EndDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                    Equity = Portfolio.TotalPortfolioValue,
                    Cash = Portfolio.CashBook[Currencies.CNY].Amount,
                    HoldingsValue = Portfolio.TotalHoldingsValue,
                    PositionCount = Portfolio.Values.Count(holding => holding.Invested),
                    BenchmarkClose = benchmarkClose,
                    StrategyDrawdown = 0m,
                    BenchmarkNav = 0m,
                    BenchmarkDrawdown = 0m
                });
            }

            return rows;
        }

        private IndustryRotationComparisonSummary BuildComparisonSummary(IReadOnlyList<IndustryRotationDailyCsvRow> dailyRows)
        {
            var strategyTotalReturn = _initialCapital > 0m
                ? Portfolio.TotalPortfolioValue / _initialCapital - 1m
                : 0m;
            var benchmarkStartClose = GetBenchmarkBaseClose();
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

            return new IndustryRotationComparisonSummary
            {
                StartDate = StartDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                EndDate = EndDate.ToString("yyyyMMdd", CultureInfo.InvariantCulture),
                BenchmarkSymbol = "000300.SH",
                FinalEquity = Portfolio.TotalPortfolioValue,
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

        private void LoadBenchmarkRows()
        {
            if (!File.Exists(_benchmarkFilePath))
            {
                throw new FileNotFoundException("Benchmark file was not found.", _benchmarkFilePath);
            }

            _benchmarkCloseByDate.Clear();
            _benchmarkDates.Clear();
            foreach (var line in File.ReadLines(_benchmarkFilePath))
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var csv = line.Split(',');
                if (csv.Length < 2)
                {
                    continue;
                }

                if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var tradeDate))
                {
                    continue;
                }

                if (!decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var close))
                {
                    continue;
                }

                _benchmarkCloseByDate[tradeDate.Date] = close;
                _benchmarkDates.Add(tradeDate.Date);
            }

            _benchmarkDates.Sort();
            _lastBenchmarkFileWriteTimeUtc = File.GetLastWriteTimeUtc(_benchmarkFilePath);
        }

        private void LoadPlans()
        {
            if (!File.Exists(_planFilePath))
            {
                throw new FileNotFoundException("Plan file was not found.", _planFilePath);
            }

            _plansByDate.Clear();
            var grouped = new Dictionary<DateTime, List<IndustryRotationPlanRow>>();
            var signalDates = new Dictionary<DateTime, DateTime>();
            var variants = new Dictionary<DateTime, string>();
            var industryText = new Dictionary<DateTime, string>();

            foreach (var line in File.ReadLines(_planFilePath))
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("variant", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var csv = line.Split(',');
                if (csv.Length < 7)
                {
                    continue;
                }

                if (!DateTime.TryParseExact(csv[1], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var signalDate))
                {
                    continue;
                }

                if (!DateTime.TryParseExact(csv[2], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var executionDate))
                {
                    continue;
                }

                var symbol = ParseTsCode(csv[3]);
                if (symbol == null)
                {
                    continue;
                }

                if (!decimal.TryParse(csv[4], NumberStyles.Any, CultureInfo.InvariantCulture, out var targetWeight))
                {
                    continue;
                }

                if (!grouped.TryGetValue(executionDate.Date, out var rows))
                {
                    rows = new List<IndustryRotationPlanRow>();
                    grouped[executionDate.Date] = rows;
                }

                rows.Add(new IndustryRotationPlanRow
                {
                    Symbol = symbol,
                    TargetWeight = targetWeight,
                    Industry = csv[5]
                });
                signalDates[executionDate.Date] = signalDate.Date;
                variants[executionDate.Date] = csv[0];
                industryText[executionDate.Date] = csv.Length > 10 ? csv[10] : string.Empty;
            }

            foreach (var pair in grouped)
            {
                _plansByDate[pair.Key] = new IndustryRotationPlanSnapshot
                {
                    ExecutionDate = pair.Key,
                    SignalDate = signalDates.TryGetValue(pair.Key, out var signalDate) ? signalDate : pair.Key,
                    Variant = variants.TryGetValue(pair.Key, out var variant) ? variant : InferVariantName(_planFilePath),
                    SelectedIndustries = industryText.TryGetValue(pair.Key, out var text) ? text : string.Empty,
                    Rows = pair.Value
                        .OrderByDescending(row => row.TargetWeight)
                        .ThenBy(row => row.Symbol.Value, StringComparer.Ordinal)
                        .ToList()
                };
            }

            _lastPlanFileWriteTimeUtc = File.GetLastWriteTimeUtc(_planFilePath);
        }

        private void EnsurePlanSymbolsRegistered()
        {
            foreach (var tsCode in _plansByDate.Values
                         .SelectMany(plan => plan.Rows)
                         .Select(row => row.Symbol)
                         .Distinct()
                         .OrderBy(symbol => symbol.Value, StringComparer.Ordinal))
            {
                if (_symbols.Contains(tsCode))
                {
                    continue;
                }

                var equity = AddEquity(tsCode.Value, Resolution.Daily, tsCode.ID.Market);
                equity.FeeModel = new AShareStockFeeModel();
                equity.FillModel = new AShareStockFillModel();
                equity.BuyingPowerModel = new AShareStockBuyingPowerModel();
                equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                equity.Session.Size = 2;
                _symbols.Add(equity.Symbol);
                _anchorSymbol ??= equity.Symbol;
            }
        }

        private void RefreshPlanInputsIfChanged()
        {
            var benchmarkChanged = File.Exists(_benchmarkFilePath) && File.GetLastWriteTimeUtc(_benchmarkFilePath) > _lastBenchmarkFileWriteTimeUtc;
            var planChanged = File.Exists(_planFilePath) && File.GetLastWriteTimeUtc(_planFilePath) > _lastPlanFileWriteTimeUtc;
            if (!benchmarkChanged && !planChanged)
            {
                return;
            }

            if (benchmarkChanged)
            {
                LoadBenchmarkRows();
            }

            if (planChanged)
            {
                LoadPlans();
                EnsurePlanSymbolsRegistered();
                SetRuntimeStatistic("Plan Dates", _plansByDate.Count.ToString(CultureInfo.InvariantCulture));
                SetRuntimeStatistic("Universe", _symbols.Count.ToString(CultureInfo.InvariantCulture));
                Log($"Reloaded industry rotation plans planDates={_plansByDate.Count} symbols={_symbols.Count} updated_at={Time:yyyy-MM-dd HH:mm:ss}");
            }
        }

        private decimal GetBenchmarkClose(DateTime date)
        {
            if (_benchmarkCloseByDate.TryGetValue(date.Date, out var close))
            {
                return close;
            }

            var prior = _benchmarkDates.LastOrDefault(item => item <= date.Date);
            return prior == default ? 0m : _benchmarkCloseByDate.GetValueOrDefault(prior, 0m);
        }

        private decimal GetBenchmarkBaseClose()
        {
            var candidate = _benchmarkDates.FirstOrDefault(item => item >= StartDate.Date);
            if (candidate != default)
            {
                return _benchmarkCloseByDate.GetValueOrDefault(candidate, 0m);
            }

            return GetBenchmarkClose(StartDate);
        }

        private static Symbol ParseTsCode(string tsCode)
        {
            if (string.IsNullOrWhiteSpace(tsCode))
            {
                return null;
            }

            var parts = tsCode.Split('.');
            if (parts.Length != 2)
            {
                return null;
            }

            var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
            return QuantConnect.Symbol.Create(parts[0], SecurityType.Equity, market);
        }

        private static decimal NormalizeLotQuantity(decimal quantity, decimal lotSize)
        {
            if (quantity <= 0m || lotSize <= 0m)
            {
                return 0m;
            }

            return Math.Floor(quantity / lotSize) * lotSize;
        }

        private static string ToTsCode(Symbol symbol)
        {
            var suffix = symbol.ID.Market == Market.SSE ? "SH" : "SZ";
            return $"{symbol.Value}.{suffix}";
        }

        private static string InferVariantName(string planPath)
        {
            return Path.GetFileNameWithoutExtension(planPath ?? string.Empty) ?? "rotation_resonance";
        }

        private static string ResolvePath(string value, string fallback)
        {
            var path = string.IsNullOrWhiteSpace(value) ? fallback : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private static string ResolveOutputPath(string value, string fallbackFileName)
        {
            var path = string.IsNullOrWhiteSpace(value)
                ? Path.Combine(Globals.ResultsDestinationFolder, fallbackFileName)
                : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private static void WriteLines(string path, IEnumerable<string> lines)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            var file = new FileInfo(path);
            file.Directory?.Create();
            File.WriteAllLines(file.FullName, lines);
        }

        private static void WriteText(string path, string content)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return;
            }

            var file = new FileInfo(path);
            file.Directory?.Create();
            File.WriteAllText(file.FullName, content);
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var parameter = GetParameter(name);
            return decimal.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
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

        private static DateTime ParseTradeDate(string value)
        {
            return DateTime.TryParseExact(value ?? string.Empty, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed)
                ? parsed
                : default;
        }

        private static string EscapeCsv(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }

            return value.Contains(',') || value.Contains('"')
                ? $"\"{value.Replace("\"", "\"\"", StringComparison.Ordinal)}\""
                : value;
        }

        private sealed class IndustryRotationPlanSnapshot
        {
            public DateTime ExecutionDate { get; set; }
            public DateTime SignalDate { get; set; }
            public string Variant { get; set; }
            public string SelectedIndustries { get; set; }
            public List<IndustryRotationPlanRow> Rows { get; set; } = new();
        }

        private sealed class IndustryRotationPlanRow
        {
            public Symbol Symbol { get; set; }
            public decimal TargetWeight { get; set; }
            public string Industry { get; set; }
        }

        private sealed class IndustryRotationTradeRow
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

        private sealed class IndustryRotationDailyRow
        {
            public string TradeDate { get; set; }
            public decimal Equity { get; set; }
            public decimal Cash { get; set; }
            public decimal HoldingsValue { get; set; }
            public int PositionCount { get; set; }
            public decimal BenchmarkClose { get; set; }
        }

        private sealed class IndustryRotationDailyCsvRow
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

        private sealed class IndustryRotationRebalanceRow
        {
            public string SignalDate { get; set; }
            public string ExecutionDate { get; set; }
            public string Variant { get; set; }
            public int HoldingsCount { get; set; }
            public decimal TargetExposure { get; set; }
            public string SelectedIndustries { get; set; }
            public string SelectedSymbols { get; set; }
        }

        private sealed class IndustryRotationComparisonSummary
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
