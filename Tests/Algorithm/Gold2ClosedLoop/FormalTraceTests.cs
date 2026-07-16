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
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Gold2ClosedLoop;

namespace QuantConnect.Tests.Algorithm.Gold2ClosedLoop
{
    /// <summary>
    /// Tests for the Gold2 closed-loop formal trace DTO, strict JSONL sink and
    /// order/fill/holdings reconciliation. Spec §8 requires a versioned event schema
    /// with strict JSONL writing and FILL→ORDER_INTENT/HOLDINGS_SNAPSHOT correlation.
    /// </summary>
    [TestFixture]
    public class FormalTraceTests
    {
        private string _tempDir;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(),
                "gold2_formal_trace_" + Guid.NewGuid().ToString("N"));
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

        private string NewTracePath() => Path.Combine(_tempDir, "trace_" + Guid.NewGuid().ToString("N") + ".jsonl");

        // ----------------------------------------------------------------
        // FormalTraceEvent DTO tests
        // ----------------------------------------------------------------

        [Test]
        public void Constructor_PreservesAllIdentityFields()
        {
            var utcNow = DateTime.UtcNow;
            var payload = new { orderId = 7, fillPrice = 12.34m, fillQuantity = 100m, fee = 1.23m };
            var ev = new FormalTraceEvent(
                schemaVersion: "1",
                sequence: 1,
                eventType: "FILL",
                experimentId: "exp",
                windowId: "W1",
                stageId: "G0",
                runId: "run",
                candidateId: "c",
                eventTimeUtc: utcNow,
                payload: payload);

            Assert.That(ev.SchemaVersion, Is.EqualTo("1"));
            Assert.That(ev.Sequence, Is.EqualTo(1));
            Assert.That(ev.EventType, Is.EqualTo("FILL"));
            Assert.That(ev.ExperimentId, Is.EqualTo("exp"));
            Assert.That(ev.WindowId, Is.EqualTo("W1"));
            Assert.That(ev.StageId, Is.EqualTo("G0"));
            Assert.That(ev.RunId, Is.EqualTo("run"));
            Assert.That(ev.CandidateId, Is.EqualTo("c"));
            Assert.That(ev.EventTimeUtc, Is.EqualTo(utcNow));
            Assert.That(ev.Payload, Is.Not.Null);
        }

        [Test]
        public void Serialize_PreservesDecimalPrecisionInJson()
        {
            var value = new FormalTraceEvent("1", 1, "FILL", "exp", "W1", "G0", "run", "c",
                DateTime.UtcNow,
                new { orderId = 7, fillPrice = 12.34m, fillQuantity = 100m, fee = 1.23m });
            var json = JsonConvert.SerializeObject(value);
            Assert.That(json, Does.Contain("12.34"));
            Assert.That(json, Does.Contain("100"));
            Assert.That(json, Does.Contain("1.23"));
            Assert.That(json, Does.Contain("\"schema_version\":\"1\""));
            Assert.That(json, Does.Contain("\"sequence\":1"));
            Assert.That(json, Does.Contain("\"event_type\":\"FILL\""));
            Assert.That(json, Does.Contain("\"experiment_id\":\"exp\""));
            Assert.That(json, Does.Contain("\"window_id\":\"W1\""));
            Assert.That(json, Does.Contain("\"stage_id\":\"G0\""));
            Assert.That(json, Does.Contain("\"run_id\":\"run\""));
            Assert.That(json, Does.Contain("\"candidate_id\":\"c\""));
        }

        [Test]
        public void Deserialize_RoundTripsDecimalValues()
        {
            var original = new FormalTraceEvent("1", 5, "ORDER_INTENT", "exp2", "W2", "G1",
                "run2", "c2", DateTime.UtcNow,
                new { orderId = 42, limitPrice = 98.76m, limitQuantity = 500m });
            var json = JsonConvert.SerializeObject(original);
            var roundTripped = JsonConvert.DeserializeObject<FormalTraceEvent>(json);

            Assert.That(roundTripped.SchemaVersion, Is.EqualTo(original.SchemaVersion));
            Assert.That(roundTripped.Sequence, Is.EqualTo(original.Sequence));
            Assert.That(roundTripped.EventType, Is.EqualTo(original.EventType));
            Assert.That(roundTripped.ExperimentId, Is.EqualTo(original.ExperimentId));
            Assert.That(roundTripped.WindowId, Is.EqualTo(original.WindowId));
            Assert.That(roundTripped.StageId, Is.EqualTo(original.StageId));
            Assert.That(roundTripped.RunId, Is.EqualTo(original.RunId));
            Assert.That(roundTripped.CandidateId, Is.EqualTo(original.CandidateId));
            var payloadJson = roundTripped.Payload.ToString(Formatting.None);
            Assert.That(payloadJson, Does.Contain("98.76"));
            Assert.That(payloadJson, Does.Contain("500"));
        }

