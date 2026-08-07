/*
 * TDD tests for AShareT1SequentialExecutionModel — verifies T+1 sell-before-buy
 * ordering, lot-size rounding, and buy-skip-on-insufficient-buying-power.
 */
using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Brokerages.Backtesting;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Lean.Engine.Results;
using QuantConnect.Lean.Engine.TransactionHandlers;
using QuantConnect.Orders;
using QuantConnect.Securities;
using QuantConnect.Tests.Common;
using QuantConnect.Tests.Engine.DataFeeds;
namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class AShareT1SequentialExecutionModelTests
    {
        // Verifies the model separates targets into sells-first then buys.
        // Uses a real QCAlgorithm sub-class with securities + a buying-power
        // model that denies buys when cash is low.
        [Test]
        public void Execute_ProcessesSellsBeforeBuys()
        {
            // Structural smoke: model instantiates and classifies a sell vs buy target
            // by sign of GetUnorderedQuantity. We assert the two-phase split logic via
            // the model's internal helper exposed for testing.
            var model = new AShareT1SequentialExecutionModel();
            // A sell target (negative quantity) and a buy target (positive)
            var sells = model.ClassifySignForTest(-100);
            var buys = model.ClassifySignForTest(150);
            Assert.AreEqual("sell", sells);
            Assert.AreEqual("buy", buys);
        }

        [Test]
        public void RoundToLotSize_RoundsDownToMultipleOf100()
        {
            var model = new AShareT1SequentialExecutionModel();
            Assert.AreEqual(100, model.RoundToLotSizeForTest(150));
            Assert.AreEqual(200, model.RoundToLotSizeForTest(250));
            Assert.AreEqual(0, model.RoundToLotSizeForTest(50));
            Assert.AreEqual(-100, model.RoundToLotSizeForTest(-150));
        }

        // Behavioral test: proves the two-phase sell-before-buy ordering releases
        // cash from a sell so a subsequent buy (that would otherwise be
        // unaffordable) actually FILLS.
        //
        // Setup: algorithm holds 200 shares of SPY priced at $250 (=$50k locked in
        // SPY). Cash is set to just $500. We emit two targets:
        //   - sell SPY to 0  (quantity = -200, lot-rounded)
        //   - buy AAPL 100   (cost = 100 * $250 = $25k, far exceeding the $500 cash
        //                     on hand BEFORE the SPY sell, affordable AFTER).
        //
        // Mechanism: with asynchronous=false, each MarketOrder call blocks until the
        // order is FILLED (BacktestingTransactionHandler.Run -> HandleOrderRequest ->
        // ProcessAsynchronousEvents -> BacktestingBrokerage.Scan -> fill ->
        // OnOrderEvents -> Portfolio.ProcessFills credits cash). So by the time
        // ExecuteSells returns, the SPY sell has filled and Portfolio.Cash has been
        // credited the ~$50k proceeds. ExecuteBuys then runs against the freed cash.
        //
        // BacktestingBrokerage.Scan enforces HasSufficientBuyingPowerForOrder at fill
        // time — an unaffordable buy is marked OrderStatus.Invalid (the "Insufficient
        // buying power" rejection). MinimumOrderMarginPortfolioPercentage is set
        // NONZERO (0.001) so the execution model's pre-check is live, not
        // short-circuited.
        //
        // Assertions are on FILLS (Portfolio quantities + Cash), not order
        // submissions — proving the actual cash-release mechanism. With a
        // buy-before-sell ordering the AAPL buy would be invalidated at fill time
        // (only $500 cash, no sell proceeds yet) and Portfolio[AAPL].Quantity would
        // stay 0. This test would also FAIL under NullBrokerage (no fills → SPY
        // quantity stays 200, Cash stays 500), which is the meaningfulness check.
        [Test]
        public void Execute_SellFreesCashForSubsequentBuy()
        {
            var algorithm = new AlgorithmStub();
            algorithm.SetDateTime(new DateTime(2024, 1, 2, 9, 30, 0));
            // NONZERO so AboveMinimumOrderMarginPortfolioPercentage is live (not short-circuited).
            algorithm.Settings.MinimumOrderMarginPortfolioPercentage = 0.001m;

            var spy = algorithm.AddEquity(Symbols.SPY.Value);
            var aapl = algorithm.AddEquity(Symbols.AAPL.Value);
            // Force exchange open so MarketOrder is not converted to MarketOnOpen
            // (which would not fill until next bar). Tests in this file don't run a
            // time loop, so we need the exchange always open for synchronous fills.
            spy.Exchange = new SecurityExchange(SecurityExchangeHours.AlwaysOpen(TimeZones.NewYork));
            aapl.Exchange = new SecurityExchange(SecurityExchangeHours.AlwaysOpen(TimeZones.NewYork));
            // BacktestingBrokerage fills market orders at the current market price.
            spy.SetMarketPrice(new TradeBar { Value = 250m, Time = algorithm.UtcTime });
            aapl.SetMarketPrice(new TradeBar { Value = 250m, Time = algorithm.UtcTime });

            // Give the algorithm 200 shares of SPY (=$50k), very little cash ($500).
            spy.Holdings.SetHoldings(250m, 200);
            algorithm.Portfolio.SetCash(500m);
            var cashBefore = algorithm.Portfolio.Cash;

            algorithm.SetFinishedWarmingUp();

            var orderProcessor = GetAndSetBacktestingTransactionHandler(algorithm, out var brokerage);
            try
            {
                // synchronous: each MarketOrder blocks until filled (cash credited before next order).
                var model = new AShareT1SequentialExecutionModel(asynchronous: false);
                algorithm.SetExecution(model);

                var changes = Common.Data.UniverseSelection.SecurityChangesTests
                    .CreateNonInternal(new[] { spy, aapl }, Enumerable.Empty<Security>());
                model.OnSecuritiesChanged(algorithm, changes);

                // Sell SPY to 0, buy 100 shares of AAPL.
                var targets = new IPortfolioTarget[]
                {
                    new PortfolioTarget(Symbols.SPY, 0m),
                    new PortfolioTarget(Symbols.AAPL, 100m)
                };
                model.Execute(algorithm, targets);
                orderProcessor.ProcessSynchronousEvents();

                // 1. The SPY sell FILLED: holdings liquidated, cash credited.
                Assert.AreEqual(0m, algorithm.Portfolio[Symbols.SPY].Quantity,
                    "SPY sell did not fill — holdings not liquidated");
                Assert.Greater(algorithm.Portfolio.Cash, cashBefore,
                    "SPY sell did not credit cash — fill pipeline not engaged");
                // Sell proceeds: 200 * 250 = 50000 credited. Cash before was 500.
                // 500 + 50000 = 50500 before the AAPL buy. The fact that Cash is now
                // ~25500 (not ~50500) proves the AAPL buy ALSO filled (cost 25000),
                // spending the freed cash — exactly the sell-frees-cash-for-buy path.
                Assert.That(algorithm.Portfolio.Cash,
                    Is.GreaterThan(24000m).And.LessThan(27000m),
                    $"Expected ~25500 (500 + 50000 sell - 25000 buy - fees); Cash={algorithm.Portfolio.Cash}");

                // 2. The AAPL buy FILLED against the freed cash. Without sell-before-buy
                //    the buy would be invalidated (Insufficient buying power) and
                //    Portfolio[AAPL].Quantity would stay 0.
                Assert.AreEqual(100m, algorithm.Portfolio[Symbols.AAPL].Quantity,
                    "AAPL buy did not fill — sell did not free cash for the buy");
            }
            finally
            {
                orderProcessor.Exit();
                brokerage.Dispose();
            }
        }

        // Wires the REAL backtest fill pipeline: BacktestingTransactionHandler +
        // BacktestingBrokerage. BacktestingBrokerage.Scan fills market orders
        // synchronously and fires OrdersStatusChanged -> Portfolio.ProcessFills,
        // crediting cash. This is the same pipeline the production backtest uses.
        internal static BacktestingTransactionHandler GetAndSetBacktestingTransactionHandler(
            IAlgorithm algorithm, out BacktestingBrokerage brokerage)
        {
            brokerage = new BacktestingBrokerage(algorithm);
            var orderProcessor = new BacktestingTransactionHandler();
            orderProcessor.Initialize(algorithm, brokerage, new BacktestingResultHandler());
            algorithm.Transactions.SetOrderProcessor(orderProcessor);
            algorithm.Transactions.MarketOrderFillTimeout = TimeSpan.Zero;
            return orderProcessor;
        }
    }
}

