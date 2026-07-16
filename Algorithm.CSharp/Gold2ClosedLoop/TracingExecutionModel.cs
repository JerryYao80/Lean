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
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Gold2ClosedLoop
{
    /// <summary>
    /// A single newly-discovered native order captured by the tracing decorator
    /// after delegating to the inner execution model. Carries only the LEAN-native
    /// fields required for the ORDER_INTENT trace event. Pure DTO; it never
    /// reproduces order sizing, lot rounding or margin checks.
    /// </summary>
    public sealed class OrderIntentRecord
    {
        /// <summary>The LEAN-native order id assigned by the transaction handler.</summary>
        public int OrderId { get; }

        /// <summary>The security symbol the order was submitted for.</summary>
        public Symbol Symbol { get; }

        /// <summary>The requested order quantity (signed).</summary>
        public decimal Quantity { get; }

        public OrderIntentRecord(int orderId, Symbol symbol, decimal quantity)
        {
            OrderId = orderId;
            Symbol = symbol;
            Quantity = quantity;
        }
    }

    /// <summary>
    /// A LEAN-native holdings snapshot read immediately after a fill. Every field is
    /// read directly from <see cref="Security.Holdings"/> / <see cref="Security.Price"/>
    /// / <see cref="SecurityPortfolioManager"/>; the proof never reconstructs these
    /// values. Captured so the FILL to HOLDINGS_SNAPSHOT correlation invariant can be
    /// validated by <see cref="FormalTraceReconciler"/>.
    /// </summary>
    public sealed class HoldingsSnapshotPayload
    {
        /// <summary>The native order id this snapshot closes (correlation key).</summary>
        public int OrderId { get; }

        /// <summary>The security ticker.</summary>
        public string Symbol { get; }

        /// <summary>LEAN-native holdings quantity.</summary>
        public decimal Quantity { get; }

        /// <summary>LEAN-native average entry price.</summary>
        public decimal AveragePrice { get; }

        /// <summary>LEAN-native current market price.</summary>
        public decimal Price { get; }

        /// <summary>LEAN-native portfolio cash.</summary>
        public decimal Cash { get; }

        /// <summary>LEAN-native total portfolio value.</summary>
        public decimal TotalPortfolioValue { get; }

        public HoldingsSnapshotPayload(int orderId, string symbol, decimal quantity,
            decimal averagePrice, decimal price, decimal cash, decimal totalPortfolioValue)
        {
            OrderId = orderId;
            Symbol = symbol;
            Quantity = quantity;
            AveragePrice = averagePrice;
            Price = price;
            Cash = cash;
            TotalPortfolioValue = totalPortfolioValue;
        }
    }

    /// <summary>
    /// Proof-only execution decorator that wraps the mature
    /// <see cref="QuantConnect.Algorithm.Framework.Execution.AShareLotSizeExecutionModel"/>
    /// and emits the formal Gold2 closed-loop trace events defined by plan Task 5.
    /// </summary>
    /// <remarks>
    /// <para><b>Routing decision (engine-routes-to-ExecutionModel).</b> The LEAN engine
    /// captures <c>qcAlgorithm.Execution</c> in <c>BrokerageTransactionHandler.Initialize</c>
    /// (Engine/TransactionHandlers/BrokerageTransactionHandler.cs:214) and then invokes
    /// <c>_executionModel.OnOrderEvent(_qcAlgorithmIntance, orderEvent)</c> on every fill
    /// (Engine/TransactionHandlers/BrokerageTransactionHandler.cs:1369) BEFORE calling
    /// <c>_algorithm.OnOrderEvent</c> (line 1374). <c>Transactions.Initialize</c> is called
    /// after <c>algorithm.Initialize()</c> (Engine/Engine.cs:332 vs
    /// Engine/Setup/BacktestingSetupHandler.cs:194), so the proof strategy's
    /// <see cref="Gold2ClosedLoopProofStrategy.Initialize"/> - which installs this
    /// decorator as <c>Execution</c> - has already run. Therefore the decorator's
    /// <see cref="OnOrderEvent"/> is the engine's direct hook for fills: it emits FILL
    /// and an immediate post-fill HOLDINGS_SNAPSHOT there. The strategy does NOT need
    /// to override <see cref="QCAlgorithm.OnOrderEvent"/>.</para>
    /// <para><b>Non-reproduction contract.</b> The decorator delegates Execute exactly
    /// once to the inner model and OnOrderEvent exactly once to the inner model. It
    /// never reproduces order sizing, lot rounding, margin checks, fill pricing, fee
    /// calculation, or holdings/cash/TPV accounting: every traced value is read
    /// directly from LEAN-native <see cref="OrderEvent"/> and
    /// <see cref="Security.Holdings"/> instances.</para>
    /// <para><b>Sequence discipline.</b> A single 1-based counter (<see cref="_sequence"/>)
    /// supplies every sequence under <see cref="_traceLock"/>; the write to the strict
    /// sink happens inside the same critical section so write order always matches
    /// sequence order (the sink rejects gaps/duplicates).</para>
    /// <para><b>Testability seams.</b>
    /// <see cref="GetKnownOrderIds"/>, <see cref="DiscoverNewOrders"/> and
    /// <see cref="BuildHoldingsSnapshotPayload"/> are protected virtual so a test double
    /// can supply deterministic order ids and holdings without instantiating a full
    /// LEAN engine. The production paths read real LEAN-native state.</para>
    /// </remarks>
    public class TracingExecutionModel : ExecutionModel
    {
        private readonly IExecutionModel _inner;
        private readonly IFormalTraceSink _sink;
        private readonly QCAlgorithm _algorithm;
        private readonly string _experimentId;
        private readonly string _windowId;
        private readonly string _stageId;
        private readonly string _runId;
        private readonly string _candidateId;
        private long _sequence;
        private readonly object _traceLock = new();

        /// <summary>
        /// Constructs the decorator wrapping <paramref name="inner"/>. When
        /// <paramref name="algorithm"/> is non-null and the identity strings are null,
        /// identity is read from the algorithm's parameters
        /// (<c>experiment-id</c>, <c>window-id</c>, <c>stage-id</c>, <c>run-id</c>,
        /// <c>candidate-id</c>); otherwise sensible defaults are used. This matches the
        /// plan's verbatim construction
        /// <c>new TracingExecutionModel(Execution, _sink, this)</c>.
        /// </summary>
        public TracingExecutionModel(IExecutionModel inner, IFormalTraceSink sink,
            QCAlgorithm algorithm,
            string experimentId = null,
            string windowId = null,
            string stageId = null,
            string runId = null,
            string candidateId = null)
            : base(asynchronous: true)
        {
            _inner = inner ?? throw new ArgumentNullException(nameof(inner));
            _sink = sink ?? throw new ArgumentNullException(nameof(sink));
            _algorithm = algorithm;
            _experimentId = ResolveIdentity(experimentId, algorithm, "experiment-id", "E1");
            _windowId = ResolveIdentity(windowId, algorithm, "window-id", "W1");
            _stageId = ResolveIdentity(stageId, algorithm, "stage-id", "G0");
            _runId = ResolveIdentity(runId, algorithm, "run-id", "R1");
            _candidateId = ResolveIdentity(candidateId, algorithm, "candidate-id", "C1");
        }

        private static string ResolveIdentity(string supplied, QCAlgorithm algorithm,
            string parameterName, string defaultValue)
        {
            if (!string.IsNullOrWhiteSpace(supplied)) return supplied;
            if (algorithm != null)
            {
                var p = algorithm.GetParameter(parameterName);
                if (!string.IsNullOrWhiteSpace(p)) return p;
            }
            return defaultValue;
        }

        /// <summary>
        /// Emits DECISION (the portfolio targets just received), delegates EXACTLY ONCE
        /// to the inner execution model, then discovers the native orders the inner
        /// model created and emits an ORDER_INTENT per new order carrying the native
        /// <c>orderId</c>. Does not reproduce sizing/rounding/margin.
        /// </summary>
        public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // The ctor-supplied algorithm is authoritative for trace identity; accept
            // the engine-passed algorithm as a fallback (they are the same instance in
            // the proof run).
            var algo = algorithm ?? _algorithm;

            // DECISION BEFORE delegating: captures the risk-adjusted targets that the
            // portfolio construction layer emitted this bar.
            var decisionTargets = (targets ?? Array.Empty<IPortfolioTarget>())
                .Select(t => new { symbol = t.Symbol.Value, quantity = t.Quantity })
                .ToList();
            Emit(algo, "DECISION", new { targets = decisionTargets });

            // Record known order ids before delegation so we can diff afterwards.
            var knownOrderIds = GetKnownOrderIds(algo);

            // Delegate EXACTLY ONCE to the mature execution model. No reproduction of
            // sizing, lot rounding, or margin checks.
            _inner.Execute(algo, targets);

            // ORDER_INTENT AFTER delegating: one per newly-created native order.
            foreach (var record in DiscoverNewOrders(algo, knownOrderIds))
            {
                Emit(algo, "ORDER_INTENT", new
                {
                    orderId = record.OrderId,
                    symbol = record.Symbol.Value,
                    quantity = record.Quantity
                });
            }
        }

        /// <summary>
        /// Delegates to the inner model, then - on a fill event - emits a native FILL
        /// and an immediate post-fill HOLDINGS_SNAPSHOT correlated by
        /// <c>orderId</c>. Both values are read directly from the LEAN-native
        /// <see cref="OrderEvent"/> and <see cref="Security.Holdings"/>.
        /// </summary>
        public override void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent)
        {
            // Delegate first so the inner model processes the event contractually.
            _inner.OnOrderEvent(algorithm, orderEvent);

            if (orderEvent == null) return;

            // Emit FILL on PartiallyFilled and Filled (each gets its own snapshot).
            // Non-fill statuses (Submitted, Canceled, Invalid, ...) are not traced as
            // fills; they pass through to the inner model only.
            var isFill = orderEvent.Status == OrderStatus.Filled
                || orderEvent.Status == OrderStatus.PartiallyFilled;
            if (!isFill || orderEvent.FillQuantity == 0m) return;

            var algo = algorithm ?? _algorithm;

            // FILL: pure LEAN-native OrderEvent fields.
            Emit(algo, "FILL", new
            {
                orderId = orderEvent.OrderId,
                symbol = orderEvent.Symbol.Value,
                fillPrice = orderEvent.FillPrice,
                fillQuantity = orderEvent.FillQuantity,
                fee = (decimal)orderEvent.OrderFee,
                feeCurrency = orderEvent.OrderFee?.Value.Currency,
                status = orderEvent.Status.ToString(),
                direction = orderEvent.Direction.ToString()
            }, orderEvent.UtcTime);

            // HOLDINGS_SNAPSHOT immediately after the fill, reading CURRENT native
            // holdings for the filled symbol. LEAN-native accounting; never reconstructed.
            // ShouldEmitHoldingsSnapshot is a testability seam (default true); production
            // always emits the post-fill snapshot so the reconciler can correlate.
            if (!ShouldEmitHoldingsSnapshot(orderEvent)) return;
            var snapshot = BuildHoldingsSnapshotPayload(algo, orderEvent.Symbol, orderEvent.OrderId);
            Emit(algo, "HOLDINGS_SNAPSHOT", new
            {
                orderId = snapshot.OrderId,
                symbol = snapshot.Symbol,
                quantity = snapshot.Quantity,
                averagePrice = snapshot.AveragePrice,
                price = snapshot.Price,
                cash = snapshot.Cash,
                totalPortfolioValue = snapshot.TotalPortfolioValue
            }, orderEvent.UtcTime);
        }

        /// <summary>
        /// Delegates security-changed events to the inner model. No trace emission.
        /// </summary>
        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            _inner.OnSecuritiesChanged(algorithm, changes);
        }

        // ----------------------------------------------------------------
        // Testability seams (protected virtual). Production paths read real
        // LEAN-native state; test doubles override for deterministic behavior.
        // ----------------------------------------------------------------

        /// <summary>
        /// Returns the set of known native order ids before delegating to the inner
        /// model. Production: enumerates <see cref="SecurityTransactionManager.GetOrderTickets"/>.
        /// </summary>
        protected virtual ISet<int> GetKnownOrderIds(QCAlgorithm algorithm)
        {
            var set = new HashSet<int>();
            if (algorithm == null) return set;
            foreach (var t in algorithm.Transactions.GetOrderTickets())
            {
                set.Add(t.OrderId);
            }
            return set;
        }

        /// <summary>
        /// Discovers orders newly created by the inner model, as native
        /// <see cref="OrderIntentRecord"/>s. Production: diffs
        /// <see cref="SecurityTransactionManager.GetOrderTickets"/> against the
        /// previously-known ids. Does not reproduce sizing.
        /// </summary>
        protected virtual IReadOnlyList<OrderIntentRecord> DiscoverNewOrders(
            QCAlgorithm algorithm, ISet<int> knownOrderIds)
        {
            var records = new List<OrderIntentRecord>();
            if (algorithm == null) return records;
            foreach (var t in algorithm.Transactions.GetOrderTickets())
            {
                if (knownOrderIds.Add(t.OrderId))
                {
                    records.Add(new OrderIntentRecord(t.OrderId, t.Symbol, t.Quantity));
                }
            }
            return records;
        }

        /// <summary>
        /// Reads the CURRENT LEAN-native holdings snapshot for <paramref name="symbol"/>
        /// immediately after a fill. Production: reads
        /// <see cref="Security.Holdings"/> and <see cref="SecurityPortfolioManager"/>
        /// directly; never reconstructs. Test doubles may synthesize values.
        /// </summary>
        /// <summary>
        /// Testability seam: returns true by default so the post-fill HOLDINGS_SNAPSHOT
        /// is always emitted in production. A test double may override to return false
        /// to simulate a missing snapshot so the reconciler (correctly) fails.
        /// </summary>
        protected virtual bool ShouldEmitHoldingsSnapshot(OrderEvent orderEvent) => true;

        protected virtual HoldingsSnapshotPayload BuildHoldingsSnapshotPayload(
            QCAlgorithm algorithm, Symbol symbol, int orderId)
        {
            if (algorithm == null)
            {
                return new HoldingsSnapshotPayload(orderId, symbol.Value, 0m, 0m, 0m, 0m, 0m);
            }
            var security = algorithm.Securities[symbol];
            var holdings = security.Holdings;
            return new HoldingsSnapshotPayload(
                orderId,
                symbol.Value,
                holdings.Quantity,
                holdings.AveragePrice,
                security.Price,
                algorithm.Portfolio.Cash,
                algorithm.Portfolio.TotalPortfolioValue);
        }

        // ----------------------------------------------------------------
        // Trace emission helpers.
        // ----------------------------------------------------------------

        private void Emit(QCAlgorithm algorithm, string eventType, object payload, DateTime? eventTimeUtc = null)
        {
            // Sequence allocation AND the sink write happen inside the SAME critical
            // section so write order always matches sequence order. Holding _traceLock
            // across Write guarantees no thread can write seq=N+1 before seq=N lands,
            // which would otherwise make the strict sink reject the in-order sequence
            // (it expects lastSequence+1) and spuriously raise FAILED_EVIDENCE_CAPTURE
            // under concurrent emission. The sink's own internal _lock is a distinct
            // object, so there is no re-entrancy deadlock. Any I/O failure propagates.
            lock (_traceLock)
            {
                var seq = ++_sequence;
                var ev = new FormalTraceEvent(
                    schemaVersion: "1",
                    sequence: seq,
                    eventType: eventType,
                    experimentId: _experimentId,
                    windowId: _windowId,
                    stageId: _stageId,
                    runId: _runId,
                    candidateId: _candidateId,
                    eventTimeUtc: eventTimeUtc ?? GetUtcTime(algorithm),
                    payload: payload);
                _sink.Write(ev);
            }
        }

        private static DateTime GetUtcTime(QCAlgorithm algorithm)
        {
            if (algorithm != null) return algorithm.UtcTime;
            return DateTime.UtcNow;
        }
    }
}
