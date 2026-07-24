using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace QuantConnect.Factors.Volatility
{
    public class IVSkewFactor : IFactor
    {
        private readonly int _lookback;
        private readonly Dictionary<Symbol, List<(DateTime time, decimal skew)>> _cache = new();
        public string Id => $"iv_skew_{_lookback}d";
        public string Name => $"{_lookback}-Day IV Skew";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_opt_daily";

        public IVSkewFactor(int lookback = 252) => _lookback = lookback;

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            var skewHistory = GetSkewHistory(symbol);
            if (skewHistory == null || skewHistory.Count == 0)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var entry = skewHistory.LastOrDefault(e => e.Item1 <= time);
            if (entry == default) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var window = skewHistory.Where(e => e.Item1 >= time.AddDays(-_lookback) && e.Item1 <= time).Select(e => e.Item2).ToList();
            decimal percentile = 0m;
            if (window.Count > 1)
            {
                window.Sort();
                int rank = window.Count;
                for (int i = 0; i < window.Count; i++) { if (window[i] >= entry.Item2) { rank = i; break; } }
                percentile = (decimal)rank / window.Count;
            }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = percentile, RawValue = entry.Item2 };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => GetSkewHistory(symbol)?.Count > 0;

        private List<(DateTime, decimal)> GetSkewHistory(Symbol symbol)
        {
            if (_cache.TryGetValue(symbol, out var cached)) return cached;
            var csvPath = Globals.DataFolder + $"/alternative/ashare-implied-volatility/sse/daily/{symbol.ID.Symbol}.csv";
            if (!File.Exists(csvPath)) return null;
            try
            {
                var lines = File.ReadAllLines(csvPath);
                if (lines.Length < 2) return null;
                var header = lines[0].Split(',');
                var colIdx = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                for (int i = 0; i < header.Length; i++) colIdx[header[i].Trim()] = i;
                int idxDate = colIdx.TryGetValue("trade_date", out var d) ? d : 0;
                int idxSkew = colIdx.TryGetValue("skew", out var s) ? s : -1;
                if (idxSkew < 0) return null;
                var history = new List<(DateTime, decimal)>();
                for (int li = 1; li < lines.Length; li++)
                {
                    var parts = lines[li].Split(',');
                    try
                    {
                        var dt = DateTime.ParseExact(parts[idxDate].Trim(), "yyyyMMdd", CultureInfo.InvariantCulture);
                        var v = decimal.TryParse(parts[idxSkew].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var val) ? val : 0m;
                        history.Add((dt, v));
                    }
                    catch { }
                }
                _cache[symbol] = history;
                return history;
            }
            catch { return null; }
        }
    }
}
