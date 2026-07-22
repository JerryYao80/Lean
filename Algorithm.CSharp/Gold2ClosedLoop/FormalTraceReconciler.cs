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
using System.Linq;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Algorithm.CSharp.Gold2ClosedLoop
{
    /// <summary>
    /// Exception raised by <see cref="FormalTraceReconciler"/> when a formal trace
    /// violates an order/fill/holdings correlation invariant. The exception message
    /// is actionable and includes the offending sequence and orderId so that the
    /// caller can identify the exact event that failed the proof gate.
    /// </summary>
    public sealed class FormalTraceReconciliationException : Exception
    {
        /// <summary>
        /// Initializes a new instance of the <see cref="FormalTraceReconciliationException"/> class.
        /// </summary>
        /// <param name="message">The actionable error message.</param>
        public FormalTraceReconciliationException(string message)
            : base(message)
        {
        }

        /// <summary>
        /// Initializes a new instance of the <see cref="FormalTraceReconciliationException"/> class.
        /// </summary>
        /// <param name="message">The actionable error message.</param>
        /// <param name="innerException">The inner exception.</param>
        public FormalTraceReconciliationException(string message, Exception innerException)
            : base(message, innerException)
        {
        }
    }

    /// <summary>
    /// Reconciles a sequence of formal trace events, enforcing the order/fill/holdings
    /// correlation invariants required by the Gold2 closed-loop proof (spec §8):
    /// <list type="bullet">
    /// <item><description>Every <c>FILL</c> must reference an earlier correlated <c>ORDER_INTENT</c>.</description></item>
    /// <item><description>Every <c>FILL</c> must have a following correlated <c>HOLDINGS_SNAPSHOT</c>.</description></item>
    /// <item><description>Correlation uses the native <c>orderId</c> field in the payload; matching by event count alone is forbidden.</description></item>
    /// <item><description>Duplicate sequences and malformed correlations are rejected deterministically.</description></item>
    /// </list>
    /// The reconciler operates on in-memory <see cref="FormalTraceEvent"/> instances,
    /// which is the simplest robust design: it avoids re-parsing JSONL and keeps the
    /// validation logic language-agnostic for later porting to the Python formal loader.
    /// </summary>
    public static class FormalTraceReconciler
    {
        // Closed enum of event types recognized by the reconciler. Unknown event types
        // are allowed (forward compatibility) but the three correlation-bearing types
        // are validated strictly.
        private const string OrderIntentEventType = "ORDER_INTENT";
        private const string FillEventType = "FILL";
        private const string HoldingsSnapshotEventType = "HOLDINGS_SNAPSHOT";
        private const string OrderIdFieldName = "orderId";

        /// <summary>
        /// Reconciles the supplied events in order. The list is processed exactly once;
        /// ordering semantics are intent-before-fill and snapshot-after-fill. Violations
        /// throw <see cref="FormalTraceReconciliationException"/> with an actionable
        /// message identifying the offending sequence and orderId.
        /// </summary>
        /// <param name="events">
        /// The events to reconcile, in the order they were written. The caller is
        /// responsible for preserving write order (e.g. by copying the sink's in-memory
        /// list rather than re-reading the JSONL).
        /// </param>
        /// <exception cref="FormalTraceReconciliationException">A correlation invariant was violated.</exception>
        public static void Reconcile(IReadOnlyList<FormalTraceEvent> events)
        {
            if (events == null)
            {
                throw new ArgumentNullException(nameof(events));
            }

            if (events.Count == 0)
            {
                return;
            }

            var seenSequences = new HashSet<long>();
            // Map orderId -> sequence of the ORDER_INTENT that opened it.
            var openIntentsByOrderId = new Dictionary<long, long>();
            // Map orderId -> queue of fill sequences that still need a following snapshot.
            var pendingSnapshotsByOrderId = new Dictionary<long, Queue<long>>();

            foreach (var ev in events)
            {
                if (!seenSequences.Add(ev.Sequence))
                {
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "Duplicate formal trace sequence {0} detected; sequences must be strictly unique.",
                            ev.Sequence));
                }

                if (string.IsNullOrEmpty(ev.EventType))
                {
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "Formal trace sequence {0} has an empty event type.",
                            ev.Sequence));
                }

                // Only correlation-bearing types need validation; DECISION and other
                // event types pass through.
                if (ev.EventType == OrderIntentEventType)
                {
                    var orderId = ExtractOrderId(ev, required: true);
                    if (openIntentsByOrderId.ContainsKey(orderId))
                    {
                        // A duplicate intent for the same order id without an intervening
                        // fill is ambiguous; reject deterministically.
                        throw new FormalTraceReconciliationException(
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "Duplicate ORDER_INTENT for orderId {0} at sequence {1}; an earlier " +
                                "intent for the same order is still open.",
                                orderId,
                                ev.Sequence));
                    }
                    openIntentsByOrderId[orderId] = ev.Sequence;
                }
                else if (ev.EventType == FillEventType)
                {
                    var orderId = ExtractOrderId(ev, required: true);
                    if (!openIntentsByOrderId.TryGetValue(orderId, out var intentSequence))
                    {
                        throw new FormalTraceReconciliationException(
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "FILL at sequence {0} references orderId {1} but no earlier ORDER_INTENT " +
                                "for that order exists. Ordering requires intent before fill.",
                                ev.Sequence,
                                orderId));
                    }

                    // The fill is accepted; record that a holdings snapshot is now pending
                    // for this fill so we can detect a missing post-fill snapshot later.
                    if (!pendingSnapshotsByOrderId.TryGetValue(orderId, out var pending))
                    {
                        pending = new Queue<long>();
                        pendingSnapshotsByOrderId[orderId] = pending;
                    }
                    pending.Enqueue(ev.Sequence);
                }
                else if (ev.EventType == HoldingsSnapshotEventType)
                {
                    var orderId = ExtractOrderId(ev, required: true);
                    if (!openIntentsByOrderId.ContainsKey(orderId))
                    {
                        throw new FormalTraceReconciliationException(
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "HOLDINGS_SNAPSHOT at sequence {0} references orderId {1} but no " +
                                "ORDER_INTENT for that order exists.",
                                ev.Sequence,
                                orderId));
                    }

                    if (!pendingSnapshotsByOrderId.TryGetValue(orderId, out var pending) || pending.Count == 0)
                    {
                        // Snapshot without a preceding unfilled fill for this order.
                        // This is not necessarily fatal for snapshot-only diagnostics, but
                        // under strict one-to-one correlation it indicates a spurious
                        // snapshot: reject deterministically.
                        throw new FormalTraceReconciliationException(
                            string.Format(
                                CultureInfo.InvariantCulture,
                                "HOLDINGS_SNAPSHOT at sequence {0} for orderId {1} has no preceding FILL " +
                                "awaiting a snapshot; correlation is spurious.",
                                ev.Sequence,
                                orderId));
                    }

                    // Pop the oldest pending fill for this order id. Strict one-to-one
                    // correlation: one snapshot closes one fill.
                    pending.Dequeue();
                }
                // else: DECISION or unknown event types pass through without correlation.
            }

            // After processing all events, every fill must have been closed by a
            // correlated holdings snapshot. Any leftover pending snapshots are
            // missing post-fill evidence.
            foreach (var kvp in pendingSnapshotsByOrderId)
            {
                if (kvp.Value.Count > 0)
                {
                    var missingCount = kvp.Value.Count;
                    var firstMissingFill = kvp.Value.Peek();
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "FILL at sequence {0} for orderId {1} has no following correlated " +
                            "HOLDINGS_SNAPSHOT ({2} missing snapshot(s) for this order). Every fill " +
                            "must be followed by a correlated snapshot.",
                            firstMissingFill,
                            kvp.Key,
                            missingCount));
                }
            }
        }

        /// <summary>
        /// Extracts the <c>orderId</c> field from the event payload. The field is
        /// required for ORDER_INTENT, FILL and HOLDINGS_SNAPSHOT events. A missing
        /// or non-integer orderId is rejected deterministically with an actionable
        /// message. Decimal order ids (e.g. <c>7m</c>) are coerced to long when
        /// possible; non-integer values are rejected.
        /// </summary>
        private static long ExtractOrderId(FormalTraceEvent ev, bool required)
        {
            var payload = ev.PayloadAsJObject();
            if (payload == null)
            {
                if (required)
                {
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "{0} at sequence {1} has no payload object; required field '{2}' is missing.",
                            ev.EventType,
                            ev.Sequence,
                            OrderIdFieldName));
                }
                return 0;
            }

            var token = payload.GetValue(OrderIdFieldName, StringComparison.OrdinalIgnoreCase);
            if (token == null || token.Type == JTokenType.Null)
            {
                if (required)
                {
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "{0} at sequence {1} is missing required correlation field '{2}'; " +
                            "correlation must use the native order id, not event count.",
                            ev.EventType,
                            ev.Sequence,
                            OrderIdFieldName));
                }
                return 0;
            }

            // Accept integer tokens directly; accept decimal/double tokens only when
            // they represent whole numbers, to preserve compatibility with payloads
            // that were authored with 'm' suffix in C# (decimal) but serialized to JSON
            // as 7.0.
            long orderId;
            switch (token.Type)
            {
                case JTokenType.Integer:
                    orderId = token.Value<long>();
                    break;
                case JTokenType.Float:
                    {
                        var d = token.Value<decimal>();
                        if (d != Math.Truncate(d))
                        {
                            throw new FormalTraceReconciliationException(
                                string.Format(
                                    CultureInfo.InvariantCulture,
                                    "{0} at sequence {1} has a non-integer '{2}' value {3}; " +
                                    "order ids must be integers.",
                                    ev.EventType,
                                    ev.Sequence,
                                    OrderIdFieldName,
                                    d.ToString(CultureInfo.InvariantCulture)));
                        }
                        orderId = (long)d;
                    }
                    break;
                case JTokenType.String:
                    {
                        var s = token.Value<string>();
                        if (!long.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out orderId))
                        {
                            throw new FormalTraceReconciliationException(
                                string.Format(
                                    CultureInfo.InvariantCulture,
                                    "{0} at sequence {1} has a non-integer '{2}' string value '{3}'; " +
                                    "order ids must be integers.",
                                    ev.EventType,
                                    ev.Sequence,
                                    OrderIdFieldName,
                                    s));
                        }
                    }
                    break;
                default:
                    throw new FormalTraceReconciliationException(
                        string.Format(
                            CultureInfo.InvariantCulture,
                            "{0} at sequence {1} has an unsupported JSON type {2} for field '{3}'; " +
                            "order ids must be integers.",
                            ev.EventType,
                            ev.Sequence,
                            token.Type,
                            OrderIdFieldName));
            }

            if (orderId <= 0)
            {
                throw new FormalTraceReconciliationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "{0} at sequence {1} has a non-positive orderId {2}; order ids must be positive.",
                        ev.EventType,
                        ev.Sequence,
                        orderId));
            }

            return orderId;
        }
    }
}
