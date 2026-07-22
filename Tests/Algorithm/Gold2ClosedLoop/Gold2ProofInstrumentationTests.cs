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
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.CSharp.Gold2ClosedLoop;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Algorithm;

namespace QuantConnect.Tests.Algorithm.Gold2ClosedLoop
{
    /// <summary>
    /// Instrumentation tests for the Gold2 closed-loop proof-only subclass
    /// (<see cref="Gold2ClosedLoopProofStrategy"/>) and the tracing execution
    /// decorator (<see cref="TracingExecutionModel"/>). These tests assert the
    /// structural invariants required by plan Task 6 (inheritance, delegation,
    /// and trace-event emission) and the behavior of the decorator in isolation
    /// using a fake inner execution model so no live LEAN engine is required.
    /// </summary>
    /// <remarks>
    /// The headline reflection-based inheritance/delegation assertion runs here
    /// (compiles within the Tests project). The behavioral tests use a fake inner
    /// <see cref="ExecutionModel"/> test double so they are deterministic and do
    /// not require a running algorithm engine. The same assertions are mirrored
    /// in the standalone harness under /tmp/gold2_proof_task6_harness for an
    /// end-to-end run without the broken mature Gold2 test files.
    /// </remarks>
    [TestFixture]
    public class Gold2ProofInstrumentationTests
    {
        private string _tempDir;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(),
                "gold2_proof_task6_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_tempDir))
            {
                try { Directory.Delete(_tempDir, recursive: true); }
                catch (IOException) { /* best-effort */ }
                catch (UnauthorizedAccessException) { /* best-effort */ }
            }
        }

        private string NewTracePath() => Path.Combine(_tempDir,
            "trace_" + Guid.NewGuid().ToString("N") + ".jsonl");

        // ----------------------------------------------------------------
        // Reflection-based structural invariants (plan Task 6 verbatim).
        // ----------------------------------------------------------------

        /// <summary>
        /// The proof strategy MUST inherit from the mature Gold2 strategy, and the
        /// decorator MUST expose the wrapped inner model via a private instance field
        /// named <c>_inner</c>. This is the verbatim plan assertion.
        /// </summary>
        [Test]
        public void ProofStrategyInheritsMatureGold2()
        {
            Assert.That(typeof(Gold2ClosedLoopProofStrategy).BaseType,
                Is.EqualTo(typeof(Gold2BetaVolTargetStrategy)));
            Assert.That(typeof(TracingExecutionModel).GetField("_inner",
                BindingFlags.NonPublic | BindingFlags.Instance), Is.Not.Null);
        }

        /// <summary>
        /// The decorator MUST derive from <see cref="ExecutionModel"/> so the
        /// BrokerageTransactionHandler routes order events to it via the engine's
        /// <c>_executionModel.OnOrderEvent</c> call (Engine/TransactionHandlers/
        /// BrokerageTransactionHandler.cs:1369). This is why the decorator can
        /// emit FILL + HOLDINGS_SNAPSHOT from OnOrderEvent without the strategy
        /// overriding OnOrderEvent.
        /// </summary>
        [Test]
        public void TracingExecutionModelInheritsExecutionModel()
        {
            Assert.That(typeof(TracingExecutionModel).BaseType,
                Is.EqualTo(typeof(ExecutionModel)));
        }

        // ----------------------------------------------------------------
        // Behavioral tests using a fake inner execution model.
        // These do NOT instantiate a full QCAlgorithm; they verify the
        // decorator's trace emission + reconciliation deterministically.
        // ----------------------------------------------------------------

