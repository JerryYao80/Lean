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
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Optimized momentum-based ETF strategy with parameter optimization support
    /// </summary>
    public class ETFMomentumStrategyOptimized : QCAlgorithm
    {
        private List<Symbol> _etfSymbols;
        private Dictionary<Symbol, decimal> _momentum;
        private readonly Dictionary<Symbol, decimal> _latestHistoryClose = new Dictionary<Symbol, decimal>();

        // Optimizable parameters
        private int _rebalanceDays = 5;
        private int _lookbackPeriod = 20;
        private int _topN = 5;  // Increased from 3 to 5 for better diversification
        private bool _excludeMoneyMarketETFs = true;
        private bool _useOnlyLiquidETFs = true;  // Only use ETFs with complete data

        // Risk management
        private decimal _stopLossPercent = -0.08m;  // -8% stop loss
        private decimal _takeProfitPercent = 0.15m;  // +15% take profit
        private bool _enableRiskManagement = true;

        /// <summary>
        /// Initialize the algorithm
        /// </summary>
        public override void Initialize()
        {
            SetStartDate(2018, 1, 1);
            SetEndDate(2025, 12, 31);

            // Set account currency to CNY for A-share trading
            SetAccountCurrency("CNY");
            SetCash(1000000); // 1M CNY

            // Explicitly add CNY to cash book to avoid margin model errors
            Portfolio.CashBook.Add("CNY", 1000000, 1.0m);

            // Disable benchmark since we're trading Chinese ETFs
            SetBenchmark(x => 0);

            // Define liquid ETFs with complete historical data
            var liquidETFs = new List<string>
            {
                // Major index ETFs
                "510050", "510300", "510500",  // SSE 50/300/500
                "159915", "159919", "159949",  // ChiNext ETFs

                // Gold ETFs
                "518880", "159934", "159937",  // Gold ETFs

                // Optional: Money market ETFs (low volatility)
                // "511880", "511990"
            };

            // Get all T+0 ETFs from registry
            var t0ETFs = _useOnlyLiquidETFs ? liquidETFs : AShareETFRegistry.GetT0ETFs();

            Log($"Strategy Configuration:");
            Log($"  Lookback Period: {_lookbackPeriod} days");
            Log($"  Rebalance Frequency: {_rebalanceDays} days");
            Log($"  Top N Holdings: {_topN}");
            Log($"  Risk Management: {(_enableRiskManagement ? "Enabled" : "Disabled")}");
            Log($"  Stop Loss: {_stopLossPercent:P2}");
            Log($"  Take Profit: {_takeProfitPercent:P2}");
            Log($"Found {t0ETFs.Count} T+0 ETFs to trade");

            // Add ETFs to the algorithm
            _etfSymbols = new List<Symbol>();
            foreach (var ticker in t0ETFs)
            {
                var metadata = AShareETFRegistry.GetMetadata(ticker);
                if (metadata != null)
                {
                    // Skip money market ETFs if configured
                    if (_excludeMoneyMarketETFs && (ticker.StartsWith("511") || ticker.StartsWith("159001") || ticker.StartsWith("159003") || ticker.StartsWith("159005")))
                    {
                        Log($"Skipping money market ETF: {ticker} ({metadata.Name})");
                        continue;
                    }

                    var market = metadata.Market == "SSE" ? Market.SSE : Market.SZSE;
                    var equity = AddEquity(ticker, Resolution.Daily, market);

                    // Set custom models for A-share ETFs
                    equity.FeeModel = new AShareETFFeeModel();
                    equity.FillModel = new AShareETFFillModel();
                    equity.BuyingPowerModel = new AShareETFBuyingPowerModel();
                    equity.Session.Size = 2;

                    _etfSymbols.Add(equity.Symbol);
                    Log($"Added T+0 ETF: {ticker} ({metadata.Name}) on {metadata.Market}");
                }
            }

            _momentum = new Dictionary<Symbol, decimal>();

            // Schedule rebalancing
            Schedule.On(DateRules.EveryDay(), TimeRules.AfterMarketOpen(Market.SSE, 30), Rebalance);

            // Schedule risk management check
            if (_enableRiskManagement)
            {
                Schedule.On(DateRules.EveryDay(), TimeRules.AfterMarketOpen(Market.SSE, 60), CheckRiskManagement);
            }

            Log($"ETFMomentumStrategyOptimized initialized with {_etfSymbols.Count} T+0 ETFs");
        }

        /// <summary>
        /// OnData event handler
        /// </summary>
        public override void OnData(Slice data)
        {
            // Data processing happens here, but momentum calculation is done in Rebalance()
        }

        /// <summary>
        /// Check risk management rules (stop loss / take profit)
        /// </summary>
        private void CheckRiskManagement()
        {
            if (!_enableRiskManagement)
                return;

            foreach (var holding in Portfolio.Values.Where(h => h.Invested))
            {
                var unrealizedProfitPercent = holding.UnrealizedProfitPercent;

                // Stop loss
                if (unrealizedProfitPercent <= _stopLossPercent)
                {
                    Log($"STOP LOSS triggered for {holding.Symbol.Value}: {unrealizedProfitPercent:P2}");
                    Liquidate(holding.Symbol);
                }
                // Take profit (sell 50%)
                else if (unrealizedProfitPercent >= _takeProfitPercent)
                {
                    var currentQuantity = holding.Quantity;
                    var sellQuantity = (int)(currentQuantity * 0.5m / 100) * 100; // Round to lot size
                    if (sellQuantity >= 100)
                    {
                        Log($"TAKE PROFIT triggered for {holding.Symbol.Value}: {unrealizedProfitPercent:P2}, selling 50%");
                        MarketOrder(holding.Symbol, -sellQuantity);
                    }
                }
            }
        }

        /// <summary>
        /// Rebalance portfolio based on momentum
        /// </summary>
        private void Rebalance()
        {
            // Calculate momentum for all ETFs
            _momentum.Clear();
            _latestHistoryClose.Clear();

            foreach (var symbol in _etfSymbols)
            {
                var history = History(symbol, _lookbackPeriod, Resolution.Daily);
                if (history != null && history.Count() >= _lookbackPeriod)
                {
                    var bars = history.ToList();
                    var oldPrice = bars.First().Close;
                    var newPrice = bars.Last().Close;
                    _latestHistoryClose[symbol] = newPrice;
                    _momentum[symbol] = (newPrice - oldPrice) / oldPrice;
                }
            }

            if (_momentum.Count < _topN)
            {
                Log($"Not enough momentum data for rebalancing: {_momentum.Count}/{_etfSymbols.Count} ETFs (need at least {_topN})");
                return;
            }

            // Sort by momentum and select top N
            var topETFs = _momentum
                .OrderByDescending(kvp => kvp.Value)
                .Take(_topN)
                .Select(kvp => kvp.Key)
                .ToList();

            Log($"Rebalancing: Top {_topN} ETFs by momentum:");
            foreach (var symbol in topETFs)
            {
                Log($"  {symbol.Value}: {_momentum[symbol]:P2}");
            }

            if (Transactions.GetOpenOrders().Count > 0)
            {
                Log($"Skipping rebalance: waiting for {Transactions.GetOpenOrders().Count} open order(s) to fill");
                return;
            }

            // Keep a small cash buffer
            var targetWeight = 0.95m / _topN;

            var liquidationOrdersPlaced = false;

            // Liquidate positions not in top N
            foreach (var holding in Portfolio.Values.Where(h => h.Invested))
            {
                if (!topETFs.Contains(holding.Symbol))
                {
                    Log($"Liquidating {holding.Symbol.Value}: {holding.Quantity} shares");
                    Liquidate(holding.Symbol);
                    liquidationOrdersPlaced = true;
                }
            }

            if (liquidationOrdersPlaced)
            {
                Log("Skipping new allocations until liquidation orders fill");
                return;
            }

            var sellAdjustments = new List<(Symbol Symbol, int Quantity, decimal Price, decimal DeltaValue)>();
            var buyAdjustments = new List<(Symbol Symbol, int Quantity, decimal Price, decimal DeltaValue)>();

            // Allocate to top N ETFs
            foreach (var symbol in topETFs)
            {
                var currentWeight = Portfolio[symbol].HoldingsValue / Portfolio.TotalPortfolioValue;
                var targetValue = Portfolio.TotalPortfolioValue * targetWeight;
                var currentValue = Portfolio[symbol].HoldingsValue;
                var deltaValue = targetValue - currentValue;

                if (Math.Abs(deltaValue) <= Portfolio.TotalPortfolioValue * 0.01m)
                {
                    continue;
                }

                var price = Securities[symbol].Price;
                if (price <= 0)
                {
                    Log($"Skipping {symbol.Value}: current price unavailable ({price:F4})");
                    continue;
                }

                if (_latestHistoryClose.TryGetValue(symbol, out var historyClose) && historyClose > 0)
                {
                    var priceRatio = historyClose / price;
                    if (priceRatio > 10m || priceRatio < 0.1m)
                    {
                        Log($"Skipping {symbol.Value}: price inconsistency detected");
                        continue;
                    }
                }

                var targetQuantityDecimal = deltaValue / price;
                var lots = Math.Floor(Math.Abs(targetQuantityDecimal) / 100m);
                var roundedShares = lots * 100m;

                if (roundedShares > int.MaxValue)
                {
                    continue;
                }

                var targetQuantity = (int)(roundedShares * Math.Sign(targetQuantityDecimal));
                if (targetQuantity == 0)
                {
                    continue;
                }

                var adjustment = (symbol, targetQuantity, price, deltaValue);
                if (targetQuantity < 0)
                {
                    sellAdjustments.Add(adjustment);
                }
                else
                {
                    buyAdjustments.Add(adjustment);
                }
            }

            foreach (var adjustment in sellAdjustments)
            {
                Log($"Ordering {adjustment.Symbol.Value}: {adjustment.Quantity} shares at {adjustment.Price:F4}");
                MarketOrder(adjustment.Symbol, adjustment.Quantity);
            }

            if (sellAdjustments.Count > 0)
            {
                Log("Skipping buy allocations until sell orders fill");
                return;
            }

            foreach (var adjustment in buyAdjustments)
            {
                Log($"Ordering {adjustment.Symbol.Value}: {adjustment.Quantity} shares at {adjustment.Price:F4}");
                MarketOrder(adjustment.Symbol, adjustment.Quantity);
            }
        }

        /// <summary>
        /// Order event handler
        /// </summary>
        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"Order filled: {orderEvent.Symbol.Value} {orderEvent.Direction} {orderEvent.FillQuantity} @ {orderEvent.FillPrice}");
            }
            else if (orderEvent.Status == OrderStatus.Invalid)
            {
                Log($"Order invalid: {orderEvent.Symbol.Value} - {orderEvent.Message}");
            }
        }

        /// <summary>
        /// End of algorithm handler
        /// </summary>
        public override void OnEndOfAlgorithm()
        {
            Log($"Algorithm finished. Final portfolio value: {Portfolio.TotalPortfolioValue:C}");
            Log($"Total return: {((Portfolio.TotalPortfolioValue / 1000000m) - 1):P2}");

            // Log performance summary
            Log($"\nPerformance Summary:");
            Log($"  Initial Capital: ¥1,000,000");
            Log($"  Final Value: {Portfolio.TotalPortfolioValue:C}");
            Log($"  Net Profit: {Portfolio.TotalPortfolioValue - 1000000:C}");
            Log($"  Return: {((Portfolio.TotalPortfolioValue / 1000000m) - 1):P2}");
        }
    }
}
