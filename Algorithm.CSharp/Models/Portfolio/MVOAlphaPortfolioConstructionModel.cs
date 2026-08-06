/*
 * MVOAlphaPortfolioConstructionModel.cs — JSON-driven MVO Portfolio Construction.
 *
 * Reads offline expanding-window MVO weight JSON (mvo_weights_YYYY-MM-DD.json)
 * produced by export_mvo_weights.py. On rebalance, loads the latest weights
 * <= rebalance date and emits PortfolioTarget.Percent per insight symbol.
 *
 * ZERO INTRUSION: new file. AlphaWeightedMVOPortfolioConstructionModel is NOT
 * modified. Weights come from the MVO JSON (not from insight.Magnitude).
 *
 * Mirrors ICWeightedAlphaModelV2's JSON loading pattern (latest <= asOf,
 * fallback to earliest).
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Logging;

namespace QuantConnect.Algorithm.CSharp.Models.Portfolio
{
    /// <summary>
    /// Portfolio construction model driven by offline MVO weight JSON files.
    /// </summary>
    public class MVOAlphaPortfolioConstructionModel : PortfolioConstructionModel
    {
        private readonly string _mvoWeightsDir;
        private readonly decimal _minWeight;
        private readonly Resolution _rebalanceResolution;

        private DateTime? _lastRebalanceTime;
        // Keyed by bare ticker (Symbol.Value), e.g. "600000" — exchange suffix stripped.
        private Dictionary<string, decimal> _weightsCache = new();
        private DateTime _weightsAsOf = DateTime.MinValue;

        public MVOAlphaPortfolioConstructionModel(
            string mvoWeightsDir,
            decimal minWeight = 0.0m,
            Resolution rebalanceResolution = Resolution.Daily)
        {
            _mvoWeightsDir = mvoWeightsDir ?? throw new ArgumentNullException(nameof(mvoWeightsDir));
            _minWeight = minWeight;
            _rebalanceResolution = rebalanceResolution;
        }

        public override List<PortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
        {
            var targets = new List<PortfolioTarget>();
            if (!ShouldRebalance(algorithm.Time)) return targets;
            if (insights.Length == 0) return targets;

            LoadLatestWeights(algorithm.Time);

            foreach (var insight in insights)
            {
                var ticker = insight.Symbol.Value;
                if (_weightsCache.TryGetValue(ticker, out var weight) && weight >= _minWeight)
                {
                    var t = PortfolioTarget.Percent(algorithm, insight.Symbol, weight);
                    if (t != null) targets.Add((PortfolioTarget)t);
                }
            }

            _lastRebalanceTime = algorithm.Time;
            return targets;
        }

        /// <summary>
        /// Scans _mvoWeightsDir for the newest mvo_weights_YYYY-MM-DD.json with
        /// date <= asOf. Falls back to the earliest if none <= asOf.
        /// </summary>
        private void LoadLatestWeights(DateTime asOf)
        {
            if (_weightsAsOf != DateTime.MinValue && _weightsAsOf >= asOf) return;

            if (!Directory.Exists(_mvoWeightsDir))
            {
                Log.Error($"[MVO-PCM] Weights dir not found: {_mvoWeightsDir}");
                return;
            }

            var files = Directory.GetFiles(_mvoWeightsDir, "mvo_weights_*.json");
            if (files.Length == 0)
            {
                Log.Error($"[MVO-PCM] No MVO weight files in {_mvoWeightsDir}");
                return;
            }

            MVOWeightFile latest = null;
            DateTime latestDate = DateTime.MinValue;
            MVOWeightFile earliest = null;
            DateTime earliestDate = DateTime.MaxValue;

            foreach (var file in files)
            {
                var name = Path.GetFileNameWithoutExtension(file);
                var dateStr = name.Substring("mvo_weights_".Length);
                if (!DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out var fileDate)) continue;

                MVOWeightFile parsed = null;
                try
                {
                    parsed = JsonConvert.DeserializeObject<MVOWeightFile>(File.ReadAllText(file));
                }
                catch (Exception ex)
                {
                    Log.Error($"[MVO-PCM] Failed to parse {file}: {ex.Message}");
                    continue;
                }

                if (fileDate < earliestDate) { earliestDate = fileDate; earliest = parsed; }
                if (fileDate <= asOf && fileDate > latestDate) { latestDate = fileDate; latest = parsed; }
            }

            var chosen = latest ?? earliest;
            if (chosen == null)
            {
                Log.Error($"[MVO-PCM] All {files.Length} weight file(s) failed to parse");
                return;
            }

            _weightsCache = new Dictionary<string, decimal>();
            foreach (var s in chosen.Symbols ?? new List<MVOWeightSymbol>())
            {
                // Strip exchange suffix (.SH/.SZ) to match Symbol.Value (bare ticker)
                var ticker = s.TsCode.Split('.')[0];
                _weightsCache[ticker] = (decimal)s.Weight;
            }
            _weightsAsOf = latest != null ? latestDate : earliestDate;
            if (latest == null)
            {
                Log.Trace($"[MVO-PCM] No weights <= {asOf:yyyy-MM-dd}; "
                          + $"falling back to earliest {_weightsAsOf:yyyy-MM-dd}");
            }
        }

        private bool ShouldRebalance(DateTime now)
        {
            if (_lastRebalanceTime == null) return true;
            return now.Date != _lastRebalanceTime.Value.Date;
        }

        // --- test hooks ---
        internal void LoadLatestWeightsForTest(DateTime asOf) { _weightsAsOf = DateTime.MinValue; LoadLatestWeights(asOf); }
        internal Dictionary<string, decimal> GetWeightsCacheForTest() => _weightsCache;
    }

    public class MVOWeightFile
    {
        [JsonProperty("as_of")] public string AsOf { get; set; }
        [JsonProperty("symbols")] public List<MVOWeightSymbol> Symbols { get; set; } = new();
        [JsonProperty("sum")] public double Sum { get; set; }
        [JsonProperty("lambda")] public double Lambda { get; set; }
        [JsonProperty("cov_window")] public int CovWindow { get; set; }
        [JsonProperty("n_assets")] public int NAssets { get; set; }
        [JsonProperty("ic_report_used")] public string IcReportUsed { get; set; }
        [JsonProperty("fallback")] public string Fallback { get; set; }
    }

    public class MVOWeightSymbol
    {
        [JsonProperty("ts_code")] public string TsCode { get; set; }
        [JsonProperty("weight")] public double Weight { get; set; }
    }
}
