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
using Newtonsoft.Json;

namespace QuantConnect.Data
{
    /// <summary>
    /// Records which data source was used for each symbol during a backtest
    /// </summary>
    public class DataProvenanceEntry
    {
        public string TsCode { get; set; }
        public string Source { get; set; }
        public int BarCount { get; set; }
        public DateTime StartDateUtc { get; set; }
        public DateTime EndDateUtc { get; set; }
        public DateTime RecordedAtUtc { get; set; }
    }

    /// <summary>
    /// Thread-safe accumulator of per-symbol provenance entries during a backtest
    /// </summary>
    public class DataProvenanceTracker
    {
        public static DataProvenanceTracker Current { get; set; }

        private readonly ConcurrentDictionary<string, DataProvenanceEntry> _entries = new(StringComparer.OrdinalIgnoreCase);

        public void Record(string tsCode, string source, int barCount, DateTime start, DateTime end)
        {
            var entry = new DataProvenanceEntry
            {
                TsCode = tsCode,
                Source = source,
                BarCount = barCount,
                StartDateUtc = start,
                EndDateUtc = end,
                RecordedAtUtc = DateTime.UtcNow
            };
            _entries[tsCode] = entry;
        }

        public IReadOnlyDictionary<string, DataProvenanceEntry> GetEntries()
        {
            return new Dictionary<string, DataProvenanceEntry>(_entries, StringComparer.OrdinalIgnoreCase);
        }

        public string ToJson()
        {
            return JsonConvert.SerializeObject(_entries, Formatting.None);
        }
    }
}