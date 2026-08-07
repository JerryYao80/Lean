/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter for parquet-scalar factors.
 * Uses the EXISTING pythonnet+pandas bridge (Py.GIL + pd.read_parquet) — the
 * repo's established parquet-read mechanism, zero new deps. Mirrors
 * Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs:130-184.
 *
 * The scalar-read is behind a delegate seam so unit tests inject a fake (no Python
 * needed); the default delegate uses pythonnet+pandas at runtime.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using Python.Runtime;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Logging;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Reads one parquet scalar for (symbol, date) from
    /// {resultRoot}/{factorRoot}/{date:yyyy-MM-dd}/{ts_code}.parquet[column].
    /// Owns: crowding (result/crowding-factor/, col composite) + Phase 5 new factors.
    /// </summary>
    public class RParquetAdapter : IFactorAdapter
    {
        private readonly string _factorRoot;
        private readonly string _valueColumn;
        private readonly string _resultRoot;
        private readonly Func<string, string, string, decimal?> _readScalar;

        // Per-(date path) cache of {ts_code -> value}. Without this, each rebalance
        // day calls Store.Get 300 symbols × N factors times, and every call re-acquires
        // Py.GIL() + pd.read_parquet on the SAME <date>.parquet file (300× redundant
        // reads per factor per day → 864K pythonnet calls over 60-day warmup, making
        // the backtest intractable at ~90s/day). With the cache, each unique parquet
        // is read ONCE into a C# Dictionary and subsequent symbol lookups are pure C#
        // (no GIL, no IO). Eviction is LRU-bounded to avoid unbounded memory growth
        // across a long backtest.
        private readonly Dictionary<string, Dictionary<string, decimal>> _panelCache = new();
        private readonly int _maxCacheEntries = 64;
        private readonly Func<string, Dictionary<string, decimal>> _readAll;

        public RParquetAdapter(string factorRoot, string valueColumn,
                               string resultRoot = null,
                               Func<string, string, string, decimal?> readScalar = null)
        {
            _factorRoot = factorRoot;
            _valueColumn = valueColumn;
            _resultRoot = resultRoot ?? DefaultResultRoot();
            _readScalar = readScalar ?? DefaultPythonNetReader;
            // When a fake readScalar is injected (unit tests), _readAll is unused;
            // production uses the real reader. Keep them independent so tests that
            // only inject readScalar still work via the TryGet scalar fallback.
            _readAll = DefaultReadAllToDict;
        }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var tsCode = SymbolToTsCode(symbol);
            var dateStr = date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            var factorDir = Path.Combine(_resultRoot, _factorRoot);

            // New layout: <factorRoot>/<date>.parquet (multi-row, filter by ts_code)
            var newPath = Path.Combine(factorDir, $"{dateStr}.parquet");
            result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Missing };

            // Fast path: cache hit → pure C# dict lookup (no GIL, no IO).
            // This is what makes a 300-symbol × 58-factor rebalance day tractable:
            // 58 parquet reads (one per factor) instead of 17,400.
            Dictionary<string, decimal> panel = null;
            try
            {
                if (File.Exists(newPath))
                {
                    panel = GetOrLoadPanel(newPath);
                    if (panel != null && panel.TryGetValue(tsCode, out var cached))
                    {
                        result = new FactorResult { Value = cached, RawValue = cached, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Valid };
                        return true;
                    }
                    // Panel loaded but ts_code absent → Missing.
                    return false;
                }

                // Fallback to old layout: <factorRoot>/<date>/<tsCode>.parquet (single-row,
                // one file per ts_code — caching the whole dir is uneconomic, read scalar).
                var oldPath = Path.Combine(factorDir, dateStr, $"{tsCode}.parquet");
                if (File.Exists(oldPath))
                {
                    var v = _readScalar(oldPath, _valueColumn, tsCode);
                    if (v.HasValue)
                    {
                        result = new FactorResult { Value = v.Value, RawValue = v.Value, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Valid };
                        return true;
                    }
                }
            }
            catch (Exception ex) { Log.Error($"[RParquetAdapter] read failed path={newPath} tsCode={tsCode} col={_valueColumn}: {ex.Message}"); return false; }

            return false;
        }

        /// <summary>
        /// Get the cached {ts_code -> value} panel for a parquet path, or load it once.
        /// The dict only contains ts_codes that have a row in the parquet; a ts_code
        /// absent from the dict means that stock had no factor value that day (Missing).
        /// LRU-evicts the oldest entry when the cache exceeds _maxCacheEntries, keeping
        /// memory bounded across a long backtest (only recent dates retained).
        /// </summary>
        private Dictionary<string, decimal> GetOrLoadPanel(string path)
        {
            if (_panelCache.TryGetValue(path, out var hit)) return hit;
            var loaded = _readAll(path);
            if (_panelCache.Count >= _maxCacheEntries)
            {
                // Evict one oldest entry (Dictionary preserves insertion order on .NET).
                using var en = _panelCache.Keys.GetEnumerator();
                if (en.MoveNext()) _panelCache.Remove(en.Current);
            }
            _panelCache[path] = loaded;
            return loaded;
        }

        /// <summary>Expose roots for FactorStore.FreshnessReport (read-only).</summary>
        public (string FactorRoot, string ResultRoot) Describe() => (_factorRoot, _resultRoot);

        /// <summary>A-share Symbol -> tushare ts_code. Mirrors CrowdingFactorZooAlphaModel.SymbolToTsCode:
        /// 6-prefix (SH main board) or 51-prefix (SH ETF: 510xxx-518xxx) -> .SH; else .SZ.</summary>
        internal static string SymbolToTsCode(Symbol symbol)
        {
            var ticker = symbol.ID.Symbol;
            return (ticker.StartsWith("6") || ticker.StartsWith("51")) ? $"{ticker}.SH" : $"{ticker}.SZ";
        }

        private static string DefaultResultRoot()
        {
            // result/ is a sibling of Data/ under the Lean root (one level up from DataFolder).
            // Two ".." would escape to /home/project/hope/result (nonexistent); one ".." lands on Lean/result.
            try { return Path.GetFullPath(Path.Combine(Globals.DataFolder, "..", "result")); }
            catch { return "result"; }
        }

        /// <summary>
        /// Default reader: pythonnet + pandas read_parquet.
        /// With tsCodeFilter: reads multi-row parquet and filters by ts_code column.
        /// Without tsCodeFilter: reads single-row parquet and extracts the value directly.
        /// Mirrors CrowdingFactorZooAlphaModel.cs:130-184 (Py.GIL + Py.Import("pandas")
        /// + pd.read_parquet + df[column].iloc[0] + (double) cast). Returns null on
        /// missing file / empty frame / NaN — never throws (caller maps to Missing).
        /// </summary>
        private static decimal? DefaultPythonNetReader(string path, string column, string tsCodeFilter = null)
        {
            if (!File.Exists(path)) return null;
            using (Py.GIL())
            {
                dynamic pd = Py.Import("pandas");
                dynamic df = pd.read_parquet(path);
                if (df == null || (int)df.__len__() == 0) return null;

                dynamic val;
                if (!string.IsNullOrEmpty(tsCodeFilter))
                {
                    // Multi-row parquet: filter by ts_code column, then get value.
                    // NOTE: must use pandas Series.eq() method, NOT C# '==' operator.
                    // C# dynamic 'df["ts_code"] == str' raises RuntimeBinderException
                    // (== does not route to Python __eq__ via pythonnet dynamic),
                    // which the catch block silently swallowed → every Store.Get
                    // returned Missing → 0 orders. .eq() is a method call (dynamic
                    // InvokeMember → Python call) and works correctly.
                    dynamic tsCol = df["ts_code"];
                    dynamic mask = tsCol.eq(tsCodeFilter);
                    dynamic filtered = df[mask];
                    if ((int)filtered.__len__() == 0) return null;
                    val = filtered[column].iloc[0];
                }
                else
                {
                    // Single-row parquet (old format): direct extraction
                    val = df[column].iloc[0];
                }

                if (val == null) return null;
                try { return Convert.ToDecimal((double)val, CultureInfo.InvariantCulture); }
                catch { return null; }
            }
        }

        /// <summary>
        /// Read the ENTIRE parquet into a C# {ts_code -> value} dict in ONE GIL
        /// acquisition. This is the perf-critical path: a rebalance day pulls 300
        /// symbols × N factors, and caching the whole panel per (path) collapses
        /// 300 pythonnet reads per factor down to 1. Iteration uses df.itertuples()
        /// (fast, no Python-level row indexing per call) and converts each value to
        /// decimal once. Returns an empty dict (not null) on missing/empty file so
        /// the caller's TryGetValue simply reports Missing for every ts_code.
        /// </summary>
        private Dictionary<string, decimal> DefaultReadAllToDict(string path)
        {
            var dict = new Dictionary<string, decimal>();
            if (!File.Exists(path)) return dict;
            using (Py.GIL())
            {
                dynamic pd = Py.Import("pandas");
                dynamic df = pd.read_parquet(path);
                if (df == null || (int)df.__len__() == 0) return dict;

                // itertuples(index=False, name=None) yields plain tuples (ts_code, value)
                // in column order — fastest row iteration in pandas.
                dynamic rows = df.itertuples(index: false, name: null);
                foreach (var row in rows)
                {
                    // row is a PyTuple: [0]=ts_code, [1]=value
                    string tsCode = (string)(dynamic)row[0];
                    try
                    {
                        double v = (double)(dynamic)row[1];
                        if (!double.IsNaN(v)) dict[tsCode] = Convert.ToDecimal(v, CultureInfo.InvariantCulture);
                    }
                    catch { /* NaN/None → skip this ts_code (Missing) */ }
                }
            }
            return dict;
        }
    }
}
