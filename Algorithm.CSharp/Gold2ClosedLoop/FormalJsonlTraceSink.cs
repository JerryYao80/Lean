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
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;

namespace QuantConnect.Algorithm.CSharp.Gold2ClosedLoop
{
    /// <summary>
    /// Strict JSONL trace sink that writes one canonical JSON object per line to a
    /// fresh file opened with <see cref="FileMode.CreateNew"/>. It is thread-safe
    /// (writes are serialized under a lock), enforces strictly increasing 1-based
    /// sequences, and never overwrites existing evidence. <see cref="FlushAndReconcile"/>
    /// is the explicit proof gate that flushes both the text writer and the underlying
    /// <see cref="FileStream"/> (via <c>Flush(true)</c>) before reconciliation.
    /// </summary>
    /// <remarks>
    /// Spec §8 requires the trace sink to be a REQUIRED dependency: any write,
    /// flush, or reconciliation failure must surface as <c>FAILED_EVIDENCE_CAPTURE</c>.
    /// This implementation propagates errors rather than swallowing them, and it does
    /// not implicitly reconcile on <see cref="Dispose"/>: callers MUST call
    /// <see cref="FlushAndReconcile"/> explicitly when collecting formal evidence.
    /// </remarks>
    public sealed class FormalJsonlTraceSink : IFormalTraceSink
    {
        private readonly FileStream _fileStream;
        private readonly StreamWriter _streamWriter;
        private readonly object _lock = new();
        private readonly List<FormalTraceEvent> _writtenEvents = new();
        private long _lastSequence;
        private bool _disposed;

        /// <summary>
        /// Opens a fresh JSONL trace file at <paramref name="path"/> using
        /// <see cref="FileMode.CreateNew"/>. The parent directory must already exist
        /// (otherwise a <see cref="DirectoryNotFoundException"/> propagates). If the
        /// file already exists the open fails with <see cref="IOException"/> to prevent
        /// overwriting existing evidence.
        /// </summary>
        /// <param name="path">Absolute path to the JSONL trace file to create.</param>
        /// <exception cref="DirectoryNotFoundException">The parent directory does not exist.</exception>
        /// <exception cref="IOException">The target file already exists, or another I/O failure occurred.</exception>
        /// <exception cref="UnauthorizedAccessException">The target path is a directory or access is denied.</exception>
        public FormalJsonlTraceSink(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException("Trace path must be a non-empty string.", nameof(path));
            }

            // FileMode.CreateNew refuses to overwrite an existing file; if the file
            // is already there we fail loudly. If the parent directory is missing
            // the FileStream constructor throws DirectoryNotFoundException. If the
            // path resolves to a directory the constructor throws IOException or
            // UnauthorizedAccessException. None of these are swallowed.
            var fullPath = Path.GetFullPath(path);
            var parent = Path.GetDirectoryName(fullPath);
            if (string.IsNullOrEmpty(parent))
            {
                throw new ArgumentException(
                    "Trace path must have a non-empty parent directory.", nameof(path));
            }

