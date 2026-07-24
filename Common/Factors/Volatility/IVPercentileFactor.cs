using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace QuantConnect.Factors.Volatility
{
    public class IVPercentileFactor : IFactor
    {
        private readonly int _lookbackDays;
        private readonly Dictionary<Symbol, List<(System.DateTime time, decimal iv)>> _ivCache = new();

        public string Id => $"iv_pct_{_lookbackDays}d";
        public string Name => $"{_lookbackDays}-Day IV Percentile";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_opt_daily";

        public IVPercentileFactor(int lookbackDays = 252) { _lookbackDays = lookbackDays; }

        public FactorResult Compute(Symbol symbol, System.DateTime time, IEnumerable<QuantConnect.Data.BaseData> history = null)
        {
            var ivHistory = GetIVHistory(symbol);
            if (ivHistory == null || ivHistory.Count < 30) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            // Last entry with time <= current time
            var currentEntry = ivHistory.LastOrDefault(e => e.Item1 <= time);
            if (currentEntry == default) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            var windowIVs = ivHistory.Where(e => e.Item1 >= time.AddDays(-_lookbackDays) && e.Item1 <= time).Select(e => e.Item2).Where(iv => iv > 0).ToList();
            if (windowIVs.Count < 30) return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
            windowIVs.Sort();
            int rank = windowIVs.Count;
            for (int i = 0; i < windowIVs.Count; i++) { if (windowIVs[i] >= currentEntry.Item2) { rank = i; break; } }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = (decimal)rank / windowIVs.Count, RawValue = currentEntry.Item2 };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, System.DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, System.DateTime time) => GetIVHistory(symbol)?.Count >= 30;

        private List<(System.DateTime, decimal)> GetIVHistory(Symbol symbol)
        {
            if (_ivCache.TryGetValue(symbol, out var cached)) return cached;
            var csvPath = Globals.DataFolder + $"/alternative/ashare-implied-volatility/sse/daily/{symbol.ID.Symbol}.csv";
            if (!File.Exists(csvPath)) return null;
            var lines = File.ReadAllLines(csvPath);
            if (lines.Length < 2) return null;
            var header = lines[0].Split(',');
            var colIdx = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < header.Length; i++) colIdx[header[i].Trim()] = i;
            int idxDate = colIdx.TryGetValue("trade_date", out var d) ? d : 0;
            int idxIV = colIdx.TryGetValue("atm_iv", out var iv) ? iv : -1;
            if (idxIV < 0) return null;
            var history = new List<(System.DateTime, decimal)>();
            for (int li = 1; li < lines.Length; li++)
            {
                var parts = lines[li].Split(',');
                try { var dt = System.DateTime.ParseExact(parts[idxDate].Trim(), "yyyyMMdd", CultureInfo.InvariantCulture);
                      var v = decimal.TryParse(parts[idxIV].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var val) ? val : 0m;
                      if (v > 0) history.Add((dt, v)); } catch { }
            }
            _ivCache[symbol] = history;
            return history;
        }
    }
}