        // ----------------------------------------------------------------
        // FormalJsonlTraceSink construction tests
        // ----------------------------------------------------------------

        [Test]
        public void Sink_ConstructionFailsWhenParentDirectoryMissing()
        {
            var missingPath = Path.Combine(
                Path.Combine(_tempDir, "missing_parent", "deeper"),
                "trace.jsonl");
            Assert.Throws<DirectoryNotFoundException>(() =>
            {
                using var sink = new FormalJsonlTraceSink(missingPath);
            });
        }

        [Test]
        public void Sink_FileModeCreateNewRefusesOverwrite()
        {
            var p = NewTracePath();
            File.WriteAllText(p, "stale\n");
            Assert.Throws<IOException>(() =>
            {
                using var sink = new FormalJsonlTraceSink(p);
            });
        }

        [Test]
        public void Sink_ConstructionFailsWhenTargetIsADirectory()
        {
            Assert.Throws<SystemException>(() =>
            {
                using var sink = new FormalJsonlTraceSink(_tempDir);
            });
        }

        [Test]
        public void Sink_RejectsEmptyPath()
        {
            Assert.Throws<ArgumentException>(() =>
            {
                using var sink = new FormalJsonlTraceSink("");
            });
        }

        [Test]
        public void Sink_RejectsWhitespacePath()
        {
            Assert.Throws<ArgumentException>(() =>
            {
                using var sink = new FormalJsonlTraceSink("   ");
            });
        }

        // ----------------------------------------------------------------
        // Sequence enforcement tests
        // ----------------------------------------------------------------

        [Test]
        public void Write_SequenceOneAcceptedWhenStarting()
        {
            using var sink = new FormalJsonlTraceSink(NewTracePath());
            var ev = MakeEvent(sequence: 1, eventType: "DECISION");
            Assert.DoesNotThrow(() => sink.Write(ev));
        }

        [Test]
        public void Write_RejectsSequenceZeroAsFirstEvent()
        {
            using var sink = new FormalJsonlTraceSink(NewTracePath());
            var ev = MakeEvent(sequence: 0, eventType: "DECISION");
            Assert.Throws<InvalidOperationException>(() => sink.Write(ev));
        }

        [Test]
        public void Write_RejectsNegativeSequenceAsFirstEvent()
        {
            using var sink = new FormalJsonlTraceSink(NewTracePath());
            var ev = MakeEvent(sequence: -3, eventType: "DECISION");
            Assert.Throws<InvalidOperationException>(() => sink.Write(ev));
        }

        [Test]
        public void Write_RejectsOutOfOrderSequence()
        {
            var p = NewTracePath();
            using var sink = new FormalJsonlTraceSink(p);
            sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
            Assert.Throws<InvalidOperationException>(() =>
                sink.Write(MakeEvent(sequence: 3, eventType: "DECISION")));
            // File still only has the first line; no partial invalid line written.
            sink.FlushAndReconcile();
            Assert.That(File.ReadAllLines(p).Length, Is.EqualTo(1));
        }

        [Test]
        public void Write_RejectsDuplicateSequence()
        {
            using var sink = new FormalJsonlTraceSink(NewTracePath());
            sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
            Assert.Throws<InvalidOperationException>(() =>
                sink.Write(MakeEvent(sequence: 1, eventType: "DECISION")));
        }