        /// <summary>
        /// The decorator MUST emit a DECISION event BEFORE delegating to the
        /// inner model. The DECISION payload records the portfolio targets
        /// (symbol + desired quantity) that were received.
        /// </summary>
        [Test]
        public void DecoratorEmitsDecisionBeforeDelegating()
        {
            var path = NewTracePath();
            using var sink = new FormalJsonlTraceSink(path);
            var fake = new FakeExecutionModel();
            var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var decorator = new FakeDiscoveringDecorator(fake, sink,
                new[] { new OrderIntentRecord(42, symbol, 200m) });
            var targets = new IPortfolioTarget[] { new PortfolioTarget(symbol, 100m) };

            decorator.Execute(null, targets);

            // The sink buffers writes (AutoFlush=false); FlushAndReconcile flushes them to disk
            // so File.ReadAllLines can read the JSONL back. Reconciliation passes (DECISION
            // only is a valid prefix; ORDER_INTENT now fires from OnOrderEvent).
            sink.FlushAndReconcile();
            var lines = File.ReadAllLines(path);
            // DECISION is emitted before delegating; ORDER_INTENT is now emitted from
            // OnOrderEvent at first sighting, so after Execute alone we expect 1 line.
            Assert.GreaterOrEqual(lines.Length, 1, "expected at least DECISION event");
            var ev = JObject.Parse(lines[0]);
            Assert.AreEqual("1", ev["schema_version"]?.ToString());
            Assert.AreEqual(1, (long)ev["sequence"]);
            Assert.AreEqual("DECISION", ev["event_type"].ToString());
            Assert.IsNotNull(ev["event_time_utc"], "DECISION must carry event_time_utc");
            Assert.AreEqual(1, fake.ExecuteCalls, "inner.Execute must be delegated to exactly once");
            CollectionAssert.AreEqual(new[] { 100m }, fake.LastTargets);
            var payload = ev["payload"] as JObject;
            Assert.IsNotNull(payload);
            var targetArr = payload["targets"] as JArray;
            Assert.IsNotNull(targetArr);
            Assert.AreEqual(1, targetArr.Count);
            Assert.AreEqual("518880", targetArr[0]["symbol"].ToString());
            Assert.AreEqual(100m, (decimal)targetArr[0]["quantity"]);
        }

        /// <summary>
        /// After delegating (which emits only DECISION), the decorator MUST emit an
        /// ORDER_INTENT event for each newly-created order the FIRST time it sights the
        /// order id - on the <c>Submitted</c> order event that always precedes the
        /// <c>Filled</c> event. This test drives <see cref="TracingExecutionModel.Execute"/>
        /// then a synthetic <c>Submitted</c> event to emit the intent, capturing the native
        /// <c>orderId</c> so the reconciler can correlate FILL to ORDER_INTENT.
        /// </summary>
        [Test]
        public void DecoratorEmitsOrderIntentAtFirstSighting()
        {
            var path = NewTracePath();
            using var sink = new FormalJsonlTraceSink(path);
            var fake = new FakeExecutionModel();
            var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var decorator = new FakeDiscoveringDecorator(fake, sink,
                new[] { new OrderIntentRecord(42, symbol, 200m) });

            // Execute emits DECISION only (ORDER_INTENT now fires from OnOrderEvent).
            decorator.Execute(null, new IPortfolioTarget[] { new PortfolioTarget(symbol, 200m) });

            // The engine fires a Submitted event before the Filled event; the decorator
            // emits ORDER_INTENT on this first sighting of orderId=42.
            decorator.OnOrderEvent(null, new OrderEvent(
                orderId: 42, symbol: symbol,
                utcTime: new DateTime(2026, 7, 14, 9, 30, 0, DateTimeKind.Utc),
                status: OrderStatus.Submitted, direction: OrderDirection.Buy,
                fillPrice: 0m, fillQuantity: 0m,
                orderFee: new OrderFee(new CashAmount(0m, Currencies.CNY))));

            // Flush buffered writes to disk so File.ReadAllLines can read them back.
            sink.FlushAndReconcile();
            var lines = File.ReadAllLines(path);
            // sequence 1 = DECISION, sequence 2 = ORDER_INTENT
            Assert.AreEqual(2, lines.Length,
                "expected DECISION + ORDER_INTENT, got:\n" + string.Join("\n", lines));
            var orderIntent = JObject.Parse(lines[1]);
            Assert.AreEqual(2, (long)orderIntent["sequence"]);
            Assert.AreEqual("ORDER_INTENT", orderIntent["event_type"].ToString());
            var payload = orderIntent["payload"] as JObject;
            Assert.IsNotNull(payload);
            Assert.AreEqual(42, (long)payload["orderId"],
                "ORDER_INTENT must carry the native orderId for reconciler correlation");
            Assert.AreEqual(200m, (decimal)payload["quantity"],
                "ORDER_INTENT must carry the LEAN-native intended quantity");
        }

