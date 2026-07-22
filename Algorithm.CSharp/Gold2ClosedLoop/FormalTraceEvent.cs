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
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Algorithm.CSharp.Gold2ClosedLoop
{
    /// <summary>
    /// Versioned formal trace event DTO for the Gold2 closed-loop proof pipeline.
    /// Each record carries the full identity tuple (experiment/window/stage/run/candidate)
    /// required by later Python validation (spec §8). Decimal values in the payload
    /// are preserved through Newtonsoft.Json serialization without binary float drift
    /// so that prices, quantities and fees are reproducible across language boundaries.
    /// </summary>
    /// <remarks>
    /// The payload is stored as a <see cref="JToken"/> so that decimal literals written
    /// by the source (e.g. <c>12.34m</c>) round-trip as exact decimal text rather than
    /// being coerced to <c>double</c>. The public constructor accepts any caller-supplied
    /// object; canonical serialization uses <see cref="JsonSerializer"/> with
    /// <see cref="JsonConvert.DefaultSettings"/>-equivalent behavior so the snake_case
    /// JSON property names below are emitted deterministically.
    /// </remarks>
    public sealed class FormalTraceEvent
    {
        /// <summary>
        /// Initializes a new instance of the <see cref="FormalTraceEvent"/> class.
        /// </summary>
        /// <param name="schemaVersion">Versioned schema tag (e.g. "1"). Required.</param>
        /// <param name="sequence">Strictly increasing 1-based sequence number. Must equal lastSequence + 1 when written via <see cref="IFormalTraceSink"/>.</param>
        /// <param name="eventType">Event type from the closed enum: DECISION, ORDER_INTENT, FILL, HOLDINGS_SNAPSHOT.</param>
        /// <param name="experimentId">Experiment identifier. Required.</param>
        /// <param name="windowId">Window identifier (e.g. "W1".."W4"). Required.</param>
        /// <param name="stageId">Stage identifier (e.g. "G0".."G3"). Required.</param>
        /// <param name="runId">LEAN run identifier. Required.</param>
        /// <param name="candidateId">Candidate identifier. Required.</param>
        /// <param name="eventTimeUtc">UTC event time. Must be UTC; callers are responsible for converting algorithm-local time.</param>
        /// <param name="payload">Caller-supplied payload object. Decimal values are preserved exactly. May be null only when the schema explicitly permits it for the event type.</param>
        public FormalTraceEvent(
            string schemaVersion,
            long sequence,
            string eventType,
            string experimentId,
            string windowId,
            string stageId,
            string runId,
            string candidateId,
            DateTime eventTimeUtc,
            object payload)
        {
            SchemaVersion = schemaVersion ?? throw new ArgumentNullException(nameof(schemaVersion));
            Sequence = sequence;
            EventType = eventType ?? throw new ArgumentNullException(nameof(eventType));
            ExperimentId = experimentId ?? throw new ArgumentNullException(nameof(experimentId));
            WindowId = windowId ?? throw new ArgumentNullException(nameof(windowId));
            StageId = stageId ?? throw new ArgumentNullException(nameof(stageId));
            RunId = runId ?? throw new ArgumentNullException(nameof(runId));
            CandidateId = candidateId ?? throw new ArgumentNullException(nameof(candidateId));
            EventTimeUtc = eventTimeUtc;
            Payload = payload == null
                ? null
                : JToken.FromObject(payload, JsonSerializer.CreateDefault());
        }

        /// <summary>
        /// Private constructor for JSON deserialization. Newtonsoft.Json populates
        /// the get-only properties via the property setters exposed by the
        /// <see cref="JsonProperty"/> attributes.
        /// </summary>
        [JsonConstructor]
        private FormalTraceEvent()
        {
            SchemaVersion = string.Empty;
            EventType = string.Empty;
            ExperimentId = string.Empty;
            WindowId = string.Empty;
            StageId = string.Empty;
            RunId = string.Empty;
            CandidateId = string.Empty;
        }

        /// <summary>
        /// Versioned schema tag. Bumping this value forces formal loaders to reject
        /// mismatched traces instead of silently coercing them.
        /// </summary>
        [JsonProperty(PropertyName = "schema_version", Required = Required.Always)]
        public string SchemaVersion { get; private set; }

        /// <summary>
        /// Strictly increasing 1-based sequence number. The sink rejects gaps,
        /// duplicates, and a first sequence other than 1.
        /// </summary>
        [JsonProperty(PropertyName = "sequence", Required = Required.Always)]
        public long Sequence { get; private set; }

        /// <summary>
        /// Event type from the closed enum. The reconciler enforces ORDER_INTENT
        /// before FILL before HOLDINGS_SNAPSHOT correlation.
        /// </summary>
        [JsonProperty(PropertyName = "event_type", Required = Required.Always)]
        public string EventType { get; private set; }

        /// <summary>
        /// Experiment identifier.
        /// </summary>
        [JsonProperty(PropertyName = "experiment_id", Required = Required.Always)]
        public string ExperimentId { get; private set; }

        /// <summary>
        /// Window identifier (W1..W4).
        /// </summary>
        [JsonProperty(PropertyName = "window_id", Required = Required.Always)]
        public string WindowId { get; private set; }

        /// <summary>
        /// Stage identifier (G0..G3).
        /// </summary>
        [JsonProperty(PropertyName = "stage_id", Required = Required.Always)]
        public string StageId { get; private set; }

        /// <summary>
        /// LEAN run identifier.
        /// </summary>
        [JsonProperty(PropertyName = "run_id", Required = Required.Always)]
        public string RunId { get; private set; }

        /// <summary>
        /// Candidate identifier.
        /// </summary>
        [JsonProperty(PropertyName = "candidate_id", Required = Required.Always)]
        public string CandidateId { get; private set; }

        /// <summary>
        /// UTC event time. Serialized in ISO 8601 round-trip format.
        /// </summary>
        [JsonProperty(PropertyName = "event_time_utc", Required = Required.Always)]
        public DateTime EventTimeUtc { get; private set; }

        /// <summary>
        /// Caller-supplied payload stored as a <see cref="JToken"/> so that decimal
        /// literals are preserved exactly across serialization boundaries. Use
        /// <see cref="PayloadAsJObject"/> to fetch it as a <see cref="JObject"/>.
        /// </summary>
        [JsonProperty(PropertyName = "payload", Required = Required.AllowNull)]
        public JToken Payload { get; private set; }

        /// <summary>
        /// Returns the payload as a <see cref="JObject"/>, or null if the payload
        /// is null or not an object. Used by the reconciler to extract correlation
        /// fields such as <c>orderId</c>.
        /// </summary>
        /// <returns>The payload as a <see cref="JObject"/>, or null.</returns>
        public JObject PayloadAsJObject() => Payload as JObject;
    }
}
