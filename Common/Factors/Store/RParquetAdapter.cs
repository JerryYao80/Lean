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

        public RParquetAdapter(string factorRoot, string valueColumn,
                               string resultRoot = null,
                               Func<string, string, string, decimal?> readScalar = null)
        {
            _factorRoot = factorRoot;
            _valueColumn = valueColumn;
            _resultRoot = resultRoot ?? DefaultResultRoot();
            _readScalar = readScalar ?? DefaultPythonNetReader;
        }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var tsCode = SymbolToTsCode(symbol);
            var dateStr = date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            var factorDir = Path.Combine(_resultRoot, _factorRoot);

            // New layout: <factorRoot>/<date>.parquet (multi-row, filter by ts_code)
            var newPath = Path.Combine(factorDir, $"{dateStr}.parquet");
            result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Missing };

            decimal? v = null;
            try
            {
                if (File.Exists(newPath))
                {
                    v = _readScalar(newPath, _valueColumn, tsCode);
                }
                else
                {
                    // Fallback to old layout: <factorRoot>/<date>/<tsCode>.parquet
                    var oldPath = Path.Combine(factorDir, dateStr, $"{tsCode}.parquet");
                    if (File.Exists(oldPath))
                    {
                        v = _readScalar(oldPath, _valueColumn, tsCode);
                    }
                }
            }
            catch { return false; }

            if (!v.HasValue) return false;
            result = new FactorResult { Value = v.Value, RawValue = v.Value, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Valid };
            return true;
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
                    // Multi-row parquet: filter by ts_code column, then get value
                    dynamic mask = df["ts_code"] == tsCodeFilter;
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
    }
}