        /// <summary>
        /// On a Filled OrderEvent, the decorator MUST emit a FILL event and an
        /// immediate post-fill HOLDINGS_SNAPSHOT for the same orderId. This is
        /// the core closed-loop evidence: LEAN-native fill + LEAN-native holdings
        /// read directly, never reconstructed.
        /// </summary>
        [Test]
        public void DecoratorEmitsFillThenHoldingsSnapshotOnOrderEvent()
        {
            var path = NewTracePath();
            using var sink = new FormalJsonlTraceSink(path);
            var fake = new FakeExecutionModel();
            var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var decorator = new FakeDiscoveringDecorator(fake, sink,
                new[] { new OrderIntentRecord(7, symbol, 100m) },
                new HoldingsSnapshotPayload(7, "518880", 100m, 5.432m, 5.5m, 900000m, 1000000m));

            // Execute emits DECISION only; ORDER_INTENT now fires from OnOrderEvent
            // at first sighting of orderId=7 (on the Filled event below).
            decorator.Execute(null, new IPortfolioTarget[] { new PortfolioTarget(symbol, 100m) });

            // The engine delivers a Submitted event before the Filled event; the
            // decorator emits ORDER_INTENT on this first sighting of orderId=7, so the
            // full chain (DECISION, ORDER_INTENT, FILL, HOLDINGS_SNAPSHOT) is produced.
            decorator.OnOrderEvent(null, new OrderEvent(
                orderId: 7, symbol: symbol,
                utcTime: new DateTime(2026, 7, 14, 9, 29, 0, DateTimeKind.Utc),
                status: OrderStatus.Submitted, direction: OrderDirection.Buy,
                fillPrice: 0m, fillQuantity: 0m,
                orderFee: new OrderFee(new CashAmount(0m, Currencies.CNY))));

            // Synthesize a fill event the way the engine would deliver it.
            var fillEvent = new OrderEvent(
                orderId: 7,
                symbol: symbol,
                utcTime: new DateTime(2026, 7, 14, 9, 30, 0, DateTimeKind.Utc),
                status: OrderStatus.Filled,
                direction: OrderDirection.Buy,
                fillPrice: 5.432m,
                fillQuantity: 100m,
                orderFee: new OrderFee(new CashAmount(5.00m, Currencies.CNY)),
                message: "test fill");
            decorator.OnOrderEvent(null, fillEvent);

            // Flush buffered writes to disk so File.ReadAllLines can read them back.
            sink.FlushAndReconcile();
            var lines = File.ReadAllLines(path);
            // DECISION(1), ORDER_INTENT(2), FILL(3), HOLDINGS_SNAPSHOT(4)
            Assert.AreEqual(4, lines.Length,
                "Expected DECISION, ORDER_INTENT, FILL, HOLDINGS_SNAPSHOT; got: " +
                string.Join("\n", lines));
            var orderIntent = JObject.Parse(lines[1]);
            Assert.AreEqual("ORDER_INTENT", orderIntent["event_type"].ToString());
            var intentPayload = orderIntent["payload"] as JObject;
            Assert.IsNotNull(intentPayload);
            Assert.AreEqual(7, (long)intentPayload["orderId"],
                "ORDER_INTENT must carry the same orderId as the FILL/SNAPSHOT");
            Assert.AreEqual(100m, (decimal)intentPayload["quantity"],
                "ORDER_INTENT must carry the LEAN-native intended quantity");
            var fill = JObject.Parse(lines[2]);
            Assert.AreEqual("FILL", fill["event_type"].ToString());
            var fillPayload = fill["payload"] as JObject;
            Assert.IsNotNull(fillPayload);
            Assert.AreEqual(7, (long)fillPayload["orderId"]);
            Assert.AreEqual(5.432m, (decimal)fillPayload["fillPrice"]);
            Assert.AreEqual(100m, (decimal)fillPayload["fillQuantity"]);
            Assert.AreEqual(5.00m, (decimal)fillPayload["fee"]);

            var snapshot = JObject.Parse(lines[3]);
            Assert.AreEqual("HOLDINGS_SNAPSHOT", snapshot["event_type"].ToString());
            var snapPayload = snapshot["payload"] as JObject;
            Assert.IsNotNull(snapPayload);
            Assert.AreEqual(7, (long)snapPayload["orderId"],
                "HOLDINGS_SNAPSHOT must carry the same orderId for FILL to SNAPSHOT correlation");
            Assert.AreEqual(100m, (decimal)snapPayload["quantity"]);
            Assert.AreEqual(5.5m, (decimal)snapPayload["price"]);
            Assert.AreEqual(1000000m, (decimal)snapPayload["totalPortfolioValue"]);
        }

