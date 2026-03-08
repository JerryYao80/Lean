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
        private readonly List<decimal> _realizedReturns = new List<decimal>();
        private readonly List<DailySummaryRow> _dailySummaries = new List<DailySummaryRow>();
        private readonly List<AllocationRow> _allocationRows = new List<AllocationRow>();
        private readonly List<ActionPlanRow> _actionPlanRows = new List<ActionPlanRow>();
        private Symbol _anchorSymbol;
        private int _topN;
        private decimal _minScoreSpread;
        private decimal? _maxAverageGapAbs;
        private bool _riskRegimeFilterEnabled;
        private decimal? _riskRegimeMediumMomentumThreshold;
        private decimal? _riskRegimeMediumVolatilityThreshold;
        private decimal _riskRegimeMediumExposureScale;
        private int _riskRegimeMediumTopN;
        private decimal _riskRegimeMediumScoreSpreadAdd;
        private decimal _riskRegimeMediumLiquidityQuantile;
        private decimal? _riskRegimeMomentumThreshold;
        private decimal? _riskRegimeVolatilityThreshold;
        private decimal _riskRegimeHighExposureScale;
        private int _riskRegimeHighTopN;
        private decimal _riskRegimeHighScoreSpreadAdd;
        private decimal _riskRegimeHighLiquidityQuantile;
        private bool _portfolioVolTargetEnabled;
        private decimal _portfolioVolTargetDailyVol;
        private int _portfolioVolTargetLookback;
        private int _portfolioVolTargetMinObservations;
        private decimal _portfolioVolTargetFloorScale;
        private decimal _portfolioVolTargetCapScale;
        private decimal _targetPortfolioExposure;
        private bool _excludeMoneyMarketEtfs;
        private string _executionMode;
        private string _tradeReportPath;
        private string _dailySummaryPath;
        private string _allocationPath;
        private string _actionPlanPath;
        private DateTime _lastTradeDate;
        private AShareEtfT0FeatureSignalSettings _signalSettings;

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2024, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            _topN = GetIntParameter("top-n", 2);
            _minScoreSpread = GetDecimalParameter("min-score-spread", 0.7m);
            _maxAverageGapAbs = GetOptionalDecimalParameter("max-average-gap-abs");
            _riskRegimeFilterEnabled = GetBoolParameter("risk-regime-filter-enabled", false);
            _riskRegimeMediumMomentumThreshold = GetOptionalDecimalParameter("risk-regime-medium-momentum-threshold");
            _riskRegimeMediumVolatilityThreshold = GetOptionalDecimalParameter("risk-regime-medium-volatility-threshold");
            _riskRegimeMediumExposureScale = GetDecimalParameter("risk-regime-medium-exposure-scale", 1.0m);
            _riskRegimeMediumTopN = GetIntParameter("risk-regime-medium-top-n", _topN);
            _riskRegimeMediumScoreSpreadAdd = GetDecimalParameter("risk-regime-medium-score-spread-add", 0m);
            _riskRegimeMediumLiquidityQuantile = GetDecimalParameter("risk-regime-medium-liquidity-quantile", 0m);
            _riskRegimeMomentumThreshold = GetOptionalDecimalParameter("risk-regime-momentum-threshold");
            _riskRegimeVolatilityThreshold = GetOptionalDecimalParameter("risk-regime-volatility-threshold");
            _riskRegimeHighExposureScale = GetDecimalParameter("risk-regime-high-exposure-scale", 0.0m);
            _riskRegimeHighTopN = GetIntParameter("risk-regime-high-top-n", _topN);
            _riskRegimeHighScoreSpreadAdd = GetDecimalParameter("risk-regime-high-score-spread-add", 0m);
            _riskRegimeHighLiquidityQuantile = GetDecimalParameter("risk-regime-high-liquidity-quantile", 0m);
            _portfolioVolTargetEnabled = GetBoolParameter("portfolio-vol-target-enabled", false);
            _portfolioVolTargetDailyVol = GetDecimalParameter("portfolio-vol-target-daily-vol", 0.012m);
            _portfolioVolTargetLookback = GetIntParameter("portfolio-vol-target-lookback", 40);
            _portfolioVolTargetMinObservations = GetIntParameter("portfolio-vol-target-min-observations", 10);
            _portfolioVolTargetFloorScale = GetDecimalParameter("portfolio-vol-target-floor-scale", 0.5m);
            _portfolioVolTargetCapScale = GetDecimalParameter("portfolio-vol-target-cap-scale", 1.0m);
            _signalSettings = new AShareEtfT0FeatureSignalSettings
            {
                NavPremiumZ20Weight = GetDecimalParameter("conditional-signal-nav-premium-z20-weight", 0m),
                NavPremiumZ20Orthogonalize = GetBoolParameter("conditional-signal-nav-premium-z20-orthogonalize", false),
                NavPremiumZ20NormalScale = GetDecimalParameter("conditional-signal-nav-premium-z20-normal-scale", 1.0m),
                NavPremiumZ20MediumScale = GetDecimalParameter("conditional-signal-nav-premium-z20-medium-scale", 0.5m),
                NavPremiumZ20HighScale = GetDecimalParameter("conditional-signal-nav-premium-z20-high-scale", 0.0m)
            };
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

            Log($"AShareEtfT0FeatureIntradayAlgorithm initialized with {_featureToUnderlying.Count} feature subscriptions");
            if (_signalSettings.NavPremiumZ20Weight != 0m)
            {
                Log($"Conditional NavPremiumZ20 overlay enabled weight={_signalSettings.NavPremiumZ20Weight:F4} orthogonalize={_signalSettings.NavPremiumZ20Orthogonalize} scales={_signalSettings.NavPremiumZ20NormalScale:F2}/{_signalSettings.NavPremiumZ20MediumScale:F2}/{_signalSettings.NavPremiumZ20HighScale:F2}");
            }
            if (_portfolioVolTargetEnabled)
            {
                Log($"Portfolio vol target enabled target={_portfolioVolTargetDailyVol:F4} lookback={_portfolioVolTargetLookback} minObs={_portfolioVolTargetMinObservations} floor/cap={_portfolioVolTargetFloorScale:F2}/{_portfolioVolTargetCapScale:F2}");
            }
            if (LiveMode)
            {
                Log("Synthetic execution mode active: this workflow records advisory trades and allocations but does not submit brokerage orders.");
            }
        }

        public override void OnData(Slice slice)
        {
            DateTime? sessionDate = null;
            foreach (var pair in slice.Get<AShareEtfT0FeatureData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                if (_featureToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _latestFeaturesByUnderlying[underlying] = pair.Value;
                    sessionDate ??= pair.Value.EndTime.Date;
                }
            }

            if (sessionDate.HasValue)
            {
                TradeSession(sessionDate.Value);
            }
        }

        private void TradeSession(DateTime sessionDate)
        {
            var currentSessionDate = sessionDate.Date;
            if (_lastTradeDate == currentSessionDate)
            {
                return;
            }
            _lastTradeDate = currentSessionDate;
            var sessionDateText = currentSessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

            var dailyFeatures = _latestFeaturesByUnderlying
                .Where(pair => IsFreshFeatureSnapshot(pair.Value, currentSessionDate))
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            if (dailyFeatures.Count == 0)
            {
                var staleFeatureCount = _latestFeaturesByUnderlying.Count(pair => pair.Value != null && pair.Value.HasSignals);
                if (staleFeatureCount > 0)
                {
                    Log($"{sessionDateText} skip trading: no fresh feature snapshots for current session; stale snapshot count={staleFeatureCount}");
                }
                else
                {
                    Log($"{sessionDateText} no feature snapshots available");
                }
                return;
            }

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
            var dailyExposureScale = 1m;
            var riskRegimeBucket = "normal";
            var effectiveTopN = _topN;
            var effectiveScoreSpreadThreshold = _minScoreSpread;
            var liquidityQuantile = 0m;
            if (_riskRegimeFilterEnabled)
            {
                if (_riskRegimeMomentumThreshold.HasValue && _riskRegimeVolatilityThreshold.HasValue &&
                    marketSignalMomentum5Mean <= _riskRegimeMomentumThreshold.Value &&
                    marketSignalVolatility10Mean >= _riskRegimeVolatilityThreshold.Value)
                {
                    dailyExposureScale = _riskRegimeHighExposureScale;
                    riskRegimeBucket = "high";
                    effectiveTopN = _riskRegimeHighTopN > 0 ? Math.Min(_topN, _riskRegimeHighTopN) : _topN;
                    effectiveScoreSpreadThreshold = _minScoreSpread + _riskRegimeHighScoreSpreadAdd;
                    liquidityQuantile = _riskRegimeHighLiquidityQuantile;
                }
                else if (_riskRegimeMediumMomentumThreshold.HasValue && _riskRegimeMediumVolatilityThreshold.HasValue &&
                    marketSignalMomentum5Mean <= _riskRegimeMediumMomentumThreshold.Value &&
                    marketSignalVolatility10Mean >= _riskRegimeMediumVolatilityThreshold.Value)
                {
                    dailyExposureScale = _riskRegimeMediumExposureScale;
                    riskRegimeBucket = "medium";
                    effectiveTopN = _riskRegimeMediumTopN > 0 ? Math.Min(_topN, _riskRegimeMediumTopN) : _topN;
                    effectiveScoreSpreadThreshold = _minScoreSpread + _riskRegimeMediumScoreSpreadAdd;
                    liquidityQuantile = _riskRegimeMediumLiquidityQuantile;
                }
            }

            var scores = AShareEtfT0FeatureSignalModel.ComputeScores(dailyFeatures, _signalSettings, riskRegimeBucket);
            var orderedScores = scores.Values.OrderBy(value => value).ToList();
            var scoreSpread = orderedScores.Count > 0 ? orderedScores[^1] - GetMedian(orderedScores) : 0m;

            if (effectiveScoreSpreadThreshold > 0 && scoreSpread < effectiveScoreSpreadThreshold)
            {
                Log($"{sessionDateText} skip trading: score spread {scoreSpread:F4} below threshold {effectiveScoreSpreadThreshold:F4}");
                return;
            }

            var rankedCandidates = scores
                .OrderByDescending(pair => pair.Value)
                .Where(pair => Securities.ContainsKey(pair.Key) && Securities[pair.Key].Price > 0)
                .ToList();

            if (liquidityQuantile > 0m)
            {
                var liquidities = rankedCandidates
                    .Where(pair => dailyFeatures.ContainsKey(pair.Key) && dailyFeatures[pair.Key].SignalLiquidity5.HasValue)
                    .Select(pair => dailyFeatures[pair.Key].SignalLiquidity5.Value)
                    .ToList();
                if (liquidities.Count > 0)
                {
                    var liquidityCutoff = GetQuantile(liquidities, liquidityQuantile);
                    rankedCandidates = rankedCandidates
                        .Where(pair => dailyFeatures.ContainsKey(pair.Key)
                            && dailyFeatures[pair.Key].SignalLiquidity5.HasValue
                            && dailyFeatures[pair.Key].SignalLiquidity5.Value >= liquidityCutoff)
                        .ToList();
                }
            }

            var ranked = rankedCandidates
                .Take(Math.Max(1, effectiveTopN))
                .ToList();
            if (ranked.Count == 0)
            {
                Log($"{sessionDateText} no tradable symbols after ranking");
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
                    Log($"{sessionDateText} skip trading: avg gap abs {averageGapAbs:F4} above threshold {_maxAverageGapAbs.Value:F4}");
                    return;
                }
            }

            var portfolioVolTargetScale = _portfolioVolTargetEnabled
                ? ComputePortfolioVolTargetScale(_realizedReturns, _portfolioVolTargetDailyVol, _portfolioVolTargetLookback, _portfolioVolTargetMinObservations, _portfolioVolTargetFloorScale, _portfolioVolTargetCapScale)
                : 1m;
            dailyExposureScale = Math.Min(1m, dailyExposureScale * portfolioVolTargetScale);

            if (dailyExposureScale <= 0m)
            {
                Log($"{sessionDateText} skip trading: risk regime scaling bucket={riskRegimeBucket} scale={dailyExposureScale:F2} mom5={marketSignalMomentum5Mean:F4} vol10={marketSignalVolatility10Mean:F4}");
                return;
            }

            if (dailyExposureScale < 1m || effectiveTopN < _topN || effectiveScoreSpreadThreshold > _minScoreSpread || liquidityQuantile > 0m || portfolioVolTargetScale < 1m)
            {
                Log($"{sessionDateText} risk regime controls bucket={riskRegimeBucket} scale={dailyExposureScale:F2} volTarget={portfolioVolTargetScale:F2} topN={effectiveTopN} spread={effectiveScoreSpreadThreshold:F4} liquidityQ={liquidityQuantile:F2} mom5={marketSignalMomentum5Mean:F4} vol10={marketSignalVolatility10Mean:F4}");
            }

            var availableCash = Portfolio.CashBook[AccountCurrency].Amount;

            var targetBudget = Math.Min(Portfolio.TotalPortfolioValue, availableCash) * _targetPortfolioExposure * dailyExposureScale;
            var remainingBudget = targetBudget;
            var remainingSlots = ranked.Count;
            var portfolioValueBefore = Portfolio.TotalPortfolioValue;
            var cashBefore = Portfolio.CashBook[AccountCurrency].Amount;
            var totalDailyPnl = 0m;
            var totalDailyFees = 0m;
            var totalEntryValue = 0m;
            var tradeDate = currentSessionDate;
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
            var dailyReturn = portfolioValueBefore > 0m ? totalDailyPnl / portfolioValueBefore : 0m;
            if (dayTrades.Count > 0)
            {
                _realizedReturns.Add(dailyReturn);
            }
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
                DailyReturn = dailyReturn,
                RiskRegimeBucket = riskRegimeBucket,
                ExposureScale = dailyExposureScale,
                PortfolioVolTargetScale = portfolioVolTargetScale,
                EffectiveTopN = effectiveTopN,
                EffectiveScoreSpreadThreshold = effectiveScoreSpreadThreshold,
                LiquidityQuantile = liquidityQuantile,
                SelectionCount = dayTrades.Count,
                SelectedSymbols = string.Join("|", dayTrades.Select(trade => trade.Symbol))
            };
            _dailySummaries.Add(dailySummary);
            RecordActionPlanRows(dailySummary, dayTrades);

            if (selections.Count > 0)
            {
                Log($"{sessionDateText} selected {string.Join("; ", selections)} day_pnl={totalDailyPnl:F2} fees={totalDailyFees:F2} nav={portfolioValueAfter:F2}");
            }
            else
            {
                Log($"{sessionDateText} selected none day_pnl={totalDailyPnl:F2} nav={portfolioValueAfter:F2}");
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


        private static decimal GetQuantile(IReadOnlyList<decimal> values, decimal quantile)
        {
            if (values == null || values.Count == 0)
            {
                return 0m;
            }

            var ordered = values.OrderBy(value => value).ToList();
            if (quantile <= 0m)
            {
                return ordered[0];
            }

            if (quantile >= 1m)
            {
                return ordered[^1];
            }

            var position = (ordered.Count - 1) * (double)quantile;
            var lowerIndex = (int)Math.Floor(position);
            var upperIndex = (int)Math.Ceiling(position);
            if (lowerIndex == upperIndex)
            {
                return ordered[lowerIndex];
            }

            var weight = (decimal)(position - lowerIndex);
            return ordered[lowerIndex] + (ordered[upperIndex] - ordered[lowerIndex]) * weight;
        }

        public static bool IsFreshFeatureSnapshot(AShareEtfT0FeatureData feature, DateTime sessionDate)
        {
            return feature != null
                && feature.HasSignals
                && feature.EndTime.Date == sessionDate.Date;
        }

        public static decimal ComputePortfolioVolTargetScale(
            IReadOnlyList<decimal> realizedReturns,
            decimal dailyVolTarget,
            int lookback,
            int minObservations,
            decimal floorScale,
            decimal capScale)
        {
            if (realizedReturns == null || realizedReturns.Count == 0 || dailyVolTarget <= 0m)
            {
                return 1m;
            }

            var effectiveLookback = lookback > 0 ? lookback : realizedReturns.Count;
            var trailing = realizedReturns
                .Skip(Math.Max(0, realizedReturns.Count - effectiveLookback))
                .Select(value => (double)value)
                .ToList();
            if (trailing.Count < Math.Max(1, minObservations))
            {
                return 1m;
            }

            var mean = trailing.Average();
            var variance = trailing.Select(value => (value - mean) * (value - mean)).Average();
            var std = Math.Sqrt(variance);
            if (double.IsNaN(std) || double.IsInfinity(std) || std <= 0d)
            {
                return 1m;
            }

            var rawScale = (decimal)((double)dailyVolTarget / std);
            return ClampDecimal(rawScale, floorScale, capScale);
        }

        private static decimal ClampDecimal(decimal value, decimal lower, decimal upper)
        {
            var lowerBound = Math.Min(lower, upper);
            var upperBound = Math.Max(lower, upper);
            return Math.Max(lowerBound, Math.Min(value, upperBound));
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
            yield return "trade_date,portfolio_value_before,portfolio_value_after,cash_before,cash_after,entry_value,fees,net_pnl,daily_return,risk_regime_bucket,exposure_scale,portfolio_vol_target_scale,effective_top_n,effective_score_spread_threshold,liquidity_quantile,selection_count,selected_symbols";
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
                    FormatDecimal(row.DailyReturn),
                    row.RiskRegimeBucket,
                    FormatDecimal(row.ExposureScale),
                    FormatDecimal(row.PortfolioVolTargetScale),
                    row.EffectiveTopN.ToString(CultureInfo.InvariantCulture),
                    FormatDecimal(row.EffectiveScoreSpreadThreshold),
                    FormatDecimal(row.LiquidityQuantile),
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
            public decimal DailyReturn { get; set; }
            public string RiskRegimeBucket { get; set; }
            public decimal ExposureScale { get; set; }
            public decimal PortfolioVolTargetScale { get; set; }
            public int EffectiveTopN { get; set; }
            public decimal EffectiveScoreSpreadThreshold { get; set; }
            public decimal LiquidityQuantile { get; set; }
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
            return File.Exists(featurePath)
                && File.Exists(pricePath)
                && File.ReadLines(featurePath).Skip(1).Take(25).Count() >= 25;
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
