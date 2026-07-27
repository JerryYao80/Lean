/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Alpha101CompositeAlphaModel — FIRST real FactorStore (Phase-2 pull) consumer.
 *
 * Monthly rebalance: for each of 8 alpha101 ids, pull cross-sectional values
 * across the active universe via FactorStore.Get, skip Quality==Missing,
 * two-pass z-score, apply per-alpha direction sign, average across available
 * alphas, long-only top N%.
 *
 * FactorStore is constructed ONCE in the ctor (auto RegisterDefaults); Get is
 * read-only and immutable post-ctor. RParquetAdapter does NOT forward-fill
 * (unlike RBarraAdapter) → Missing means "no parquet for this exact date";
 * ResolveFactorDate walks back to the previous trade day (cap 7 days).
 *
 * Test seam: an optional FactorStore ctor parameter lets tests inject a fake
 * store (overriding the 8 used alpha ids with deterministic readScalar delegates)
 * — production callers pass null (default) to get the real FactorStore with
 * pythonnet+pandas readers.
 */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// Composite long-only AlphaModel that pulls 8 alpha101 factor values per
    /// active security from FactorStore, z-scores them cross-sectionally, applies
    /// per-alpha direction signs, averages into a composite score, and emits
    /// Insight.Price for the top-N% names. Monthly rebalance.
    /// </summary>
    public class Alpha101CompositeAlphaModel : IAlphaModel
    {
        /// <summary>
        /// The 8 alpha101 factor ids used by the composite (selected for documented
        /// standalone IC per docs/all-option.md). Mirrors the canonical list in
        /// docs/superpowers/plans/2026-07-27-csi300-alpha101-composite-strategy.md.
        /// </summary>
        private static readonly string[] AlphaIds =
        {
            "alpha001", "alpha006", "alpha030", "alpha040",
            "alpha042", "alpha055", "alpha058", "alpha101",
        };

        /// <summary>
        /// +1 正向 / -1 反向 (sign-flip so high composite → long).
        /// Direction signs sourced from the alpha101 IC literature (alphas where
        /// high raw value predicts negative returns are flipped to -1).
        /// </summary>
        private static readonly Dictionary<string, int> DirectionSign = new()
        {
            {"alpha001", -1}, {"alpha006", -1}, {"alpha030", +1}, {"alpha040", -1},
            {"alpha042", -1}, {"alpha055", -1}, {"alpha058", -1}, {"alpha101", +1},
        };

        private readonly int _rebalanceMonths;
        private readonly int _insightPeriodDays;
        private readonly decimal _topQuantile;
        private readonly FactorStore _store;   // ctor once — immutable post-ctor

        private int _lastRebalanceYear = -1;
        private int _lastRebalanceMonth = -1;

        public string Name => "Alpha101Composite";

        /// <summary>
        /// Constructs the composite alpha model. If <paramref name="store"/> is
        /// null (production default), a new FactorStore is created — its ctor auto
        /// calls FactorStoreConfig.RegisterDefaults which registers the 101 alpha
        /// RParquetAdapters + barra + runtime + crowding. Tests pass a fake store
        /// that overrides the 8 used alphas with deterministic readScalar delegates.
        /// </summary>
        public Alpha101CompositeAlphaModel(
            int rebalanceMonths = 1,
            int insightPeriodDays = 21,
            decimal topQuantile = 0.10m,
            FactorStore store = null)
        {
            _rebalanceMonths = Math.Max(1, rebalanceMonths);
            _insightPeriodDays = Math.Max(1, insightPeriodDays);
            _topQuantile = topQuantile;
            _store = store ?? new FactorStore();   // ctor once — auto RegisterDefaults.
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            if (!IsRebalanceMonth(algorithm.Time))
                yield break;

            var symbols = algorithm.ActiveSecurities.Keys.ToList();
            if (symbols.Count == 0) yield break;

            // ResolveFactorDate needs a SecurityExchangeHours to walk back trade days.
            // The first active security's exchange hours is a representative A-share
            // calendar (SSE/SZSE share the same holiday schedule); if there are none
            // (empty universe edge case), fall through with a null probe — Update
            // returns early before ResolveFactorDate in that case.
            SecurityExchangeHours exchange = null;
            var firstSec = algorithm.ActiveSecurities.FirstOrDefault();
            if (firstSec.Value != null)
            {
                exchange = firstSec.Value.Exchange.Hours;
            }

            // When a test injects a fake FactorStore with deterministic readScalar
            // delegates, the adapter's TryGet ignores the date (it returns the value
            // regardless of which date is requested). So we can resolve the as-of
            // date to algorithm.Time directly when the injected fake store is in
            // place; for the production store we still need DateFolderExists to
            // walk back to the most recent factor-zoo date dir. The probe is cheap
            // and skips harmlessly if a fake adapter is registered.
            DateTime asOf = algorithm.Time;
            if (exchange != null)
            {
                var resolved = ResolveFactorDate(symbols[0], algorithm.Time, exchange);
                if (resolved == null)
                {
                    // Either no factor-zoo date dir exists on disk (production store,
                    // no parquet written for this date) OR a fake store is installed
                    // (its readScalar ignores the path so we don't actually need a
                    // real date dir). Distinguish by probing the store: call Get on
                    // alpha001 for the first symbol at algorithm.Time — if the fake
                    // returns Valid, use algorithm.Time; if real (Missing), hold.
                    var probe = _store.Get("alpha001", symbols[0], algorithm.Time);
                    if (probe.Quality != FactorDataQuality.Valid)
                    {
                        algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: no parquet; holding.");
                        yield break;
                    }
                    // Fake store in place — keep asOf = algorithm.Time.
                }
                else
                {
                    asOf = resolved.Value;
                }
            }

            var perSymbol = new Dictionary<Symbol, List<double>>();
            int alphasUsed = 0;
            foreach (var aid in AlphaIds)
            {
                var raw = new List<(Symbol sym, double v)>();
                foreach (var sym in symbols)
                {
                    var fr = _store.Get(aid, sym, asOf);
                    if (fr.Quality != FactorDataQuality.Valid) continue;
                    raw.Add((sym, (double)fr.Value));
                }

                // Need at least 2 valid values to compute a meaningful z-score;
                // also skip if all values identical (std==0 → division by zero).
                if (raw.Count < 2) continue;

                var mean = raw.Average(x => x.v);
                var std = Math.Sqrt(raw.Sum(x => (x.v - mean) * (x.v - mean)) / raw.Count);
                if (std < 1e-9) continue;

                int sign = DirectionSign[aid];
                foreach (var (sym, v) in raw)
                {
                    var z = sign * (v - mean) / std;
                    if (!perSymbol.TryGetValue(sym, out var list))
                        perSymbol[sym] = list = new List<double>();
                    list.Add(z);
                }

                alphasUsed++;
            }

            if (alphasUsed == 0 || perSymbol.Count == 0)
            {
                algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: all alphas empty; holding.");
                yield break;
            }

            var composite = perSymbol
                .Select(kv => (sym: kv.Key, score: kv.Value.Average()))
                .ToList();
            var ranked = composite.OrderByDescending(x => x.score).ToList();
            var n = Math.Max(1, (int)Math.Floor(ranked.Count * (double)_topQuantile));
            if (n > ranked.Count) n = ranked.Count;
            var selected = ranked.Take(n).ToList();

            var w = 1.0 / selected.Count;
            foreach (var x in selected)
            {
                yield return Insight.Price(
                    x.sym,
                    TimeSpan.FromDays(_insightPeriodDays),
                    InsightDirection.Up,
                    magnitude: x.score,
                    confidence: null,
                    sourceModel: Name,
                    weight: w);
            }

            algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: " +
                $"alphas_used={alphasUsed}/{AlphaIds.Length}, eligible={perSymbol.Count}, selected={selected.Count}.");

            _lastRebalanceYear = algorithm.Time.Year;
            _lastRebalanceMonth = algorithm.Time.Month;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        /// <summary>
        /// Walks back from the requested date to the most recent date for which the
        /// alpha001 factor-zoo date directory exists (cap 7 days). RParquetAdapter
        /// does not forward-fill, so Missing on date D means "no parquet written
        /// for D" — the previous trade day's parquet is the correct fallback. The
        /// directory existence probe is cheap (no Python, no parquet read).
        /// </summary>
        private DateTime? ResolveFactorDate(Symbol probe, DateTime requested, SecurityExchangeHours hours)
        {
            var d = requested.Date;
            for (int i = 0; i < 7; i++)
            {
                if (DateFolderExists("alpha001", d)) return d;
                if (hours == null) return null;
                try
                {
                    d = hours.GetPreviousTradingDay(d);
                }
                catch
                {
                    // GetPreviousTradingDay throws if it can't find a trading day
                    // within a week (e.g. dates outside the calendar's valid range).
                    return null;
                }
            }
            return null;
        }

        private static bool DateFolderExists(string alphaId, DateTime d)
        {
            // Mirror RParquetAdapter.DefaultResultRoot: result/ is a sibling of
            // Data/ (one ".." up from Globals.DataFolder). Probe the alphaNNN
            // date directory directly — no Python, no parquet read.
            var root = Path.Combine(Globals.DataFolder, "..", "result", "factor-zoo", alphaId);
            return Directory.Exists(Path.Combine(root, d.ToString("yyyy-MM-dd")));
        }

        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true;
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }
    }
}
