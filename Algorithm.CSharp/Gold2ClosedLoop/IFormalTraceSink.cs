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

namespace QuantConnect.Algorithm.CSharp.Gold2ClosedLoop
{
    /// <summary>
    /// Strict, append-only JSONL sink for Gold2 closed-loop formal trace events.
    /// A formal trace sink is a REQUIRED dependency of the proof pipeline
    /// (spec §8): any write failure, flush failure or reconciliation failure
    /// must surface as <c>FAILED_EVIDENCE_CAPTURE</c> rather than being
    /// silently swallowed. Implementations must be thread-safe and must not
    /// overwrite existing evidence.
    /// </summary>
    /// <remarks>
    /// Implementations must:
    /// <list type="bullet">
    /// <item><description>open the target with <c>FileMode.CreateNew</c> (never overwrite existing evidence);</description></item>
    /// <item><description>propagate construction failures (no swallowed errors);</description></item>
    /// <item><description>serialize writes under a lock (thread-safe);</description></item>
    /// <item><description>require strictly monotonically increasing sequences starting at 1;</description></item>
    /// <item><description>propagate append/write errors;</description></item>
    /// <item><description>flush the text writer AND <c>FileStream.Flush(true)</c> during reconciliation;</description></item>
    /// <item><description>not implicitly reconcile on <see cref="IDisposable.Dispose"/> (explicit <see cref="FlushAndReconcile"/> is the proof gate).</description></item>
    /// </list>
    /// </remarks>
    public interface IFormalTraceSink : IDisposable
    {
        /// <summary>
        /// Appends a single canonical JSON object on its own line. The sequence
        /// must equal <c>lastWrittenSequence + 1</c>, beginning at 1. Sequence
        /// gaps, duplicates, or zero as the first sequence are rejected without
        /// writing a partial line.
        /// </summary>
        /// <param name="traceEvent">The trace event to write.</param>
        /// <exception cref="InvalidOperationException">The sequence is out of order, duplicated, or does not start at 1.</exception>
        /// <exception cref="ObjectDisposedException">The sink has been disposed.</exception>
        /// <exception cref="System.IO.IOException">An I/O failure occurred during the write.</exception>
        void Write(FormalTraceEvent traceEvent);

        /// <summary>
        /// Flushes the text writer, calls <c>FileStream.Flush(true)</c> for durable
        /// file data, and then runs the in-memory order/fill/holdings reconciliation
        /// over every event written so far. This is the explicit proof gate; callers
        /// MUST invoke it before <see cref="IDisposable.Dispose"/> when collecting formal evidence.
        /// </summary>
        /// <exception cref="FormalTraceReconciliationException">A correlation invariant was violated.</exception>
        /// <exception cref="ObjectDisposedException">The sink has been disposed.</exception>
        /// <exception cref="System.IO.IOException">An I/O failure occurred during the flush.</exception>
        void FlushAndReconcile();
    }
}
