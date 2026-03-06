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
        private int _rebalanceDays = 5;
        private int _lookbackPeriod = 20;
        private int _topN = 3;

        /// <summary>
        /// Initialize the algorithm
        /// </summary>
        public override void Initialize()
        {
            SetStartDate(2024, 1, 1);
            SetEndDate(2024, 12, 31);

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
                    var market = metadata.Market == "SSE" ? Market.SSE : Market.SZSE;
                    var equity = AddEquity(ticker, Resolution.Daily, market);

                    // Set custom models for A-share ETFs
                    equity.FeeModel = new AShareETFFeeModel();
                    equity.FillModel = new AShareETFFillModel();
                    equity.BuyingPowerModel = new AShareETFBuyingPowerModel();

                    _etfSymbols.Add(equity.Symbol);
                    Log($"Added T+0 ETF: {ticker} ({metadata.Name}) on {metadata.Market}, Price: {equity.Price:F4}");
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
            foreach (var symbol in _etfSymbols)
            {
                var history = History(symbol, _lookbackPeriod, Resolution.Daily);
                if (history != null && history.Count() >= _lookbackPeriod)
                {
                    var bars = history.ToList();
                    var oldPrice = bars.First().Close;
                    var newPrice = bars.Last().Close;
                    _momentum[symbol] = (newPrice - oldPrice) / oldPrice;
                    Log($"Momentum for {symbol.Value}: {_momentum[symbol]:P2} (from {oldPrice:F2} to {newPrice:F2})");
                }
                else
                {
                    Log($"Insufficient history for {symbol.Value}: {history?.Count() ?? 0} bars (need {_lookbackPeriod})");
                }
            }

            if (_momentum.Count < _etfSymbols.Count)
            {
                Log($"Not enough momentum data for rebalancing: {_momentum.Count}/{_etfSymbols.Count} ETFs");
                return;
            }

            // Sort by momentum and select top N
            var topETFs = _momentum
                .OrderByDescending(kvp => kvp.Value)
                .Take(_topN)
                .Select(kvp => kvp.Key)
                .ToList();

            Log($"Rebalancing: Top {_topN} ETFs by momentum: {string.Join(", ", topETFs.Select(s => s.Value))}");

            // Calculate target weights
            var targetWeight = 1.0m / _topN;

            // Liquidate positions not in top N
            foreach (var holding in Portfolio.Values.Where(h => h.Invested))
            {
                if (!topETFs.Contains(holding.Symbol))
                {
                    Log($"Liquidating {holding.Symbol.Value}: {holding.Quantity} shares");
                    Liquidate(holding.Symbol);
                }
            }

            // Allocate to top N ETFs
            foreach (var symbol in topETFs)
            {
                var currentWeight = Portfolio[symbol].HoldingsValue / Portfolio.TotalPortfolioValue;
                var targetValue = Portfolio.TotalPortfolioValue * targetWeight;
                var currentValue = Portfolio[symbol].HoldingsValue;
                var deltaValue = targetValue - currentValue;

                if (Math.Abs(deltaValue) > Portfolio.TotalPortfolioValue * 0.01m) // 1% threshold
                {
                    var price = Securities[symbol].Price;
                    if (price > 0)
                    {
                        // Use decimal for calculation to avoid overflow
                        var targetQuantityDecimal = deltaValue / price;
                        // Round to lot size (100 shares)
                        var lots = Math.Floor(Math.Abs(targetQuantityDecimal) / 100m);
                        var targetQuantity = (int)(lots * 100m * Math.Sign(targetQuantityDecimal));

                        if (targetQuantity != 0)
                        {
                            Log($"Ordering {symbol.Value}: {targetQuantity} shares at {price:F4} (delta: {deltaValue:F2}, target weight: {targetWeight:P2})");
                            MarketOrder(symbol, targetQuantity);
                        }
                    }
                }
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
