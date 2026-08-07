using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Universe
{
    /// <summary>
    /// 期权波动率套利 L1 选股模型.
    /// 候选池可为任意提供 IV 数据的标的 (A股 ETF 期权标的 510050/510300/510500/588000/588080 等),
    /// 选股逻辑: 仅保留同时满足以下条件的标的:
    ///   1. 现货日线数据可用 (Data/equity/sse/daily/{ticker}.csv)
    ///   2. IV CSV 可用 (Data/alternative/ashare-implied-volatility/sse/daily/{ticker}.csv)
    ///   3. IV CSV 覆盖算法回测窗口 (若 startDate/endDate 给定)
    /// 这是真正的 L1 选股 (数据可用性门控), 而非硬编码数组透传.
    /// 继承原生 ManualUniverseSelectionModel, 零侵入.
    ///
    /// 自动窗口检测: 当 startDate/endDate 未传 (null) 时, 选股只做 Gate1/Gate2;
    /// 调用方可在构造后读 IvWindowStart/End 拿到入选标的 IV 数据的交集窗口,
    /// 用于回测窗口对齐 (取最长可用跨度). 见 OptionVolArb5LayerStrategy "auto" 模式.
    /// </summary>
    public class OptionVolArbUniverseSelectionModel : ManualUniverseSelectionModel
    {
        /// <summary>最终入选标的 (审计可读).</summary>
        public IReadOnlyList<Symbol> SelectedSymbols { get; }

        /// <summary>被剔除的标的及原因 (审计可读).</summary>
        public IReadOnlyList<(string Ticker, string Reason)> DroppedSymbols { get; }

        /// <summary>入选标的 IV 数据的交集窗口起始 (max of per-ticker minDates). null 若无入选.</summary>
        public DateTime? IvWindowStart { get; }

        /// <summary>入选标的 IV 数据的交集窗口结束 (min of per-ticker maxDates). null 若无入选.</summary>
        public DateTime? IvWindowEnd { get; }

        public OptionVolArbUniverseSelectionModel(
            IEnumerable<string> candidateTickers,
            DateTime? startDate = null,
            DateTime? endDate = null,
            string market = Market.SSE,
            UniverseSettings universeSettings = null)
            : base(FilterCandidates(candidateTickers, startDate, endDate, market, out var selected, out var dropped, out var ivStart, out var ivEnd), universeSettings)
        {
            SelectedSymbols = selected;
            DroppedSymbols = dropped;
            IvWindowStart = ivStart;
            IvWindowEnd = ivEnd;
        }

        /// <summary>
        /// 计算给定候选标的 IV 数据的交集窗口 [max(minDates), min(maxDates)].
        /// 不做 Gate 校验 — 仅扫描 csv 的 trade_date 列. 用于策略层 "auto" 回测窗口.
        /// 返回 null 表示无可用数据或交集为空.
        /// </summary>
        public static (DateTime Start, DateTime End)? ComputeIvIntersectionWindow(
            IEnumerable<string> tickers, string market = Market.SSE)
        {
            DateTime? maxMin = null;
            DateTime? minMax = null;
            var dataFolder = Globals.DataFolder;

            foreach (var ticker in tickers)
            {
                var ivCsv = Path.Combine(dataFolder, "alternative", "ashare-implied-volatility",
                    market.ToLowerInvariant(), "daily", $"{ticker}.csv");
                if (!File.Exists(ivCsv)) continue;

                DateTime? minDate = null, maxDate = null;
                try
                {
                    var lines = File.ReadAllLines(ivCsv);
                    if (lines.Length < 2) continue;
                    var header = lines[0].Split(',');
                    var dateIdx = Array.IndexOf(header, "trade_date");
                    if (dateIdx < 0) continue;

                    for (int i = 1; i < lines.Length; i++)
                    {
                        var parts = lines[i].Split(',');
                        if (parts.Length <= dateIdx) continue;
                        if (DateTime.TryParseExact(parts[dateIdx].Trim(), "yyyyMMdd",
                            CultureInfo.InvariantCulture, DateTimeStyles.None, out var dt))
                        {
                            if (!minDate.HasValue || dt < minDate) minDate = dt;
                            if (!maxDate.HasValue || dt > maxDate) maxDate = dt;
                        }
                    }
                }
                catch { continue; }

                if (!minDate.HasValue || !maxDate.HasValue) continue;
                if (!maxMin.HasValue || minDate.Value > maxMin) maxMin = minDate;
                if (!minMax.HasValue || maxDate.Value < minMax) minMax = maxDate;
            }

            if (!maxMin.HasValue || !minMax.HasValue || maxMin.Value > minMax.Value)
                return null;
            return (maxMin.Value, minMax.Value);
        }

        private static IEnumerable<Symbol> FilterCandidates(
            IEnumerable<string> tickers,
            DateTime? startDate,
            DateTime? endDate,
            string market,
            out List<Symbol> selected,
            out List<(string, string)> dropped,
            out DateTime? ivStart,
            out DateTime? ivEnd)
        {
            selected = new List<Symbol>();
            dropped = new List<(string, string)>();
            ivStart = null;
            ivEnd = null;
            var dataFolder = Globals.DataFolder;
            var minDates = new List<DateTime>();
            var maxDates = new List<DateTime>();

            foreach (var ticker in tickers)
            {
                var symbol = Symbol.Create(ticker, SecurityType.Equity, market);

                // Gate 1: 现货日线数据
                var eqCsv = Path.Combine(dataFolder, "equity", market.ToLowerInvariant(), "daily", $"{ticker}.csv");
                if (!File.Exists(eqCsv))
                {
                    dropped.Add((ticker, $"equity daily csv missing: {eqCsv}"));
                    continue;
                }

                // Gate 2: IV CSV 可用
                var ivCsv = Path.Combine(dataFolder, "alternative", "ashare-implied-volatility", market.ToLowerInvariant(), "daily", $"{ticker}.csv");
                if (!File.Exists(ivCsv))
                {
                    dropped.Add((ticker, $"IV csv missing: {ivCsv}"));
                    continue;
                }

                // Gate 3 (optional): IV CSV 覆盖算法窗口
                DateTime? minDate = null, maxDate = null;
                if (startDate.HasValue || endDate.HasValue)
                {
                    if (!IvCoversWindow(ivCsv, startDate, endDate, out var reason, out minDate, out maxDate))
                    {
                        dropped.Add((ticker, reason));
                        continue;
                    }
                }
                else
                {
                    // 没传窗口 — 仍读 IV 边界供 IvWindowStart/End
                    IvCoversWindow(ivCsv, null, null, out _, out minDate, out maxDate);
                }
                if (minDate.HasValue) minDates.Add(minDate.Value);
                if (maxDate.HasValue) maxDates.Add(maxDate.Value);

                selected.Add(symbol);
            }

            // 入选标的 IV 交集窗口
            if (minDates.Count > 0 && maxDates.Count > 0)
            {
                var start = minDates.Max();
                var end = maxDates.Min();
                if (start <= end)
                {
                    ivStart = start;
                    ivEnd = end;
                }
            }

            return selected;
        }

        private static bool IvCoversWindow(string ivCsv, DateTime? startDate, DateTime? endDate, out string reason, out DateTime? minDate, out DateTime? maxDate)
        {
            reason = null;
            minDate = null;
            maxDate = null;
            try
            {
                var lines = File.ReadAllLines(ivCsv);
                if (lines.Length < 2) { reason = "IV csv empty"; return false; }

                var header = lines[0].Split(',');
                var dateIdx = Array.IndexOf(header, "trade_date");
                if (dateIdx < 0) { reason = "IV csv missing trade_date column"; return false; }

                DateTime? localMin = null, localMax = null;
                for (int i = 1; i < lines.Length; i++)
                {
                    var parts = lines[i].Split(',');
                    if (parts.Length <= dateIdx) continue;
                    if (DateTime.TryParseExact(parts[dateIdx].Trim(), "yyyyMMdd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out var dt))
                    {
                        if (!localMin.HasValue || dt < localMin) localMin = dt;
                        if (!localMax.HasValue || dt > localMax) localMax = dt;
                    }
                }
                minDate = localMin;
                maxDate = localMax;

                if (startDate.HasValue && localMin.HasValue && localMin.Value > startDate.Value)
                {
                    reason = $"IV starts {localMin.Value:yyyy-MM-dd} after backtest start {startDate.Value:yyyy-MM-dd}";
                    return false;
                }
                if (endDate.HasValue && localMax.HasValue && localMax.Value < endDate.Value)
                {
                    reason = $"IV ends {localMax.Value:yyyy-MM-dd} before backtest end {endDate.Value:yyyy-MM-dd}";
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                reason = $"IV csv parse error: {ex.Message}";
                return false;
            }
        }
    }
}
