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
using System.IO;
using System.Linq;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Securities;
using Python.Runtime;

namespace QuantConnect.Algorithm.CSharp.UniverseSelection
{
    /// <summary>
    /// CSI300 (沪深300) universe selection model for A-share algorithms.
    ///
    /// Reuses <c>data-source/tushare/barra_cne5_data_loader.py:BarraCNE5DataLoader.load_index_constituents</c>
    /// via pythonnet to read the <c>index_weight</c> parquet for index code <c>000300.SH</c>
    /// and return the constituent <c>ts_code</c> list as of a given date.
    ///
    /// ts_code → Symbol conversion mirrors <c>ChipPeakFactorZooAlphaModel.SymbolToTsCode</c>
    /// and <c>AShareBarraCNE5Algorithm.TryResolveSymbol</c>: suffix <c>.SH</c> → <c>Market.SSE</c>,
    /// <c>.SZ</c> → <c>Market.SZSE</c> (ticker keeps the digits, e.g. <c>600519.SH</c> → <c>600519</c> SSE).
    ///
    /// Monthly refresh: <see cref="GetNextRefreshTimeUtc"/> advances by <c>refreshMonths</c>
    /// (default 1) after each <see cref="CreateUniverses"/> call, so LEAN re-invokes us on
    /// the schedule and we reload constituents (CSI300 membership drifts slowly).
    ///
    /// Pythonnet init mirrors <c>ChipPeakFactorZooAlphaModel.InitializePython</c>:
    /// <c>Py.GIL</c> + <c>importlib.util.spec_from_file_location</c> + <c>sys.path.insert</c>
    /// for both repo root and <c>data-source/tushare</c> (the loader imports pandas etc.).
    /// </summary>
    public class AShareCSI300UniverseSelectionModel : UniverseSelectionModel
    {
        private const string DefaultDataRoot = "/home/project/tushare-downloader/tushare_data_v2";
        private const string DefaultIndexCode = "000300.SH";

        private readonly string _dataRoot;
        private readonly string _rootPath;
        private readonly string _indexCode;
        private readonly int _refreshMonths;
        private readonly string _asofDate;

        private dynamic _loader;
        private bool _pythonInitialized;
        private List<Universe> _cachedUniverses;
        private DateTime _nextRefreshUtc = DateTime.MinValue;

        public AShareCSI300UniverseSelectionModel(
            string dataRoot = DefaultDataRoot,
            string rootPath = null,
            string indexCode = DefaultIndexCode,
            int refreshMonths = 1,
            string asofDate = null)
        {
            _dataRoot = dataRoot ?? DefaultDataRoot;
            _rootPath = rootPath ?? Directory.GetParent(Globals.DataFolder)?.FullName
                ?? "/home/project/hope/Lean";
            _indexCode = string.IsNullOrWhiteSpace(indexCode) ? DefaultIndexCode : indexCode;
            _refreshMonths = Math.Max(1, refreshMonths);
            _asofDate = asofDate;
        }

        /// <summary>
        /// Next time LEAN should re-invoke <see cref="CreateUniverses"/>. Initially
        /// <c>DateTime.UtcNow</c> (immediate), then advanced by <c>_refreshMonths</c>
        /// after each refresh.
        /// </summary>
        public override DateTime GetNextRefreshTimeUtc()
        {
            if (_nextRefreshUtc == DateTime.MinValue)
            {
                _nextRefreshUtc = DateTime.UtcNow;
            }
            return _nextRefreshUtc;
        }

        public override IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
        {
            if (_cachedUniverses == null || algorithm.UtcTime >= _nextRefreshUtc)
            {
                var symbols = LoadConstituentSymbols(algorithm);
                var asofLabel = _asofDate ?? algorithm.Time.ToString("yyyyMMdd");
                if (symbols.Count == 0)
                {
                    algorithm.Debug(
                        $"[AShareCSI300Universe] no constituents returned for index_code={_indexCode} " +
                        $"asof={asofLabel}; universe empty.");
                }
                else
                {
                    algorithm.Log(
                        $"[AShareCSI300Universe] loaded {symbols.Count} constituents " +
                        $"from {_indexCode} (asof={asofLabel}).");
                }

                // Delegate to ManualUniverseSelectionModel for the universe yield
                // (groups symbols by market/security type, builds ManualUniverse per group,
                // handles SubscriptionDataConfig + MarketHoursDatabase). Zero-intrusion reuse
                // of LEAN-native machinery — no LEAN core edits.
                var inner = new ManualUniverseSelectionModel(symbols);
                _cachedUniverses = inner.CreateUniverses(algorithm).ToList();
                _nextRefreshUtc = algorithm.UtcTime.AddMonths(_refreshMonths);
            }
            return _cachedUniverses;
        }

