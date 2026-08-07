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
    /// Simple momentum-based ETF strategy for A-Share T+0 ETFs ONLY
    /// This strategy explicitly filters for T+0 ETFs (same-day buy/sell)
    /// and excludes T+1 ETFs (next-day sell only)
    /// </summary>
    public class ETFMomentumStrategy : QCAlgorithm
    {
        private List<Symbol> _etfSymbols;
        private Dictionary<Symbol, decimal> _momentum;
        private readonly Dictionary<Symbol, decimal> _latestHistoryClose = new Dictionary<Symbol, decimal>();
        private int _rebalanceDays = 5;
        private int _lookbackPeriod = 20;
        private int _topN = 3;
        private bool _excludeMoneyMarketETFs = true; // Exclude low-volatility money market ETFs

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

            // Get all T+0 ETFs from registry (excludes T+1 ETFs)
            var t0ETFs = AShareETFRegistry.GetT0ETFs();

            Log($"Found {t0ETFs.Count} T+0 ETFs in registry");

            // Add only T+0 tradable ETFs to the algorithm
            _etfSymbols = new List<Symbol>();
            foreach (var ticker in t0ETFs)
            {
                var metadata = AShareETFRegistry.GetMetadata(ticker);
                if (metadata != null)
                {
                    // Skip money market ETFs if configured (they have very low volatility)
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
                    var initialPrice = equity.Price > 0 ? equity.Price.ToString("F4") : "pending first bar";
                    Log($"Added T+0 ETF: {ticker} ({metadata.Name}) on {metadata.Market}, Initial Price: {initialPrice}");
                }
            }

            _momentum = new Dictionary<Symbol, decimal>();

            // Schedule rebalancing
            Schedule.On(DateRules.EveryDay(), TimeRules.AfterMarketOpen(Market.SSE, 30), Rebalance);

            Log($"ETFMomentumStrategy initialized with {_etfSymbols.Count} T+0 ETFs (T+1 ETFs excluded)");
        }

        /// <summary>
        /// OnData event handler
        /// </summary>
        public override void OnData(Slice data)
        {
            // Data processing happens here, but momentum calculation is done in Rebalance()
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
                    Log($"Momentum for {symbol.Value}: {_momentum[symbol]:P2} (from {oldPrice:F2} to {newPrice:F2})");
                }
                else
                {
                    Log($"Insufficient history for {symbol.Value}: {history?.Count() ?? 0} bars (need {_lookbackPeriod})");
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

            Log($"Rebalancing: Top {_topN} ETFs by momentum: {string.Join(", ", topETFs.Select(s => s.Value))}");

            if (Transactions.GetOpenOrders().Count > 0)
            {
                Log($"Skipping rebalance: waiting for {Transactions.GetOpenOrders().Count} open order(s) to fill");
                return;
            }

            // Keep a small cash buffer for fees and because daily MarketOrders are converted to MOO orders
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
                        Log($"Skipping {symbol.Value}: current price {price:F4} inconsistent with history close {historyClose:F4} (ratio: {priceRatio:F2})");
                        continue;
                    }
                }

                var targetQuantityDecimal = deltaValue / price;
                var lots = Math.Floor(Math.Abs(targetQuantityDecimal) / 100m);
                var roundedShares = lots * 100m;

                if (roundedShares > int.MaxValue)
                {
                    Log($"Skipping {symbol.Value}: calculated quantity {roundedShares:F0} exceeds Int32.MaxValue");
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
                Log($"Ordering {adjustment.Symbol.Value}: {adjustment.Quantity} shares at {adjustment.Price:F4} (delta: {adjustment.DeltaValue:F2}, target weight: {targetWeight:P2})");
                MarketOrder(adjustment.Symbol, adjustment.Quantity);
            }

            if (sellAdjustments.Count > 0)
            {
                Log("Skipping buy allocations until sell rebalancing orders fill");
                return;
            }

            foreach (var adjustment in buyAdjustments)
            {
                Log($"Ordering {adjustment.Symbol.Value}: {adjustment.Quantity} shares at {adjustment.Price:F4} (delta: {adjustment.DeltaValue:F2}, target weight: {targetWeight:P2})");
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
        }
    }
}