            _fileStream = new FileStream(
                fullPath,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.Read,
                bufferSize: 4096,
                FileOptions.None);
            _streamWriter = new StreamWriter(_fileStream, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false))
            {
                AutoFlush = false
            };
        }

        /// <summary>
        /// Appends a single canonical JSON object on its own line. The sequence
        /// must equal <c>lastWrittenSequence + 1</c>, beginning at 1. Sequence
        /// gaps, duplicates, or zero as the first sequence are rejected without
        /// writing a partial line.
        /// </summary>
        /// <param name="traceEvent">The trace event to write.</param>
        /// <exception cref="InvalidOperationException">The sequence is out of order, duplicated, or does not start at 1.</exception>
        /// <exception cref="ObjectDisposedException">The sink has been disposed.</exception>
        /// <exception cref="IOException">An I/O failure occurred during the write.</exception>
        public void Write(FormalTraceEvent traceEvent)
        {
            if (traceEvent == null)
            {
                throw new ArgumentNullException(nameof(traceEvent));
            }

            lock (_lock)
            {
                ThrowIfDisposed();
                ValidateSequence(traceEvent);

                var json = JsonConvert.SerializeObject(
                    traceEvent,
                    Formatting.None,
                    JsonSerializerSettings);

                _streamWriter.Write(json);
                _streamWriter.Write('\n');
                _writtenEvents.Add(traceEvent);
                _lastSequence = traceEvent.Sequence;
            }
        }

        /// <summary>
        /// Flushes the text writer, calls <see cref="FileStream.Flush(bool)"/> with
        /// <c>flushToDisk: true</c> for durable file data, and then runs the in-memory
        /// order/fill/holdings reconciliation over every event written so far. This
        /// is the explicit proof gate; callers MUST invoke it before
        /// <see cref="Dispose"/> when collecting formal evidence.
        /// </summary>
        /// <exception cref="FormalTraceReconciliationException">A correlation invariant was violated.</exception>
        /// <exception cref="ObjectDisposedException">The sink has been disposed.</exception>
        /// <exception cref="IOException">An I/O failure occurred during the flush.</exception>
        public void FlushAndReconcile()
        {
            List<FormalTraceEvent> snapshot;
            lock (_lock)
            {
                ThrowIfDisposed();
                // Flush the buffered text writer to the FileStream first so that
                // the OS-level flush below actually sees all in-memory bytes.
                _streamWriter.Flush();
                // Flush the FileStream buffers through to the OS/durable storage.
                _fileStream.Flush(flushToDisk: true);
                snapshot = new List<FormalTraceEvent>(_writtenEvents);
            }

            // Reconciliation runs outside the writer lock to avoid holding it
            // during any allocation-heavy validation work; the snapshot is an
            // immutable copy of the events so far.
            FormalTraceReconciler.Reconcile(snapshot);
        }

        /// <summary>
        /// Disposes the underlying writer and stream. Does NOT implicitly reconcile:
        /// callers must invoke <see cref="FlushAndReconcile"/> explicitly to gate
        /// evidence collection. Dispose is idempotent and does not swallow errors
        /// raised by the underlying Dispose calls.
        /// </summary>
        public void Dispose()
        {
            lock (_lock)
            {
                if (_disposed)
                {
                    return;
                }
                _disposed = true;
            }

            // Disposing the StreamWriter also disposes the FileStream. Errors
            // propagate to the caller; we deliberately do not catch them here.
            _streamWriter.Dispose();
        }

        private void ValidateSequence(FormalTraceEvent traceEvent)
        {
            if (traceEvent.Sequence <= 0)
            {
                throw new InvalidOperationException(
                    $"Formal trace sequence must be strictly positive; got {traceEvent.Sequence}.");
            }

            var expected = _lastSequence + 1;
            if (traceEvent.Sequence != expected)
            {
                throw new InvalidOperationException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "Formal trace sequence out of order: expected {0} but received {1} " +
                        "(last written sequence was {2}). The invalid event was not written.",
                        expected,
                        traceEvent.Sequence,
                        _lastSequence));
            }
        }

        private void ThrowIfDisposed()
        {
            if (_disposed)
            {
                throw new ObjectDisposedException(
                    nameof(FormalJsonlTraceSink),
                    "The formal trace sink has been disposed.");
            }
        }

        /// <summary>
        /// The canonical <see cref="JsonSerializerSettings"/> used by the sink. It uses
        /// the default camelCase-to-PascalCase mapping that the LEAN codebase relies on
        /// elsewhere via <see cref="JsonProperty"/> PropertyName attributes; the snake_case
        /// field names are emitted explicitly by the <see cref="JsonProperty"/> attributes
        /// on <see cref="FormalTraceEvent"/>.
        /// </summary>
        private static readonly JsonSerializerSettings JsonSerializerSettings = new()
        {
            NullValueHandling = NullValueHandling.Include,
            DateFormatHandling = DateFormatHandling.IsoDateFormat,
            DateTimeZoneHandling = DateTimeZoneHandling.Utc,
            Culture = CultureInfo.InvariantCulture
        };
    }
}
