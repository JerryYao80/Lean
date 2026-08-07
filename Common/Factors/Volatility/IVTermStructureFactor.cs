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
    public class IVTermStructureFactor : IFactor
    {
        private readonly Dictionary<Symbol, List<(DateTime time, decimal ratio)>> _cache = new();
        public string Id => "iv_term_structure";
        public string Name => "IV Term Structure (near/next)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_opt_daily";

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            var ratioHistory = GetRatioHistory(symbol);
            if (ratioHistory == null || ratioHistory.Count == 0)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var entry = ratioHistory.LastOrDefault(e => e.Item1 <= time);
            if (entry == default) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = entry.Item2, RawValue = entry.Item2 };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => GetRatioHistory(symbol)?.Count > 0;

        private List<(DateTime, decimal)> GetRatioHistory(Symbol symbol)
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
                int idxNear = colIdx.TryGetValue("sigma_near", out var sn) ? sn : -1;
                int idxNext = colIdx.TryGetValue("sigma_next", out var sf) ? sf : -1;
                if (idxNear < 0 || idxNext < 0) return null;
                var history = new List<(DateTime, decimal)>();
                for (int li = 1; li < lines.Length; li++)
                {
                    var parts = lines[li].Split(',');
                    try
                    {
                        var dt = DateTime.ParseExact(parts[idxDate].Trim(), "yyyyMMdd", CultureInfo.InvariantCulture);
                        var near = decimal.TryParse(parts[idxNear].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var n) ? n : 0m;
                        var next = decimal.TryParse(parts[idxNext].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var f) ? f : 0m;
                        if (next > 0) history.Add((dt, near / next - 1m));
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