        /// <summary>
        /// The reconciler MUST pass when the full chain is well-formed:
        /// DECISION, ORDER_INTENT, FILL, HOLDINGS_SNAPSHOT, all correlated
        /// by native orderId. This is the proof gate.
        /// </summary>
        [Test]
        public void ReconcilerPassesOnFullChain()
        {
            var path = NewTracePath();
            using var sink = new FormalJsonlTraceSink(path);
            var fake = new FakeExecutionModel();
            var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var decorator = new FakeDiscoveringDecorator(fake, sink,
                new[] { new OrderIntentRecord(99, symbol, 100m) },
                new HoldingsSnapshotPayload(99, "518880", 100m, 1m, 1m, 1m, 1m));
            // Execute emits DECISION only; ORDER_INTENT now fires from OnOrderEvent
            // at first sighting of orderId=99 (on the Filled event below).
            decorator.Execute(null, new IPortfolioTarget[] { new PortfolioTarget(symbol, 100m) });
            // First sighting of orderId=99 (its fill event) emits ORDER_INTENT, then
            // FILL, then the correlated HOLDINGS_SNAPSHOT: a valid full chain.
            decorator.OnOrderEvent(null, new OrderEvent(
                orderId: 99, symbol: symbol,
                utcTime: DateTime.UtcNow, status: OrderStatus.Filled,
                direction: OrderDirection.Buy, fillPrice: 1m, fillQuantity: 100m,
                orderFee: new OrderFee(new CashAmount(1m, Currencies.CNY))));

            // FlushAndReconcile must NOT throw.
            Assert.DoesNotThrow(() => sink.FlushAndReconcile());
        }

        /// <summary>
        /// A FILL with no preceding ORDER_INTENT (e.g. orderId never recorded)
        /// MUST fail reconciliation. This proves the decorator's ORDER_INTENT
        /// emission is load-bearing for the proof gate.
        /// </summary>
        [Test]
        public void ReconcilerFailsOnFillWithoutOrderIntent()
        {
            var path = NewTracePath();
            using var sink = new FormalJsonlTraceSink(path);
            var fake = new FakeExecutionModel();
            var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            // FakeDiscoveringDecorator with empty intents -> BuildOrderIntentRecord
            // returns null for orderId 555 -> no ORDER_INTENT emitted.
            var decorator = new FakeDiscoveringDecorator(fake, sink,
                Array.Empty<OrderIntentRecord>(),
                new HoldingsSnapshotPayload(555, "518880", 100m, 1m, 1m, 1m, 1m));
            decorator.Execute(null, new IPortfolioTarget[] { new PortfolioTarget(symbol, 100m) });
            // Synthesize a fill for an orderId that had no ORDER_INTENT.
            decorator.OnOrderEvent(null, new OrderEvent(
                orderId: 555, symbol: symbol,
                utcTime: DateTime.UtcNow, status: OrderStatus.Filled,
                direction: OrderDirection.Buy, fillPrice: 1m, fillQuantity: 100m,
                orderFee: new OrderFee(new CashAmount(1m, Currencies.CNY))));

            Assert.Throws<FormalTraceReconciliationException>(
                () => sink.FlushAndReconcile());
        }

        // ----------------------------------------------------------------
        // Failure-propagation semantics.
        // ----------------------------------------------------------------

        /// <summary>
        /// If reconciliation throws, the exception MUST propagate (FAILED_EVIDENCE_CAPTURE).
        /// The decorator/sink must never swallow. This mirrors the OnEndOfAlgorithm gate:
        /// a FILL with no following HOLDINGS_SNAPSHOT must cause FlushAndReconcile to
        /// throw, and the throw must surface to the caller unchanged.
        /// </summary>
        [Test]
        public void FailurePropagatesFromFlushAndReconcile()
        {
            var path = NewTracePath();
            var sink = new FormalJsonlTraceSink(path);
            try
            {
                var fake = new FakeExecutionModel();
                var symbol = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
                // ORDER_INTENT for orderId=1 is emitted, but the snapshot is suppressed
                // by the SuppressSnapshot flag -> no HOLDINGS_SNAPSHOT follows the FILL.
                var decorator = new FakeDiscoveringDecorator(fake, sink,
                    new[] { new OrderIntentRecord(1, symbol, 100m) },
                    snapshotPayload: new HoldingsSnapshotPayload(1, "518880", 100m, 1m, 1m, 1m, 1m),
                    suppressSnapshot: true);
                decorator.Execute(null, new IPortfolioTarget[] { new PortfolioTarget(symbol, 100m) });
                // Fill for orderId=1; snapshot suppressed -> missing snapshot.
                decorator.OnOrderEvent(null, new OrderEvent(
                    orderId: 1, symbol: symbol,
                    utcTime: DateTime.UtcNow, status: OrderStatus.Filled,
                    direction: OrderDirection.Buy, fillPrice: 1m, fillQuantity: 100m,
                    orderFee: new OrderFee(new CashAmount(1m, Currencies.CNY))));

                // Missing HOLDINGS_SNAPSHOT -> reconciler throws and propagates.
                Assert.Throws<FormalTraceReconciliationException>(
                    () => sink.FlushAndReconcile());
            }
            finally
            {
                sink.Dispose();
            }
        }

