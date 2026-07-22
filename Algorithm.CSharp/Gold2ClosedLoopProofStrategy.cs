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
using QuantConnect.Algorithm.CSharp.Gold2ClosedLoop;
using QuantConnect.Algorithm.Framework.Execution;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Proof-only subclass of the mature <see cref="Gold2BetaVolTargetStrategy"/>.
    /// Adds formal closed-loop trace instrumentation (Task 5/6) WITHOUT changing
    /// mature behavior: <see cref="Initialize"/> runs <see cref="Gold2BetaVolTargetStrategy.Initialize"/>
    /// unchanged, then wraps the already-installed <see cref="AShareLotSizeExecutionModel"/>
    /// with a <see cref="TracingExecutionModel"/> so the LEAN engine's per-fill
    /// dispatch (BrokerageTransactionHandler.cs:1369) emits the DECISION /
    /// ORDER_INTENT / FILL / HOLDINGS_SNAPSHOT chain to a strict JSONL sink.
    /// </summary>
    /// <remarks>
    /// <para><b>Immutability contract.</b> This class is PROOF-ONLY: it must never
    /// modify mature Gold2 behavior beyond adding trace emission. The mature
    /// <see cref="Gold2BetaVolTargetStrategy.Initialize"/> sets up the universe,
    /// alpha, portfolio, risk and execution layers; this override only:
    /// <list type="number">
    /// <item><description>Calls <see cref="Gold2BetaVolTargetStrategy.Initialize"/> so the mature <c>AShareLotSizeExecutionModel</c> is installed;</description></item>
    /// <item><description>Validates and opens the formal trace sink at <c>formal-trace-path</c>;</description></item>
    /// <item><description>Replaces <see cref="QCAlgorithm.Execution"/> with a <see cref="TracingExecutionModel"/> that wraps the mature model.</description></item>
    /// </list>
    /// No sizing, rounding, margin, fill, fee, holdings, cash or TPV logic is reproduced.</para>
    /// <para><b>Evidence gate.</b> <see cref="OnEndOfAlgorithm"/> calls
    /// <see cref="IFormalTraceSink.FlushAndReconcile"/> and disposes the sink. Any
    /// reconciliation violation (e.g. a FILL with no following HOLDINGS_SNAPSHOT)
    /// or I/O failure propagates as a runtime error (FAILED_EVIDENCE_CAPTURE
    /// semantics). The sink is never swallowed.</para>
    /// </remarks>
    public class Gold2ClosedLoopProofStrategy : Gold2BetaVolTargetStrategy
    {
        private IFormalTraceSink _sink;

        /// <summary>
        /// Runs the mature Gold2 initialization unchanged, then installs the
        /// <see cref="TracingExecutionModel"/> decorator around the mature
        /// <see cref="AShareLotSizeExecutionModel"/> and opens the formal trace
        /// sink. The <c>formal-trace-path</c> parameter is REQUIRED.
        /// </summary>
        public override void Initialize()
        {
            // Mature setup: universe, alpha, portfolio, risk, and the mature
            // AShareLotSizeExecutionModel are installed here. Unchanged.
            base.Initialize();

            // REQUIRED formal trace path. A missing/empty path is a configuration
            // error that must fail loudly (no silent no-op trace).
            var path = GetParameter("formal-trace-path");
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException(
                    "formal-trace-path is required for Gold2ClosedLoopProofStrategy");
            }
            _sink = new FormalJsonlTraceSink(path);

            // Wrap the mature execution model (installed by base.Initialize) with the
            // tracing decorator. The engine captures this Execution property later in
            // BrokerageTransactionHandler.Initialize (Engine/Engine.cs:332 runs AFTER
            // BacktestingSetupHandler.cs:194 algorithm.Initialize()), so the decorator
            // receives every per-fill OnOrderEvent dispatch from the engine.
            Execution = new TracingExecutionModel(Execution, _sink, this);
        }

        /// <summary>
        /// Proof gate: flushes the trace sink and runs the in-memory reconciliation
        /// (every FILL references an earlier ORDER_INTENT; every FILL has a following
        /// correlated HOLDINGS_SNAPSHOT). Any violation or I/O failure propagates as a
        /// runtime error - never swallowed. The sink is then disposed.
        /// </summary>
        public override void OnEndOfAlgorithm()
        {
            // Delegate to the mature OnEndOfAlgorithm first (base class hook) so any
            // mature finalization runs before we gate the evidence.
            base.OnEndOfAlgorithm();

            // FlushAndReconcile is the explicit proof gate. A reconciliation or I/O
            // failure here surfaces as a runtime error (FAILED_EVIDENCE_CAPTURE).
            // We do NOT catch: swallowing would silently mask broken evidence.
            if (_sink != null)
            {
                _sink.FlushAndReconcile();
                _sink.Dispose();
            }
        }
    }
}