        private List<Symbol> LoadConstituentSymbols(QCAlgorithm algorithm)
        {
            InitializePython(algorithm);

            var asof = _asofDate ?? algorithm.Time.ToString("yyyyMMdd");
            var tsCodes = new List<string>();
            using (Py.GIL())
            {
                dynamic result = _loader.load_index_constituents(
                    asof_date: asof, index_code: _indexCode);
                if (result == null || result.IsNone()) return new List<Symbol>();

                foreach (var item in result)
                {
                    var code = item.As<string>();
                    if (!string.IsNullOrEmpty(code)) tsCodes.Add(code);
                }
            }

            var symbols = new List<Symbol>(tsCodes.Count);
            foreach (var tsCode in tsCodes)
            {
                if (TryConvertTsCodeToSymbol(tsCode, out var sym))
                {
                    symbols.Add(sym);
                }
                else
                {
                    algorithm.Debug(
                        $"[AShareCSI300Universe] skipping malformed ts_code='{tsCode}'.");
                }
            }
            return symbols;
        }

        private void InitializePython(QCAlgorithm algorithm)
        {
            if (_pythonInitialized) return;
            using (Py.GIL())
            {
                var sys = Py.Import("sys");
                dynamic path = sys.GetAttr("path");
                var root = _rootPath;

                var rootPy = root.ToPython();
                if (!path.__contains__(rootPy).__bool__())
                {
                    // sys.path is a Python list — call its .insert() method directly
                    // (pythonnet dispatches list.insert(0, item) natively). The previous
                    // path.invoke("insert", ...) form treated `path` as a method object,
                    // which raises "'list' object has no attribute 'invoke'".
                    path.insert(0, rootPy);
                }

                // barra_cne5_data_loader.py is at <root>/data-source/tushare/ — insert
                // that dir too so its internal `import` statements resolve.
                var tushareDir = Path.Combine(root, "data-source", "tushare");
                var tusharePy = tushareDir.ToPython();
                if (!path.__contains__(tusharePy).__bool__())
                {
                    path.insert(0, tusharePy);
                }

                dynamic importlib = Py.Import("importlib.util");
                var loaderPath = Path.Combine(root, "data-source", "tushare",
                    "barra_cne5_data_loader.py");
                dynamic spec = importlib.spec_from_file_location(
                    "barra_cne5_data_loader", loaderPath);
                dynamic mod = importlib.module_from_spec(spec);

                // CRITICAL: Register the module in sys.modules BEFORE exec_module.
                // Without this, @dataclass(frozen=True) at line 10 of
                // barra_cne5_data_loader.py fails during exec_module with
                // "'NoneType' object has no attribute '__dict__'" because the
                // dataclass decorator calls sys.modules.get(cls.__module__).__dict__
                // and the module isn't in sys.modules yet (pythonnet's
                // module_from_spec does not auto-register).
                dynamic sys2 = Py.Import("sys");
                sys2.modules.__setitem__("barra_cne5_data_loader", mod);

                spec.loader.exec_module(mod);

                _loader = mod.BarraCNE5DataLoader(_dataRoot);
            }
            _pythonInitialized = true;
        }

        /// <summary>
        /// Convert tushare ts_code (e.g. <c>600519.SH</c>, <c>000001.SZ</c>) to a LEAN
        /// <see cref="Symbol"/>. Suffix <c>.SH</c> → <c>Market.SSE</c>, else
        /// <c>Market.SZSE</c>. Mirrors <c>AShareBarraCNE5Algorithm.TryResolveSymbol</c>
        /// and the inverse of <c>ChipPeakFactorZooAlphaModel.SymbolToTsCode</c>.
        /// </summary>
        private static bool TryConvertTsCodeToSymbol(string tsCode, out Symbol symbol)
        {
            symbol = default;
            if (string.IsNullOrWhiteSpace(tsCode)) return false;
            var parts = tsCode.Trim().Split('.');
            if (parts.Length != 2 || string.IsNullOrEmpty(parts[0])) return false;

            var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase)
                ? Market.SSE
                : Market.SZSE;
            symbol = Symbol.Create(parts[0], SecurityType.Equity, market);
            return true;
        }
    }
}
