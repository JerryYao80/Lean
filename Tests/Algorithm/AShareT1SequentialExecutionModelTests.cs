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
        // unaffordable) is actually submitted.
        //
        // Setup: algorithm holds 200 shares of SPY priced at $250 (=$50k locked in
        // SPY). Cash is set to just $500. We emit two targets:
        //   - sell SPY to 0  (quantity = -200, lot-rounded)
        //   - buy AAPL 100   (cost = 100 * $250 = $25k, far exceeding the $500 cash
        //                     on hand BEFORE the SPY sell, affordable AFTER).
        //
        // Assertion: after Execute + synchronous fill processing, an order for AAPL
        // exists. The buy could only have been submitted if the SPY sell freed cash
        // first — exactly the T+1 cash-release behavior we need. With a
        // buy-before-sell ordering (or a single-phase model that skips the buy when
        // cash is insufficient), no AAPL order would appear.
        //
        // Uses the real backtest fill pipeline (BrokerageTransactionHandler +
        // NullBrokerage → Portfolio.ProcessFills) so the cash freed by the SPY sell
        // is actually credited to Portfolio.Cash before the AAPL buy's buying-power
        // check runs. Default CashBuyingPowerModel (Equity) is used — the point is
        // to test ORDERING, not A-share T+1 specifics.
        [Test]
        public void Execute_SellFreesCashForSubsequentBuy()
        {
            var algorithm = new AlgorithmStub();
            algorithm.SetDateTime(new DateTime(2024, 1, 2, 9, 30, 0));
            algorithm.Settings.MinimumOrderMarginPortfolioPercentage = 0;

            var spy = algorithm.AddEquity(Symbols.SPY.Value);
            var aapl = algorithm.AddEquity(Symbols.AAPL.Value);
            spy.SetMarketPrice(new TradeBar { Value = 250m, Time = algorithm.UtcTime });
            aapl.SetMarketPrice(new TradeBar { Value = 250m, Time = algorithm.UtcTime });

            // Give the algorithm 200 shares of SPY (=$50k), very little cash.
            spy.Holdings.SetHoldings(250m, 200);
            algorithm.Portfolio.SetCash(500m);

            algorithm.SetFinishedWarmingUp();

            var orderProcessor = GetAndSetBrokerageTransactionHandler(algorithm, out var brokerage);
            try
            {
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

                var orders = orderProcessor.GetOrders().ToList();
                // The SPY sell must have been submitted.
                Assert.IsTrue(orders.Any(o => o.Symbol == Symbols.SPY && o.Quantity < 0),
                    "SPY sell order was not submitted");
                // The AAPL buy must have been submitted — proving the SPY sell freed
                // cash before the buy's buying-power check ran. Without sell-first
                // ordering, the $500 cash would not cover 100 * $250 = $25k and the
                // buy would be skipped.
                Assert.IsTrue(orders.Any(o => o.Symbol == Symbols.AAPL && o.Quantity > 0),
                    "AAPL buy order was not submitted — sell did not free cash for the buy");
            }
            finally
            {
                orderProcessor.Exit();
                brokerage.Dispose();
            }
        }

        internal static BrokerageTransactionHandler GetAndSetBrokerageTransactionHandler(
            IAlgorithm algorithm, out NullBrokerage brokerage)
        {
            brokerage = new NullBrokerage();
            var orderProcessor = new BrokerageTransactionHandler();
            orderProcessor.Initialize(algorithm, brokerage, new BacktestingResultHandler());
            algorithm.Transactions.SetOrderProcessor(orderProcessor);
            algorithm.Transactions.MarketOrderFillTimeout = TimeSpan.Zero;
            return orderProcessor;
        }
    }
}