        [Test]
        public void Write_AcceptsSequentialOneTwoThreeAndDurableJsonlHasThreeLines()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
                sink.Write(MakeEvent(sequence: 2, eventType: "DECISION"));
                sink.Write(MakeEvent(sequence: 3, eventType: "DECISION"));
                sink.FlushAndReconcile();
            }

            var lines = File.ReadAllLines(p);
            Assert.That(lines.Length, Is.EqualTo(3), "durable JSONL must have 3 lines after flush");
            foreach (var line in lines)
            {
                Assert.That(line, Does.Contain("\"schema_version\""));
                Assert.That(line, Does.Contain("\"sequence\""));
                Assert.That(line, Does.Contain("\"event_type\""));
            }
        }

        [Test]
        public void FlushAndReconcile_DurablyPersistsLinesBeforeReconciliation()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
                sink.FlushAndReconcile();
                Assert.That(File.ReadAllLines(p).Length, Is.EqualTo(1));
            }
        }

        [Test]
        public void Dispose_IsIdempotent()
        {
            var p = NewTracePath();
            var sink = new FormalJsonlTraceSink(p);
            sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
            sink.Dispose();
            Assert.DoesNotThrow(() => sink.Dispose());
        }

        [Test]
        public void Dispose_DoesNotImplicitlyReconcile()
        {
            using (var sink = new FormalJsonlTraceSink(NewTracePath()))
            {
                sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
            }
        }

        [Test]
        public void Write_AfterDisposeThrowsObjectDisposedException()
        {
            var sink = new FormalJsonlTraceSink(NewTracePath());
            sink.Dispose();
            Assert.Throws<ObjectDisposedException>(() =>
                sink.Write(MakeEvent(sequence: 1, eventType: "DECISION")));
        }

        [Test]
        public void FlushAndReconcile_AfterDisposeThrowsObjectDisposedException()
        {
            var sink = new FormalJsonlTraceSink(NewTracePath());
            sink.Dispose();
            Assert.Throws<ObjectDisposedException>(() => sink.FlushAndReconcile());
        }

        [Test]
        public void FlushAndReconcile_RunsReconciliationOverWrittenEvents()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }));
                sink.Write(MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }));
                sink.Write(MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m }));
                Assert.DoesNotThrow(() => sink.FlushAndReconcile());
            }
        }

        [Test]
        public void FlushAndReconcile_PropagatesReconciliationFailure()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(1, "FILL", new { orderId = 7, fillPrice = 12.34m }));
                Assert.Throws<FormalTraceReconciliationException>(() => sink.FlushAndReconcile());
            }
        }

        [Test]
        public void Sink_WriteErrorsAreNotSwallowed()
        {
            using var sink = new FormalJsonlTraceSink(NewTracePath());
            Assert.Throws<ArgumentNullException>(() => sink.Write(null));
        }

        [Test]
        public void Sink_PropagatesReconciliationFailureFromFlushAndReconcile()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }));
                sink.Write(MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }));
                Assert.Throws<FormalTraceReconciliationException>(() => sink.FlushAndReconcile());
            }
        }

        // ----------------------------------------------------------------
        // Concurrent writes tests
        // ----------------------------------------------------------------

        [Test]
        public void Write_ConcurrentSequentialSequencesAreSerializedCorrectly()
        {
            var p = NewTracePath();
            using var sink = new FormalJsonlTraceSink(p);
            var sequences = Enumerable.Range(1, 50).ToList();
            var bags = new ConcurrentBag<Exception>();

            Parallel.ForEach(sequences, seq =>
            {
                try
                {
                    sink.Write(MakeEvent(sequence: seq, eventType: "DECISION"));
                }
                catch (Exception ex)
                {
                    bags.Add(ex);
                }
            });

            Assert.That(bags, Is.Empty,
                "deterministic sequential sequence writes must all succeed under the lock");
            sink.FlushAndReconcile();

            var lines = File.ReadAllLines(p);
            Assert.That(lines.Length, Is.EqualTo(50));
            var seenSequences = lines
                .Select(l => JsonConvert.DeserializeObject<FormalTraceEvent>(l).Sequence)
                .OrderBy(s => s)
                .ToList();
            Assert.That(seenSequences, Is.EqualTo(sequences.Select(s => (long)s).ToList()),
                "all 50 sequences must be present and parseable after concurrent writes");
        }

        [Test]
        public void Write_ConcurrentDuplicateSequencesRejectedWithoutCorruptLines()
        {
            var p = NewTracePath();
            using var sink = new FormalJsonlTraceSink(p);
            int successCount = 0;
            int failureCount = 0;
            var gate = new ManualResetEventSlim(false);
            var countdown = new CountdownEvent(20);
            var tasks = new Task[20];
            for (int i = 0; i < 20; i++)
            {
                tasks[i] = Task.Run(() =>
                {
                    gate.Wait();
                    try
                    {
                        sink.Write(MakeEvent(sequence: 1, eventType: "DECISION"));
                        Interlocked.Increment(ref successCount);
                    }
                    catch (InvalidOperationException)
                    {
                        Interlocked.Increment(ref failureCount);
                    }
                    finally
                    {
                        countdown.Signal();
                    }
                });
            }
            gate.Set();
            countdown.Wait();
            countdown.Dispose();
            gate.Dispose();
            Task.WaitAll(tasks);

            Assert.That(successCount, Is.EqualTo(1), "exactly one concurrent writer wins sequence 1");
            Assert.That(failureCount, Is.EqualTo(19), "all duplicates rejected");
            sink.FlushAndReconcile();

            var lines = File.ReadAllLines(p);
            Assert.That(lines.Length, Is.EqualTo(1), "no duplicate/corrupt lines produced");
        }

        // ----------------------------------------------------------------
        // FormalTraceReconciler tests
        // ----------------------------------------------------------------

        [Test]
        public void Reconcile_ValidOrderIntentFillHoldingsChainPasses()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }),
                MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m })
            };
            Assert.DoesNotThrow(() => FormalTraceReconciler.Reconcile(events));
        }

        [Test]
        public void Reconcile_FillWithoutIntentRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "FILL", new { orderId = 7 }),
                MakeEvent(2, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("ORDER_INTENT"));
            Assert.That(ex.Message, Does.Contain("7"));
        }

        [Test]
        public void Reconcile_FillWithoutFollowingHoldingsRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("HOLDINGS_SNAPSHOT"));
            Assert.That(ex.Message, Does.Contain("7"));
        }

        [Test]
        public void Reconcile_WrongOrderIdHoldingsRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }),
                MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 99, quantity = 100m })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("HOLDINGS_SNAPSHOT"));
            Assert.That(ex.Message, Does.Contain("99"));
        }

        [Test]
        public void Reconcile_FillAppearingBeforeIntentRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "FILL", new { orderId = 7 }),
                MakeEvent(2, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("ORDER_INTENT"));
            Assert.That(ex.Message, Does.Contain("7"));
        }

        [Test]
        public void Reconcile_DuplicateSequenceRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "DECISION"),
                MakeEvent(1, "ORDER_INTENT")
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("sequence"));
        }

        [Test]
        public void Reconcile_MultipleFillsForOneIntentEachRequireSnapshot()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }),
                MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 50m }),
                MakeEvent(4, "FILL", new { orderId = 7, fillPrice = 13.00m })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("HOLDINGS_SNAPSHOT"));
            Assert.That(ex.Message, Does.Contain("7"));
        }

        [Test]
        public void Reconcile_MultipleFillsEachWithSnapshotPasses()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }),
                MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }),
                MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 50m }),
                MakeEvent(4, "FILL", new { orderId = 7, fillPrice = 13.00m }),
                MakeEvent(5, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m })
            };
            Assert.DoesNotThrow(() => FormalTraceReconciler.Reconcile(events));
        }

        [Test]
        public void Reconcile_EmptyEventListPasses()
        {
            Assert.DoesNotThrow(() => FormalTraceReconciler.Reconcile(new List<FormalTraceEvent>()));
        }

        [Test]
        public void Reconcile_DecisionEventsWithoutFillsPass()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "DECISION"),
                MakeEvent(2, "DECISION")
            };
            Assert.DoesNotThrow(() => FormalTraceReconciler.Reconcile(events));
        }

        [Test]
        public void Reconcile_FillMissingOrderIdPayloadRejected()
        {
            var events = new List<FormalTraceEvent>
            {
                MakeEvent(1, "ORDER_INTENT", new { otherField = "x" }),
                MakeEvent(2, "FILL", new { otherField = "y" })
            };
            var ex = Assert.Throws<FormalTraceReconciliationException>(() =>
                FormalTraceReconciler.Reconcile(events));
            Assert.That(ex.Message, Does.Contain("orderId"));
        }

        [Test]
        public void Reconcile_NullEventsListRejected()
        {
            Assert.Throws<ArgumentNullException>(() =>
                FormalTraceReconciler.Reconcile(null));
        }

        [Test]
        public void Reconcile_IntegratedWithSinkValidChainPasses()
        {
            var p = NewTracePath();
            using (var sink = new FormalJsonlTraceSink(p))
            {
                sink.Write(MakeEvent(1, "ORDER_INTENT", new { orderId = 7 }));
                sink.Write(MakeEvent(2, "FILL", new { orderId = 7, fillPrice = 12.34m }));
                sink.Write(MakeEvent(3, "HOLDINGS_SNAPSHOT", new { orderId = 7, quantity = 100m }));
                sink.FlushAndReconcile();
            }

            var lines = File.ReadAllLines(p);
            Assert.That(lines.Length, Is.EqualTo(3));
            var events = lines
                .Where(l => !string.IsNullOrWhiteSpace(l))
                .Select(l => JsonConvert.DeserializeObject<FormalTraceEvent>(l))
                .ToList();
            Assert.DoesNotThrow(() => FormalTraceReconciler.Reconcile(events));
        }

        // ----------------------------------------------------------------
        // Helpers
        // ----------------------------------------------------------------

        private static FormalTraceEvent MakeEvent(long sequence, string eventType, object payload = null)
        {
            return new FormalTraceEvent(
                schemaVersion: "1",
                sequence: sequence,
                eventType: eventType,
                experimentId: "exp",
                windowId: "W1",
                stageId: "G0",
                runId: "run",
                candidateId: "c",
                eventTimeUtc: DateTime.UtcNow,
                payload: payload ?? new { value = 1m });
        }
    }
}
