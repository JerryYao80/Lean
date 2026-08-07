/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter for Barra CNE5 V2 CSV factors.
 * Mirrors the IVPercentileFactor GetIVHistory read pattern (File.ReadAllLines +
 * Split(',') + column-name lookup). Never writes.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Reads one Barra factor column (beta/momentum/.../chipcost) for (symbol, date)
    /// from Data/alternative/barra-cne5v2-factors/{market}/daily/{ticker}.csv.
    /// </summary>
    public class RBarraAdapter : IFactorAdapter
    {
        private readonly string _column;
        private readonly string _dataRoot;

        public RBarraAdapter(string column, string dataRoot = null)
        {
            _column = column;
            _dataRoot = dataRoot ?? Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-factors");
        }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var (market, ticker) = ResolveMarketTicker(symbol);
            var csvPath = Path.Combine(_dataRoot, market, "daily", $"{ticker}.csv");
            result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _column, Quality = FactorDataQuality.Missing };
            if (!File.Exists(csvPath)) return false;

            var lines = File.ReadAllLines(csvPath);
            if (lines.Length < 2) return false;
            var header = lines[0].Split(',');
            int idxDate = -1, idxCol = -1;
            for (int i = 0; i < header.Length; i++)
            {
                var h = header[i].Trim();
                if (h == "trade_date") idxDate = i;
                else if (h == _column) idxCol = i;
            }
            if (idxDate < 0 || idxCol < 0) return false;

            var want = date.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            string bestValue = null;
            for (int i = lines.Length - 1; i >= 1; i--)
            {
                var parts = lines[i].Split(',');
                if (parts.Length <= Math.Max(idxDate, idxCol)) continue;
                var d = parts[idxDate].Trim();
                if (string.CompareOrdinal(d, want) <= 0) { bestValue = parts[idxCol].Trim(); break; }
            }
            if (bestValue == null) return false;
            if (!decimal.TryParse(bestValue, NumberStyles.Float, CultureInfo.InvariantCulture, out var v)) return false;
            result = new FactorResult { Value = v, RawValue = v, Time = date, Symbol = symbol, FactorId = _column, Quality = FactorDataQuality.Valid };
            return true;
        }

        private static (string market, string ticker) ResolveMarketTicker(Symbol symbol)
        {
            // 6-prefix (SH main board) or 51-prefix (SH ETF: 510xxx-518xxx) -> sse; else szse.
            // Matches the reference SymbolToTsCode rule; the old 6/9-only rule misclassified
            // 5-prefix SH ETFs (511660 etc., whose CSVs live in sse/daily/) as szse.
            var ticker = symbol.ID.Symbol;
            var market = (ticker.StartsWith("6") || ticker.StartsWith("51")) ? "sse" : "szse";
            return (market, ticker);
        }
    }
}
