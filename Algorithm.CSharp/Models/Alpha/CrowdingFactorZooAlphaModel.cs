using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Sentiment;
using QuantConnect.Securities;
using QuantConnect.Util;
using Python.Runtime;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// Crowding AlphaModel (因子动物园版) — long-only low-crowding 30% basket,
    /// monthly rebalance.
    ///
    /// Mirrors <c>ChipPeakFactorZooAlphaModel</c>: the crowding composite is
    /// precomputed by <c>data-source/tushare/crowding_factor_builder.py</c> and
    /// persisted as one parquet per ts_code at
    /// <c>result/crowding-factor/&lt;YYYY-MM-DD&gt;/&lt;ts_code&gt;.parquet</c>.
    /// On each rebalance, this model reads those parquet files via pythonnet
    /// (pandas), injects the composite into <c>CrowdingFactor</c> (Precomputed +
    /// <see cref="CrowdingFactor.InjectValue"/>) and emits <c>Insight.Price</c>
    /// for the lowest-crowding names.
    ///
    /// Spec §4 (loud fail): if the parquet for the algorithm's current trade
    /// date is missing for ANY active security, the model throws
    /// <see cref="InvalidOperationException"/> so the operator runs
    /// <c>crowding_factor_builder</c> before retrying — never silently returns
    /// empty (silent fail would look like a valid "hold" signal).
    /// </summary>
    public class CrowdingFactorZooAlphaModel : IAlphaModel
    {
        private const string DefaultResultRoot = "/home/project/hope/Lean/result";

        private readonly string _resultRoot;
        private readonly decimal _lowQuantile;
        private readonly int _rebalanceMonths;
        private readonly int _insightPeriodDays;
        private readonly CrowdingFactor _crowdingFactor;

        private dynamic _pandas;
        private bool _pythonInitialized;
        private int _lastRebalanceMonth = -1;
        private int _lastRebalanceYear = -1;

        public string Name => "CrowdingFactorZoo";

        private readonly string _rootPath;

        public CrowdingFactorZooAlphaModel(
            string resultRoot = null,
            decimal lowQuantile = 0.30m,
            int rebalanceMonths = 1,
            int insightPeriodDays = 30,
            string rootPath = null)
        {
            _resultRoot = resultRoot ?? DefaultResultRoot;
            _lowQuantile = lowQuantile;
            _rebalanceMonths = Math.Max(1, rebalanceMonths);
            _insightPeriodDays = Math.Max(1, insightPeriodDays);
            _rootPath = rootPath ?? Directory.GetParent(Globals.DataFolder)?.FullName
                ?? "/home/project/hope/Lean";

            FactorRegistry.Initialize();
            _crowdingFactor = FactorRegistry.Get("crowding") as CrowdingFactor;
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var candidates = algorithm.ActiveSecurities.Keys.ToList();
            if (candidates.Count == 0) return new List<Insight>();

            // Monthly rebalance: only emit on the first trading day of a new
            // month (relative to _rebalanceMonths cadence). Non-rebalance days
            // return empty insights — the portfolio construction model holds
            // existing positions until the next rebalance.
            var now = algorithm.Time;
            if (IsRebalanceMonth(now))
            {
                // fall through to rebalance
            }
            else
            {
                return new List<Insight>();
            }

            InitializePython();

            _crowdingFactor?.ClearInjectedValues();

            var codeToSymbol = new Dictionary<string, Symbol>();
            foreach (var sym in candidates)
            {
                codeToSymbol[SymbolToTsCode(sym)] = sym;
            }

            // result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet
            var dateFolder = now.ToString("yyyy-MM-dd");
            var dateDir = Path.Combine(_resultRoot, "crowding-factor", dateFolder);

            var scores = new Dictionary<Symbol, double>();
            using (Py.GIL())
            {
                foreach (var kv in codeToSymbol)
                {
                    var tsCode = kv.Key;
                    var sym = kv.Value;
                    var parquetPath = Path.Combine(dateDir, $"{tsCode}.parquet");
                    if (!File.Exists(parquetPath))
                    {
                        throw new InvalidOperationException(
                            $"crowding data missing for {dateFolder}/{tsCode}.parquet; "
                            + $"run crowding_factor_builder first (result_root={_resultRoot}).");
                    }

                    try
                    {
                        dynamic df = _pandas.read_parquet(parquetPath);
                        if (df == null || df.__bool__().__bool__() == false)
                        {
                            throw new InvalidOperationException(
                                $"crowding parquet empty for {dateFolder}/{tsCode}.parquet; "
                                + "run crowding_factor_builder first.");
                        }

                        // composite column holds the crowding score ∈ [0,1].
                        dynamic compositeVal = df["composite"].iloc[0];
                        double composite = (double)compositeVal;
                        scores[sym] = composite;
                        _crowdingFactor?.InjectValue(sym, (decimal)composite);
                    }
                    catch (InvalidOperationException)
                    {
                        throw;
                    }
                    catch (Exception ex)
                    {
                        throw new InvalidOperationException(
                            $"crowding parquet read failed for {dateFolder}/{tsCode}.parquet: "
                            + $"{ex.Message}; run crowding_factor_builder first.", ex);
                    }
                }
            }

            if (scores.Count == 0) return new List<Insight>();

            // Low-crowding long-only: sort ascending, take bottom lowQuantile.
            var ranked = scores.OrderBy(kv => kv.Value).ToList();
            var selectedCount = Math.Max(1, (int)Math.Floor(ranked.Count * (double)_lowQuantile));
            if (selectedCount > ranked.Count) selectedCount = ranked.Count;
            var selected = ranked.Take(selectedCount).ToList();

            var insights = new List<Insight>();
            var weight = 1.0 / selected.Count;
            foreach (var kv in selected)
            {
                var factorResult = _crowdingFactor?.Compute(kv.Key, algorithm.Time, null);
                insights.Add(Insight.Price(
                    kv.Key,
                    TimeSpan.FromDays(_insightPeriodDays),
                    InsightDirection.Up,
                    magnitude: kv.Value,
                    confidence: null,
                    sourceModel: Name,
                    weight: weight));
            }

            algorithm.Debug(
                $"[CrowdingFactorZoo] rebalanced {now:yyyy-MM-dd}: scanned {scores.Count}, "
                + $"selected {selected.Count} low-crowding names (q={_lowQuantile:F2}).");

            // Mark the rebalance month so non-rebalance days hold.
            _lastRebalanceMonth = now.Month;
            _lastRebalanceYear = now.Year;
            return insights;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        /// <summary>
        /// True only on the first call in a new month that satisfies the
        /// <c>_rebalanceMonths</c> cadence. Mirrors the universe model's
        /// monthly-refresh contract.
        /// </summary>
        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true; // first ever call
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;

            // Cadence: rebalance every _rebalanceMonths months. Approximate by
            // counting whole months elapsed since the last rebalance.
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }

        private void InitializePython()
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
                    path.invoke("insert", 0, rootPy);
                }

                // pandas is the established parquet reader (mirror ChipPeak's
                // pythonnet pattern; C# has no first-class DataFrame).
                _pandas = Py.Import("pandas");
            }
            _pythonInitialized = true;
        }

        /// <summary>
        /// Symbol → tushare ts_code. Mirrors
        /// <c>ChipPeakFactorZooAlphaModel.SymbolToTsCode</c>:
        /// 6/51 → .SH else .SZ.
        /// </summary>
        public static string SymbolToTsCode(Symbol symbol)
        {
            var ticker = symbol.ID.Symbol;
            if (ticker.StartsWith("6") || ticker.StartsWith("51")) return $"{ticker}.SH";
            return $"{ticker}.SZ";
        }
    }
}
