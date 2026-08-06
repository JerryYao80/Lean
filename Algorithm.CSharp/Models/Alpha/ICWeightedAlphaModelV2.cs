/*
 * ICWeightedAlphaModelV2.cs — IC-Weighted Alpha Model V2 (JSON-driven)
 *
 * Replaces the hardcoded 49-factor list in ICWeightedAlphaModel with a dynamic
 * model reading offline expanding-window IC reports from JSON.
 *
 * ZERO INTRUSION: new file. Old ICWeightedAlphaModel is not modified.
 *
 * Flow:
 *   1. On rebalance, LoadLatestReport(date) scans icReportDir for the newest
 *      ic_report_YYYY-MM-DD.json with date <= rebalance date.
 *   2. Deserialize factors + weights via Newtonsoft.Json.
 *   3. For each active security, pull each factor via FactorStore.Get and
 *      compute a weighted composite score (cross-sectional normalization,
 *      same logic as ICWeightedAlphaModel.ComputeAlphaScores).
 *   4. Select top quantile, emit Price insights (weight = 1/n).
 *
 * Fallbacks: missing dir, no report <= date, corrupt JSON, all-invalid factors
 * -> equal-weight fallback or no insight, with logs. Never crashes.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Logging;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// IC-weighted alpha model V2: reads offline expanding-window IC reports
    /// (JSON) and computes a weighted composite alpha score per security.
    /// </summary>
    public class ICWeightedAlphaModelV2 : IAlphaModel
    {
        private readonly string _icReportDir;
        private readonly int _rebalanceMonths;
        private readonly int _insightPeriodDays;
        private readonly decimal _topQuantile;
        private FactorStore _store;
        private readonly Func<FactorStore> _storeFactory;

        private int _lastRebalanceYear = -1;
        private int _lastRebalanceMonth = -1;

        public string Name => "ICWeightedAlphaV2";

        public ICWeightedAlphaModelV2(
            string icReportDir,
            int rebalanceMonths = 1,
            int insightPeriodDays = 21,
            decimal topQuantile = 0.10m,
            FactorStore store = null)
        {
            _icReportDir = icReportDir ?? throw new ArgumentNullException(nameof(icReportDir));
            _rebalanceMonths = rebalanceMonths;
            _insightPeriodDays = insightPeriodDays;
            _topQuantile = topQuantile;
            // Lazy-init: defer new FactorStore() (which calls FactorStoreConfig.RegisterDefaults
            // and may touch Python.NET) until Update() actually needs it. This keeps JSON-only
            // operations like LoadLatestReport() cheap and free of Python.NET side effects,
            // which is critical for unit tests that only exercise the report-loading path.
            _store = store;
            _storeFactory = store != null ? () => store : () => new FactorStore();
        }

        private FactorStore Store => _store ??= _storeFactory();

        /// <summary>
        /// Scans _icReportDir for the newest ic_report_YYYY-MM-DD.json with
        /// report_date <= asOf. Falls back to the earliest report if none <= asOf.
        /// Returns null on missing dir / no parseable reports.
        /// </summary>
        public ICReport LoadLatestReport(DateTime asOf)
        {
            if (!Directory.Exists(_icReportDir))
            {
                Log.Error($"[ICWeightedAlphaV2] IC report dir not found: {_icReportDir}");
                return null;
            }

            var files = Directory.GetFiles(_icReportDir, "ic_report_*.json");
            if (files.Length == 0)
            {
                Log.Error($"[ICWeightedAlphaV2] No IC reports in {_icReportDir}");
                return null;
            }

            ICReport latest = null;
            DateTime latestDate = DateTime.MinValue;
            ICReport earliest = null;
            DateTime earliestDate = DateTime.MaxValue;

            foreach (var file in files)
            {
                var name = Path.GetFileNameWithoutExtension(file);
                var dateStr = name.Substring("ic_report_".Length);
                if (!DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out var reportDate)) continue;

                ICReport parsed = null;
                try
                {
                    parsed = JsonConvert.DeserializeObject<ICReport>(File.ReadAllText(file));
                }
                catch (Exception ex)
                {
                    Log.Error($"[ICWeightedAlphaV2] Failed to parse {file}: {ex.Message}");
                    continue;
                }

                if (reportDate < earliestDate)
                {
                    earliestDate = reportDate;
                    earliest = parsed;
                }
                if (reportDate <= asOf && reportDate > latestDate)
                {
                    latestDate = reportDate;
                    latest = parsed;
                }
            }

            if (latest != null) return latest;
            if (earliest != null)
            {
                Log.Trace($"[ICWeightedAlphaV2] No report <= {asOf:yyyy-MM-dd}; "
                          + $"falling back to earliest {earliestDate:yyyy-MM-dd}");
                return earliest;
            }
            return null;
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            if (!IsRebalanceMonth(algorithm.Time)) yield break;

            var symbols = algorithm.ActiveSecurities.Keys.ToList();
            if (symbols.Count == 0) yield break;

            var report = LoadLatestReport(algorithm.Time);
            if (report == null || report.Factors == null || report.Factors.Count == 0)
            {
                algorithm.Debug($"[ICWeightedAlphaV2] {algorithm.Time:yyyy-MM-dd}: no IC report, skipping");
                yield break;
            }

            var dateStr = algorithm.Time.ToString("yyyy-MM-dd");
            var alphaScores = ComputeAlphaScores(symbols, algorithm.Time, report);

            if (alphaScores.Count == 0)
            {
                algorithm.Debug($"[ICWeightedAlphaV2] {dateStr}: no alpha scores computed");
                yield break;
            }

            var ranked = alphaScores.OrderByDescending(x => x.Value).ToList();
            var n = Math.Max(1, (int)Math.Floor(ranked.Count * (double)_topQuantile));
            var selected = ranked.Take(n).ToList();

            var w = 1.0 / selected.Count;
            foreach (var (symbol, score) in selected)
            {
                yield return Insight.Price(
                    symbol,
                    TimeSpan.FromDays(_insightPeriodDays),
                    InsightDirection.Up,
                    magnitude: (double)score,
                    confidence: null,
                    sourceModel: Name,
                    weight: w);
            }

            algorithm.Debug($"[ICWeightedAlphaV2] {dateStr}: selected={selected.Count}, " +
                            $"factors={report.Factors.Count}, report={report.ReportDate}");

            _lastRebalanceYear = algorithm.Time.Year;
            _lastRebalanceMonth = algorithm.Time.Month;
        }

        private Dictionary<Symbol, double> ComputeAlphaScores(
            List<Symbol> symbols, DateTime asOf, ICReport report)
        {
            var scores = new Dictionary<Symbol, double>();

            foreach (var symbol in symbols)
            {
                double compositeScore = 0;
                double totalWeight = 0;

                foreach (var f in report.Factors)
                {
                    var result = Store.Get(f.FactorId, symbol, asOf);
                    if (result.Quality == FactorDataQuality.Valid)
                    {
                        compositeScore += (double)result.Value * f.Weight;
                        totalWeight += f.Weight;
                    }
                }

                if (totalWeight > 0)
                    scores[symbol] = compositeScore / totalWeight;
            }

            // Normalize to [0, 1] (same logic as ICWeightedAlphaModel.ComputeAlphaScores)
            if (scores.Count > 0)
            {
                var min = scores.Values.Min();
                var max = scores.Values.Max();
                if (max > min)
                {
                    foreach (var symbol in scores.Keys.ToList())
                        scores[symbol] = (scores[symbol] - min) / (max - min);
                }
            }

            return scores;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true;
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }
    }

    /// <summary>
    /// Offline expanding-window IC report produced by the Python export script.
    /// Field names match the JSON schema exactly (snake_case).
    /// </summary>
    public class ICReport
    {
        [JsonProperty("report_date")] public string ReportDate { get; set; }
        [JsonProperty("ic_window_start")] public string IcWindowStart { get; set; }
        [JsonProperty("ic_window_end")] public string IcWindowEnd { get; set; }
        [JsonProperty("horizon")] public int Horizon { get; set; }
        [JsonProperty("min_ic")] public double MinIc { get; set; }
        [JsonProperty("min_ir")] public double MinIr { get; set; }
        [JsonProperty("factors")] public List<ICReportFactor> Factors { get; set; } = new();
    }

    /// <summary>
    /// Per-factor IC stats + weight inside an ICReport.
    /// </summary>
    public class ICReportFactor
    {
        [JsonProperty("factor_id")] public string FactorId { get; set; }
        [JsonProperty("rank_ic_mean")] public double RankIcMean { get; set; }
        [JsonProperty("rank_ic_ir")] public double RankIcIr { get; set; }
        [JsonProperty("weight")] public double Weight { get; set; }
    }
}