        // ----------------------------------------------------------------
        // Test doubles.
        // ----------------------------------------------------------------

        /// <summary>
        /// A simple fake inner execution model. It records the targets it received
        /// so tests can assert delegation. It does NOT call algorithm.MarketOrder.
        /// </summary>
        private sealed class FakeExecutionModel : ExecutionModel
        {
            public int ExecuteCalls;
            public readonly List<decimal> LastTargets = new();

            public FakeExecutionModel() : base(asynchronous: false)
            {
            }

            public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
            {
                ExecuteCalls++;
                LastTargets.Clear();
                LastTargets.AddRange((targets ?? Array.Empty<IPortfolioTarget>()).Select(t => t.Quantity));
            }

            public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
            {
            }
        }

        /// <summary>
        /// A subclass of <see cref="TracingExecutionModel"/> that overrides the
        /// LEAN-state-reading seams to return deterministic, test-supplied order
        /// intents and holdings snapshots. This lets behavioral tests run without a
        /// live algorithm/engine.
        /// <para>
        /// <see cref="BuildOrderIntentRecord"/> maps the first-sighted
        /// <c>orderId</c> to a test-supplied <see cref="OrderIntentRecord"/>. The
        /// <see cref="_suppressIntent"/> flag lets the failure-propagation intent
        /// test omit ORDER_INTENT (via <see cref="ShouldEmitOrderIntent"/>) so the
        /// reconciler (correctly) fails the subsequent FILL; the
        /// <see cref="_suppressSnapshot"/> flag lets the failure-propagation snapshot
        /// test omit the post-fill HOLDINGS_SNAPSHOT so the reconciler (correctly) fails.
        /// </para>
        /// </summary>
        private sealed class FakeDiscoveringDecorator : TracingExecutionModel
        {
            private readonly Dictionary<int, OrderIntentRecord> _intentsByOrderId;
            private readonly HoldingsSnapshotPayload _snapshotPayload;
            private readonly bool _suppressIntent;
            private readonly bool _suppressSnapshot;

            public FakeDiscoveringDecorator(IExecutionModel inner, IFormalTraceSink sink,
                IReadOnlyList<OrderIntentRecord> newOrders,
                HoldingsSnapshotPayload snapshotPayload = null,
                bool suppressSnapshot = false,
                bool suppressIntent = false)
                : base(inner, sink, algorithm: null)
            {
                _intentsByOrderId = (newOrders ?? Array.Empty<OrderIntentRecord>())
                    .ToDictionary(r => r.OrderId);
                _snapshotPayload = snapshotPayload;
                _suppressSnapshot = suppressSnapshot;
                _suppressIntent = suppressIntent;
            }

            protected override OrderIntentRecord BuildOrderIntentRecord(
                QCAlgorithm algorithm, OrderEvent orderEvent)
            {
                // Map the first-sighted native orderId to the test-supplied record.
                // Returns null when the order was not configured (so a later FILL for
                // that order has no ORDER_INTENT and the reconciler fails, exactly
                // what ReconcilerFailsOnFillWithoutOrderIntent asserts).
                _intentsByOrderId.TryGetValue(orderEvent.OrderId, out var record);
                return record;
            }

            protected override bool ShouldEmitOrderIntent(OrderEvent orderEvent)
                => !_suppressIntent;

            protected override HoldingsSnapshotPayload BuildHoldingsSnapshotPayload(
                QCAlgorithm algorithm, Symbol symbol, int orderId)
            {
                if (_snapshotPayload == null)
                {
                    return new HoldingsSnapshotPayload(orderId, symbol.Value, 0m, 0m, 0m, 0m, 0m);
                }
                return _snapshotPayload;
            }

            /// <summary>
            /// When <see cref="_suppressSnapshot"/> is true, suppress emission of the
            /// post-fill HOLDINGS_SNAPSHOT so the reconciler (correctly) detects a
            /// missing snapshot. Otherwise delegate to the base behavior (FILL + snapshot).
            /// </summary>
            protected override bool ShouldEmitHoldingsSnapshot(OrderEvent orderEvent)
                => !_suppressSnapshot;
        }

    }
}
