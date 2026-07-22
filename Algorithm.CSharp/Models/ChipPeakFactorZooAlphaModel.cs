using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Chip;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;
using QuantConnect.Util;
using Python.Runtime;

namespace QuantConnect.Algorithm.CSharp.Models
{
    /// <summary>
    /// ChipPeak AlphaModel (因子动物园版) - 使用 Python.NET 批量扫描 cyq_perf 数据,
    /// 将 composite_score 注入 ChipPeakCompositeFactor, 然后通过 FactorRegistry 取用.
    /// </summary>
    public class ChipPeakFactorZooAlphaModel : IAlphaModel
    {
        private readonly string _dataFolder;
        private readonly int _topN;
        private readonly int _batchSize;
        private readonly int _maxWorkers;
        private readonly decimal _minScore;
        private readonly ChipPeakCompositeFactor _compositeFactor;

        private dynamic _chipPeakFactors;
        private dynamic _chipDataLoader;
        private bool _pythonInitialized = false;

        public string Name => "ChipPeakFactorZoo";

        private readonly string _rootPath;

        public ChipPeakFactorZooAlphaModel(
            string dataFolder = "/home/project/tushare-downloader/tushare_data_v2",
            int topN = 5, int batchSize = 500, int maxWorkers = 2, decimal minScore = 0.01m,
            string rootPath = null)
        {
            _dataFolder = dataFolder; _topN = topN; _batchSize = batchSize;
            _maxWorkers = maxWorkers; _minScore = minScore;
            // 推断项目根目录: DataFolder 通常是 /path/to/Data, 根是父目录
            _rootPath = rootPath ?? Directory.GetParent(Globals.DataFolder)?.FullName ?? "/home/project/hope/Lean";

            FactorRegistry.Initialize();
            _compositeFactor = FactorRegistry.Get("chip_peak_composite") as ChipPeakCompositeFactor;
        }

        private void InitializePython()
        {
            if (_pythonInitialized) return;
            using (Py.GIL())
            {
                var sys = Py.Import("sys");
                dynamic path = sys.GetAttr("path");
                var root = _rootPath;
                // path 是 Python list, 用 Python in 操作符判断
                var rootPy = root.ToPython();
                if (!path.__contains__(rootPy).__bool__())
                {
                    path.invoke("insert", 0, rootPy);
                }

                dynamic importlib = Py.Import("importlib.util");
                var factorsPath = Path.Combine(root, "Algorithm.Python", "ChipPeakFactors.py");
                dynamic spec = importlib.spec_from_file_location("ChipPeakFactors", factorsPath);
                dynamic mod = importlib.module_from_spec(spec);
                spec.loader.exec_module(mod);
                _chipPeakFactors = mod.ChipPeakFactors;

                var loaderPath = Path.Combine(root, "ToolBox", "ChipDataLoader.py");
                dynamic loaderSpec = importlib.spec_from_file_location("ChipDataLoader", loaderPath);
                dynamic loaderMod = importlib.module_from_spec(loaderSpec);
                loaderSpec.loader.exec_module(loaderMod);
                // 用位置参数调用: ChipDataLoader(data_folder, cyq_dir='cyq_perf', ..., max_batch_size, max_workers)
                // 但 cyq_dir/adj_dir 是字符串，避免误传 int. 直接传 str 类型.
                _chipDataLoader = loaderMod.ChipDataLoader(
                    _dataFolder,
                    "cyq_perf",
                    "adj_factor",
                    "moneyflow",
                    "daily_basic",
                    _batchSize,
                    _maxWorkers);
            }
            _pythonInitialized = true;
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            InitializePython();
            var candidates = algorithm.ActiveSecurities.Keys.ToList();
            if (candidates.Count == 0) return new List<Insight>();

            _compositeFactor?.ClearInjectedValues();

            var codeToSymbol = new Dictionary<string, Symbol>();
            foreach (var sym in candidates)
            {
                codeToSymbol[SymbolToTsCode(sym)] = sym;
            }

            var currentDate = algorithm.Time.ToString("yyyyMMdd");
            var codes = codeToSymbol.Keys.ToList();
            var scores = new Dictionary<Symbol, double>();
            dynamic defaultParams = _chipPeakFactors.DEFAULT_PARAMS;

            using (Py.GIL())
            {
                int totalBatches = (codes.Count + _batchSize - 1) / _batchSize;
                int completed = 0;

                for (int i = 0; i < codes.Count; i += _batchSize)
                {
                    int end = Math.Min(i + _batchSize, codes.Count);
                    var batchCodes = codes.GetRange(i, end - i);

                    foreach (var tsCode in batchCodes)
                    {
                        try
                        {
                            dynamic chipRow = _chipDataLoader.enriched_load_single(tsCode, currentDate);
                            if (chipRow == null || chipRow.IsNone()) continue;

                            var sym = codeToSymbol[tsCode];
                            var security = algorithm.Securities[sym];
                            var currentPrice = (double)security.Price;

                            double score = (double)_chipPeakFactors.composite_score(chipRow, currentPrice, defaultParams);

                            if (score > (double)_minScore)
                            {
                                scores[sym] = score;
                                _compositeFactor?.InjectValue(sym, (decimal)score);
                            }
                        }
                        catch { }
                    }

                    completed++;
                    if (completed % 3 == 0 || completed == totalBatches)
                        algorithm.Log($"[ChipPeakFactorZoo] {completed * 100.0 / totalBatches:F1}% ({completed}/{totalBatches} batches)");
                }
            }

            if (scores.Count == 0) return new List<Insight>();

            var ranked = scores.OrderByDescending(kv => kv.Value).Take(_topN).ToList();
            var insights = new List<Insight>();
            foreach (var kv in ranked)
            {
                var factorResult = _compositeFactor?.Compute(kv.Key, algorithm.Time, null);
                insights.Add(Insight.Price(kv.Key, TimeSpan.FromDays(7), InsightDirection.Up, kv.Value, null, Name));
            }

            algorithm.Debug($"[ChipPeakFactorZoo] scanned {scores.Count}, emitted {insights.Count}");
            return insights;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        public static string SymbolToTsCode(Symbol symbol)
        {
            var ticker = symbol.ID.Symbol;
            if (ticker.StartsWith("6") || ticker.StartsWith("51")) return $"{ticker}.SH";
            return $"{ticker}.SZ";
        }
    }
}