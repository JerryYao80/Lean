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
using QuantConnect.Data;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Multi-field T+0 ETF strategy that consumes exported feature snapshots through LEAN custom data.
    /// It enters at the daily open and exits at the daily close of the same trading session.
    /// </summary>
    public class AShareEtfT0FeatureIntradayAlgorithm : QCAlgorithm
    {
        private const decimal CommissionRate = 0.0003m;
        private const decimal MinimumCommission = 5m;
        private const decimal ShanghaiTransferFeeRate = 0.00002m;

        private readonly Dictionary<Symbol, Symbol> _featureToUnderlying = new Dictionary<Symbol, Symbol>();
        private readonly Dictionary<Symbol, AShareEtfT0FeatureData> _latestFeaturesByUnderlying = new Dictionary<Symbol, AShareEtfT0FeatureData>();
        private readonly List<TradeDecision> _tradeDecisions = new List<TradeDecision>();
        private readonly List<DailySummaryRow> _dailySummaries = new List<DailySummaryRow>();
        private readonly List<AllocationRow> _allocationRows = new List<AllocationRow>();
        private readonly List<ActionPlanRow> _actionPlanRows = new List<ActionPlanRow>();
        private Symbol _anchorSymbol;
        private int _topN;
        private decimal _minScoreSpread;
        private decimal? _maxAverageGapAbs;
        private bool _riskRegimeFilterEnabled;
        private decimal? _riskRegimeMomentumThreshold;
        private decimal? _riskRegimeVolatilityThreshold;
        private decimal _targetPortfolioExposure;
        private bool _excludeMoneyMarketEtfs;
        private string _executionMode;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _allocationPath;
        private string _actionPlanPath;
        private DateTime _lastTradeDate;

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2024, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            _topN = GetIntParameter("top-n", 2);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.7m);
            _maxAverageGapAbs = GetOptionalDecimalParameter("max-average-gap-abs");
            _riskRegimeFilterEnabled = GetBoolParameter("risk-regime-filter-enabled", false);
            _riskRegimeMomentumThreshold = GetOptionalDecimalParameter("risk-regime-momentum-threshold");
            _riskRegimeVolatilityThreshold = GetOptionalDecimalParameter("risk-regime-volatility-threshold");
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.95m);
            _excludeMoneyMarketEtfs = GetBoolParameter("exclude-money-market-etfs", true);
            _executionMode = (GetParameter("execution-mode") ?? "synthetic").Trim().ToLowerInvariant();
            if (_executionMode != "synthetic")
            {
                throw new InvalidOperationException($"Unsupported execution-mode '{_executionMode}'. Only 'synthetic' is currently supported for this daily-bar T+0 workflow.");
            }

            SetStartDate(startDate.Year, startDate.Month, startDate.Day);
            SetEndDate(endDate.Year, endDate.Month, endDate.Day);
            SetAccountCurrency("CNY");
            SetCash(1000000);
            Portfolio.CashBook.Add("CNY", 1000000, 1.0m);
            SetBenchmark(_ => 0);

            var featureDataPath = GetParameter("feature-data-path");
            if (!string.IsNullOrWhiteSpace(featureDataPath))
            {
                AShareEtfT0FeatureData.SetBaseDirectory(featureDataPath);
            }

            _tradeReportPath = ResolveOutputPath(GetParameter("trade-report-file"), "AShareEtfT0FeatureIntradayAlgorithm-trades.csv");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "AShareEtfT0FeatureIntradayAlgorithm-daily-summary.csv");
            _allocationPath = ResolveOutputPath(GetParameter("allocation-report-file"), "AShareEtfT0FeatureIntradayAlgorithm-allocation.csv");
            _actionPlanPath = ResolveOutputPath(GetParameter("action-plan-file"), "AShareEtfT0FeatureIntradayAlgorithm-action-plan.csv");

            foreach (var ticker in AShareETFRegistry.GetT0ETFs().OrderBy(ticker => ticker))
            {
                if (_excludeMoneyMarketEtfs && IsMoneyMarketTicker(ticker))
                {
                    continue;
                }

                var metadata = AShareETFRegistry.GetMetadata(ticker);
                if (metadata == null)
                {
                    continue;
                }

                var market = metadata.Market == "SSE" ? Market.SSE : Market.SZSE;
                var underlyingSymbol = QuantConnect.Symbol.Create(ticker, SecurityType.Equity, market);
                if (!HasRequiredLocalData(underlyingSymbol))
                {
                    Log($"Skipping {ticker}: missing local price or feature data");
                    continue;
                }

                var equity = AddEquity(ticker, Resolution.Daily, market);
                equity.FeeModel = new AShareETFFeeModel();
                equity.FillModel = new AShareETFFillModel();
                equity.BuyingPowerModel = new AShareETFBuyingPowerModel();
                equity.Session.Size = 2;

                var featureSecurity = AddData<AShareEtfT0FeatureData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _featureToUnderlying[featureSecurity.Symbol] = equity.Symbol;
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException("No eligible A-share T+0 ETF symbols were added.");
            }

            Schedule.On(
                DateRules.EveryDay(_anchorSymbol),
                TimeRules.BeforeMarketOpen(_anchorSymbol, 1),
                TradeSession);

            Log($"AShareEtfT0FeatureIntradayAlgorithm initialized with {_featureToUnderlying.Count} feature subscriptions");
            if (LiveMode)
            {
                Log("Synthetic execution mode active: this workflow records advisory trades and allocations but does not submit brokerage orders.");
            }
        }

        public override void OnData(Slice slice)
        {
            foreach (var pair in slice.Get<AShareEtfT0FeatureData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                if (_featureToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _latestFeaturesByUnderlying[underlying] = pair.Value;
                }
            }
        }

        private void TradeSession()
        {
            if (_lastTradeDate == Time.Date)
            {
                return;
            }
            _lastTradeDate = Time.Date;

            var dailyFeatures = _latestFeaturesByUnderlying
                .Where(pair => pair.Value != null && pair.Value.HasSignals)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            if (dailyFeatures.Count == 0)
            {
                Log($"{Time:yyyy-MM-dd} no feature snapshots available");
                return;
            }

            var scores = AShareEtfT0FeatureSignalModel.ComputeScores(dailyFeatures);
            var orderedScores = scores.Values.OrderBy(value => value).ToList();
            var scoreSpread = orderedScores.Count > 0 ? orderedScores[^1] - GetMedian(orderedScores) : 0m;
            if (_minScoreSpread > 0 && scoreSpread < _minScoreSpread)
            {
                Log($"{Time:yyyy-MM-dd} skip trading: score spread {scoreSpread:F4} below threshold {_minScoreSpread:F4}");
                return;
            }

            if (_riskRegimeFilterEnabled && _riskRegimeMomentumThreshold.HasValue && _riskRegimeVolatilityThreshold.HasValue)
            {
                var marketSignalMomentum5Mean = dailyFeatures.Values
                    .Where(feature => feature.SignalMomentum5.HasValue)
                    .Select(feature => feature.SignalMomentum5.Value)
                    .DefaultIfEmpty(0m)
                    .Average();
                var marketSignalVolatility10Mean = dailyFeatures.Values
                    .Where(feature => feature.SignalVolatility10.HasValue)
                    .Select(feature => feature.SignalVolatility10.Value)
                    .DefaultIfEmpty(0m)
                    .Average();
                if (marketSignalMomentum5Mean <= _riskRegimeMomentumThreshold.Value && marketSignalVolatility10Mean >= _riskRegimeVolatilityThreshold.Value)
                {
                    Log($"{Time:yyyy-MM-dd} skip trading: risk regime detected mom5={marketSignalMomentum5Mean:F4} vol10={marketSignalVolatility10Mean:F4}");
                    return;
                }
            }

            var ranked = scores
                .OrderByDescending(pair => pair.Value)
                .Take(_topN)
                .Where(pair => Securities.ContainsKey(pair.Key) && Securities[pair.Key].Price > 0)
                .ToList();
            if (ranked.Count == 0)
            {
                Log($"{Time:yyyy-MM-dd} no tradable symbols after ranking");
                return;
            }

            if (_maxAverageGapAbs.HasValue)
            {
                var averageGapAbs = ranked
                    .Where(pair => dailyFeatures.ContainsKey(pair.Key) && dailyFeatures[pair.Key].SignalGapAbs.HasValue)
                    .Select(pair => dailyFeatures[pair.Key].SignalGapAbs.Value)
                    .DefaultIfEmpty(0m)
                    .Average();
                if (averageGapAbs > _maxAverageGapAbs.Value)
                {
                    Log($"{Time:yyyy-MM-dd} skip trading: avg gap abs {averageGapAbs:F4} above threshold {_maxAverageGapAbs.Value:F4}");
                    return;
                }
            }

            var availableCash = Portfolio.CashBook[AccountCurrency].Amount;
            var targetBudget = Math.Min(Portfolio.TotalPortfolioValue, availableCash) * _targetPortfolioExposure;
            var remainingBudget = targetBudget;
            var remainingSlots = ranked.Count;
            var portfolioValueBefore = Portfolio.TotalPortfolioValue;
            var cashBefore = Portfolio.CashBook[AccountCurrency].Amount;
            var totalDailyPnl = 0m;
            var totalDailyFees = 0m;
            var totalEntryValue = 0m;
            var tradeDate = Time.Date;
            var selections = new List<string>();
            var dayTrades = new List<TradeDecision>();

            foreach (var pair in ranked)
            {
                if (remainingSlots <= 0 || remainingBudget <= 0)
                {
                    break;
                }

                var targetBudgetPerSymbol = remainingBudget / remainingSlots;
                remainingSlots--;

                if (!dailyFeatures.TryGetValue(pair.Key, out var feature))
                {
                    continue;
                }

                if (!feature.Open.HasValue || !feature.Close.HasValue || feature.Open.Value <= 0 || feature.Close.Value <= 0)
                {
                    continue;
                }

                var estimatedEntryPrice = GetEstimatedEntryPrice(pair.Key, feature);
                var quantity = GetOrderQuantity(pair.Key, targetBudgetPerSymbol, estimatedEntryPrice);
                if (quantity <= 0)
                {
                    continue;
                }

                var grossPnl = quantity * (feature.Close.Value - feature.Open.Value);
                var totalFees = EstimateOrderFee(pair.Key, quantity, feature.Open.Value) + EstimateOrderFee(pair.Key, quantity, feature.Close.Value);
                var netPnl = grossPnl - totalFees;
                var entryValue = quantity * feature.Open.Value;
                var targetWeight = portfolioValueBefore > 0 ? entryValue / portfolioValueBefore : 0m;

                var trade = new TradeDecision
                {
                    TradeDate = tradeDate,
                    Symbol = pair.Key.Value,
                    Score = pair.Value,
                    Quantity = quantity,
                    EntryPrice = feature.Open.Value,
                    ExitPrice = feature.Close.Value,
                    EntryValue = entryValue,
                    TargetWeight = targetWeight,
                    GrossPnl = grossPnl,
                    Fees = totalFees,
                    NetPnl = netPnl
                };

                totalDailyPnl += netPnl;
                totalDailyFees += totalFees;
                totalEntryValue += entryValue;
                remainingBudget -= quantity * feature.Open.Value + totalFees;
                Portfolio.AddTransactionRecord(Time, netPnl, netPnl >= 0);
                selections.Add($"{trade.Symbol} score={trade.Score:F4} qty={trade.Quantity} buy@{trade.EntryPrice:F3} sell@{trade.ExitPrice:F3} w={trade.TargetWeight:P2}");
                dayTrades.Add(trade);
                _tradeDecisions.Add(trade);
            }

            if (totalDailyPnl != 0)
            {
                Portfolio.CashBook[AccountCurrency].AddAmount(totalDailyPnl);
                Portfolio.InvalidateTotalPortfolioValue();
            }

            var portfolioValueAfter = Portfolio.TotalPortfolioValue;
            var cashAfter = Portfolio.CashBook[AccountCurrency].Amount;
            RecordAllocationRows(tradeDate, portfolioValueBefore, portfolioValueAfter, cashBefore, cashAfter, totalEntryValue, totalDailyPnl, dayTrades);
            var dailySummary = new DailySummaryRow
            {
                TradeDate = tradeDate,
                PortfolioValueBefore = portfolioValueBefore,
                PortfolioValueAfter = portfolioValueAfter,
                CashBefore = cashBefore,
                CashAfter = cashAfter,
                EntryValue = totalEntryValue,
                Fees = totalDailyFees,
                NetPnl = totalDailyPnl,
                SelectionCount = dayTrades.Count,
                SelectedSymbols = string.Join("|", dayTrades.Select(trade => trade.Symbol))
            };
            _dailySummaries.Add(dailySummary);
            RecordActionPlanRows(dailySummary, dayTrades);

            if (selections.Count > 0)
            {
                Log($"{Time:yyyy-MM-dd} selected {string.Join("; ", selections)} day_pnl={totalDailyPnl:F2} fees={totalDailyFees:F2} nav={portfolioValueAfter:F2}");
            }
            else
            {
                Log($"{Time:yyyy-MM-dd} selected none day_pnl={totalDailyPnl:F2} nav={portfolioValueAfter:F2}");
            }
        }

        public override void OnEndOfAlgorithm()
        {
            ExportReports();
        }

        private decimal GetEstimatedEntryPrice(Symbol symbol, AShareEtfT0FeatureData feature)
        {
            if (feature?.PreClose > 0)
            {
                var minimumPriceVariation = Securities[symbol].SymbolProperties.MinimumPriceVariation;
                return AShareETF.GetUpperPriceLimit(symbol, feature.PreClose.Value, minimumPriceVariation);
            }

            if (feature?.Open > 0)
            {
                return feature.Open.Value;
            }

            return Securities[symbol].Price;
        }

        private int GetOrderQuantity(Symbol symbol, decimal budget, decimal estimatedEntryPrice)
        {
            if (budget <= 0 || estimatedEntryPrice <= 0)
            {
                return 0;
            }

            var maxLots = (int)Math.Floor(budget / (estimatedEntryPrice * AShareETF.LotSize));
            while (maxLots > 0)
            {
                var quantity = maxLots * AShareETF.LotSize;
                if (EstimateOrderCost(symbol, quantity, estimatedEntryPrice) <= budget)
                {
                    return quantity;
                }

                maxLots--;
            }

            return 0;
        }

        private decimal EstimateOrderCost(Symbol symbol, int quantity, decimal estimatedEntryPrice)
        {
            var absoluteQuantity = Math.Abs(quantity);
            var orderValue = absoluteQuantity * estimatedEntryPrice;
            return orderValue + EstimateOrderFee(symbol, quantity, estimatedEntryPrice);
        }

        private decimal EstimateOrderFee(Symbol symbol, int quantity, decimal price)
        {
            var absoluteQuantity = Math.Abs(quantity);
            var orderValue = absoluteQuantity * price;
            var commission = Math.Max(orderValue * CommissionRate, MinimumCommission);
            var transferFee = symbol.ID.Market == Market.SSE ? absoluteQuantity * ShanghaiTransferFeeRate : 0m;
            return commission + transferFee;
        }

        private void RecordAllocationRows(
            DateTime tradeDate,
            decimal portfolioValueBefore,
            decimal portfolioValueAfter,
            decimal cashBefore,
            decimal cashAfter,
            decimal totalEntryValue,
            decimal netPnl,
            IEnumerable<TradeDecision> dayTrades)
        {
            foreach (var trade in dayTrades)
            {
                _allocationRows.Add(new AllocationRow
                {
                    TradeDate = tradeDate,
                    Phase = "open_target",
                    Asset = trade.Symbol,
                    Value = trade.EntryValue,
                    Weight = trade.TargetWeight,
                    PortfolioValue = portfolioValueBefore,
                    CashValue = cashBefore,
                    NetPnl = netPnl
                });
            }

            var cashOpenValue = Math.Max(0m, cashBefore - totalEntryValue);
            _allocationRows.Add(new AllocationRow
            {
                TradeDate = tradeDate,
                Phase = "open_target",
                Asset = "CASH",
                Value = cashOpenValue,
                Weight = portfolioValueBefore > 0 ? cashOpenValue / portfolioValueBefore : 0m,
                PortfolioValue = portfolioValueBefore,
                CashValue = cashBefore,
                NetPnl = netPnl
            });

            _allocationRows.Add(new AllocationRow
            {
                TradeDate = tradeDate,
                Phase = "close_eod",
                Asset = "CASH",
                Value = cashAfter,
                Weight = portfolioValueAfter > 0 ? cashAfter / portfolioValueAfter : 0m,
                PortfolioValue = portfolioValueAfter,
                CashValue = cashAfter,
                NetPnl = netPnl
            });
        }

        private void ExportReports()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_tradeReportPath) ?? Globals.ResultsDestinationFolder);
            File.WriteAllLines(_tradeReportPath, BuildTradeCsv(), Encoding.UTF8);
            File.WriteAllLines(_dailySummaryPath, BuildDailySummaryCsv(), Encoding.UTF8);
            File.WriteAllLines(_allocationPath, BuildAllocationCsv(), Encoding.UTF8);
            File.WriteAllLines(_actionPlanPath, BuildActionPlanCsv(), Encoding.UTF8);

            Log($"Synthetic reports saved: trades={_tradeReportPath}, daily={_dailySummaryPath}, allocation={_allocationPath}, action={_actionPlanPath}");
        }

        private IEnumerable<string> BuildActionPlanCsv()
        {
            yield return "trade_date,symbol,score,buy_time,buy_price,buy_quantity,buy_value,open_weight,sell_time,sell_price,sell_quantity,fees,net_pnl,portfolio_value_before,portfolio_value_after,cash_before,cash_after,selected_symbols";
            foreach (var row in _actionPlanRows.OrderBy(row => row.TradeDate).ThenBy(row => row.Symbol))
            {
                yield return string.Join(",", new[]
                {
                    row.TradeDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    row.Symbol,
                    FormatDecimal(row.Score),
                    row.BuyTime,
                    FormatDecimal(row.BuyPrice),
                    row.BuyQuantity.ToString(CultureInfo.InvariantCulture),
                    FormatDecimal(row.BuyValue),
                    FormatDecimal(row.OpenWeight),
                    row.SellTime,
                    FormatDecimal(row.SellPrice),
                    row.SellQuantity.ToString(CultureInfo.InvariantCulture),
                    FormatDecimal(row.Fees),
                    FormatDecimal(row.NetPnl),
                    FormatDecimal(row.PortfolioValueBefore),
                    FormatDecimal(row.PortfolioValueAfter),
                    FormatDecimal(row.CashBefore),
                    FormatDecimal(row.CashAfter),
                    EscapeCsv(row.SelectedSymbols)
                });
            }
        }

        private IEnumerable<string> BuildTradeCsv()
        {
            yield return "trade_date,symbol,score,quantity,entry_price,exit_price,entry_value,target_weight,gross_pnl,fees,net_pnl";
            foreach (var trade in _tradeDecisions.OrderBy(trade => trade.TradeDate).ThenBy(trade => trade.Symbol))
            {
                yield return string.Join(",", new[]
                {
                    trade.TradeDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    trade.Symbol,
                    FormatDecimal(trade.Score),
                    trade.Quantity.ToString(CultureInfo.InvariantCulture),
                    FormatDecimal(trade.EntryPrice),
                    FormatDecimal(trade.ExitPrice),
                    FormatDecimal(trade.EntryValue),
                    FormatDecimal(trade.TargetWeight),
                    FormatDecimal(trade.GrossPnl),
                    FormatDecimal(trade.Fees),
                    FormatDecimal(trade.NetPnl)
                });
            }
        }

        private IEnumerable<string> BuildDailySummaryCsv()
        {
            yield return "trade_date,portfolio_value_before,portfolio_value_after,cash_before,cash_after,entry_value,fees,net_pnl,selection_count,selected_symbols";
            foreach (var row in _dailySummaries.OrderBy(row => row.TradeDate))
            {
                yield return string.Join(",", new[]
                {
                    row.TradeDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    FormatDecimal(row.PortfolioValueBefore),
                    FormatDecimal(row.PortfolioValueAfter),
                    FormatDecimal(row.CashBefore),
                    FormatDecimal(row.CashAfter),
                    FormatDecimal(row.EntryValue),
                    FormatDecimal(row.Fees),
                    FormatDecimal(row.NetPnl),
                    row.SelectionCount.ToString(CultureInfo.InvariantCulture),
                    EscapeCsv(row.SelectedSymbols)
                });
            }
        }

        private IEnumerable<string> BuildAllocationCsv()
        {
            yield return "trade_date,phase,asset,value,weight,portfolio_value,cash_value,net_pnl";
            foreach (var row in _allocationRows.OrderBy(row => row.TradeDate).ThenBy(row => row.Phase).ThenBy(row => row.Asset))
            {
                yield return string.Join(",", new[]
                {
                    row.TradeDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    row.Phase,
                    row.Asset,
                    FormatDecimal(row.Value),
                    FormatDecimal(row.Weight),
                    FormatDecimal(row.PortfolioValue),
                    FormatDecimal(row.CashValue),
                    FormatDecimal(row.NetPnl)
                });
            }
        }

        private string ResolveOutputPath(string parameterValue, string defaultFileName)
        {
            if (!string.IsNullOrWhiteSpace(parameterValue))
            {
                return Path.IsPathRooted(parameterValue)
                    ? parameterValue
                    : Path.GetFullPath(Path.Combine(Globals.ResultsDestinationFolder, parameterValue));
            }

            return Path.Combine(Globals.ResultsDestinationFolder, defaultFileName);
        }

        private static string FormatDecimal(decimal value)
        {
            return value.ToString("0.########", CultureInfo.InvariantCulture);
        }

        private static string EscapeCsv(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }

            if (value.Contains(",") || value.Contains("\"") || value.Contains("\n"))
            {
                return $"\"{value.Replace("\"", "\"\"")}\"";
            }

            return value;
        }

        private void RecordActionPlanRows(DailySummaryRow dailySummary, IEnumerable<TradeDecision> dayTrades)
        {
            foreach (var trade in dayTrades)
            {
                _actionPlanRows.Add(new ActionPlanRow
                {
                    TradeDate = trade.TradeDate,
                    Symbol = trade.Symbol,
                    Score = trade.Score,
                    BuyTime = $"{trade.TradeDate:yyyy-MM-dd} 09:30:00",
                    BuyPrice = trade.EntryPrice,
                    BuyQuantity = trade.Quantity,
                    BuyValue = trade.EntryValue,
                    OpenWeight = trade.TargetWeight,
                    SellTime = $"{trade.TradeDate:yyyy-MM-dd} 15:00:00",
                    SellPrice = trade.ExitPrice,
                    SellQuantity = trade.Quantity,
                    Fees = trade.Fees,
                    NetPnl = trade.NetPnl,
                    PortfolioValueBefore = dailySummary.PortfolioValueBefore,
                    PortfolioValueAfter = dailySummary.PortfolioValueAfter,
                    CashBefore = dailySummary.CashBefore,
                    CashAfter = dailySummary.CashAfter,
                    SelectedSymbols = dailySummary.SelectedSymbols
                });
            }
        }

        private sealed class TradeDecision
        {
            public DateTime TradeDate { get; set; }
            public string Symbol { get; set; }
            public decimal Score { get; set; }
            public int Quantity { get; set; }
            public decimal EntryPrice { get; set; }
            public decimal ExitPrice { get; set; }
            public decimal EntryValue { get; set; }
            public decimal TargetWeight { get; set; }
            public decimal GrossPnl { get; set; }
            public decimal Fees { get; set; }
            public decimal NetPnl { get; set; }
        }

        private sealed class DailySummaryRow
        {
            public DateTime TradeDate { get; set; }
            public decimal PortfolioValueBefore { get; set; }
            public decimal PortfolioValueAfter { get; set; }
            public decimal CashBefore { get; set; }
            public decimal CashAfter { get; set; }
            public decimal EntryValue { get; set; }
            public decimal Fees { get; set; }
            public decimal NetPnl { get; set; }
            public int SelectionCount { get; set; }
            public string SelectedSymbols { get; set; }
        }

        private sealed class AllocationRow
        {
            public DateTime TradeDate { get; set; }
            public string Phase { get; set; }
            public string Asset { get; set; }
            public decimal Value { get; set; }
            public decimal Weight { get; set; }
            public decimal PortfolioValue { get; set; }
            public decimal CashValue { get; set; }
            public decimal NetPnl { get; set; }
        }

        private sealed class ActionPlanRow
        {
            public DateTime TradeDate { get; set; }
            public string Symbol { get; set; }
            public decimal Score { get; set; }
            public string BuyTime { get; set; }
            public decimal BuyPrice { get; set; }
            public int BuyQuantity { get; set; }
            public decimal BuyValue { get; set; }
            public decimal OpenWeight { get; set; }
            public string SellTime { get; set; }
            public decimal SellPrice { get; set; }
            public int SellQuantity { get; set; }
            public decimal Fees { get; set; }
            public decimal NetPnl { get; set; }
            public decimal PortfolioValueBefore { get; set; }
            public decimal PortfolioValueAfter { get; set; }
            public decimal CashBefore { get; set; }
            public decimal CashAfter { get; set; }
            public string SelectedSymbols { get; set; }
        }

        private static bool HasRequiredLocalData(Symbol underlyingSymbol)
        {
            var customSymbol = QuantConnect.Symbol.CreateBase(typeof(AShareEtfT0FeatureData), underlyingSymbol, underlyingSymbol.ID.Market);
            var featurePath = AShareEtfT0FeatureData.ResolveSourcePath(customSymbol);
            var pricePath = Path.Combine(Globals.DataFolder, "equity", underlyingSymbol.ID.Market.ToLowerInvariant(), "daily", $"{underlyingSymbol.Value}.zip");
            return File.Exists(featurePath) && File.Exists(pricePath);
        }

        private static bool IsMoneyMarketTicker(string ticker)
        {
            return ticker.StartsWith("511", StringComparison.Ordinal)
                || ticker == "159001"
                || ticker == "159003"
                || ticker == "159005";
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            return int.TryParse(GetParameter(name), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)
                ? value
                : defaultValue;
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            return decimal.TryParse(GetParameter(name), NumberStyles.Any, CultureInfo.InvariantCulture, out var value)
                ? value
                : defaultValue;
        }

        private decimal? GetOptionalDecimalParameter(string name)
        {
            return decimal.TryParse(GetParameter(name), NumberStyles.Any, CultureInfo.InvariantCulture, out var value)
                ? value
                : (decimal?)null;
        }

        private bool GetBoolParameter(string name, bool defaultValue)
        {
            return bool.TryParse(GetParameter(name), out var value)
                ? value
                : defaultValue;
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            return DateTime.TryParseExact(GetParameter(name), "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var value)
                ? value
                : defaultValue;
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0)
            {
                return 0m;
            }

            var middle = values.Count / 2;
            if (values.Count % 2 == 1)
            {
                return values[middle];
            }

            return (values[middle - 1] + values[middle]) / 2m;
        }
    }
}
