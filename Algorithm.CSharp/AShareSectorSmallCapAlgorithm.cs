using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareSectorSmallCapAlgorithm : QCAlgorithm
    {
        private const int LotSize = 100;

        private decimal _initialCapital;
        private int _topSectors;
        private int _stocksPerSector;
        private decimal _maxMarketCapWan;
        private decimal _maxPb;
        private decimal _positionSize;
        private decimal _feeRate;
        private int _rebalanceDayOfMonth;
        private string _factorFilePath;
        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private string _dailySummaryPath;
        private string _summaryFilePath;

        private decimal _advisoryCash;
        private readonly Dictionary<Symbol, AdvisoryPosition> _positions = new();
        private readonly List<Symbol> _universe = new();
        private readonly List<AdvisorySignal> _signals = new();
        private readonly List<AdvisoryDailyRow> _dailyRows = new();
        private int _persistedSignalCount;
        private DateTime _lastEvaluationDate;
        private DateTime _lastRebalanceMonth;

        private readonly Dictionary<string, Dictionary<string, FactorRow>> _factorData = new();
        private readonly HashSet<string> _allTsCodes = new();

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1_000_000m);
            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetBenchmark(x => 0m);

            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2025, 12, 31)));

            _topSectors = GetIntParameter("top-sectors", 3);
            _stocksPerSector = GetIntParameter("stocks-per-sector", 5);
            _maxMarketCapWan = GetDecimalParameter("max-market-cap-wan", 500_000m);
            _maxPb = GetDecimalParameter("max-pb", 5.0m);
            _positionSize = GetDecimalParameter("position-size", 0.067m);
            _feeRate = GetDecimalParameter("fee-rate", 0.0013m);
            _rebalanceDayOfMonth = GetIntParameter("rebalance-day-of-month", 1);
            _advisoryCash = _initialCapital;

            _factorFilePath = ResolveOutputPath(GetParameter("factor-file"), "ashare-sector-smallcap-factors.csv");
            _signalFilePath = ResolveOutputPath(GetParameter("signal-file"), "ashare-sector-smallcap-signals.json");
            _portfolioSnapshotPath = ResolveOutputPath(GetParameter("portfolio-snapshot-file"), "ashare-sector-smallcap-portfolio.json");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "ashare-sector-smallcap-daily.csv");
            _summaryFilePath = ResolveOutputPath(GetParameter("summary-file"), "ashare-sector-smallcap-summary.json");

            LoadFactorData();

            foreach (var tsCode in _allTsCodes)
            {
                var parts = tsCode.Split('.');
                if (parts.Length != 2) continue;
                var ticker = parts[0];
                var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
                try
                {
                    var equity = AddEquity(ticker, Resolution.Daily, market);
                    equity.FeeModel = new AShareStockFeeModel();
                    equity.FillModel = new AShareStockFillModel();
                    equity.BuyingPowerModel = new AShareStockBuyingPowerModel();
                    equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                    _universe.Add(equity.Symbol);
                }
                catch { }
            }

            if (_universe.Count == 0)
                throw new InvalidOperationException("No symbols loaded. Check factor-file path.");

            Schedule.On(DateRules.EveryDay(_universe[0]), TimeRules.AfterMarketOpen(_universe[0], 5), EvaluateDaily);
            PersistOutputs();
            Log($"AShareSectorSmallCapAlgorithm: {_universe.Count} symbols, factor dates: {_factorData.Count}");
        }

        public override void OnEndOfAlgorithm()
        {
            PersistOutputs();
            WriteSummary();
            Log($"AShareSectorSmallCapAlgorithm finished. Signals: {_signals.Count}, Days: {_dailyRows.Count}");
        }

        private void EvaluateDaily()
        {
            var today = Time.Date;
            if (_lastEvaluationDate == today) return;
            _lastEvaluationDate = today;

            foreach (var pos in _positions.Values)
                pos.HoldingDays++;

            UpdatePositionPrices();

            var isRebalanceDay = today.Month != _lastRebalanceMonth.Month || _lastRebalanceMonth == default;
            if (isRebalanceDay)
            {
                _lastRebalanceMonth = today;
                Rebalance(today);
            }

            RecordDailyRow(today);
            PersistOutputs();
        }

        private void Rebalance(DateTime today)
        {
            var dateKey = today.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            var factorDate = FindClosestFactorDate(dateKey);
            if (factorDate == null)
            {
                Log($"[{dateKey}] No factor data available, skipping rebalance.");
                return;
            }

            var factors = _factorData[factorDate];

            var sectorMomentum = factors.Values
                .Where(r => r.Momentum20d.HasValue)
                .GroupBy(r => r.Sector)
                .Where(g => g.Count() >= 3)
                .ToDictionary(
                    g => g.Key,
                    g => g.Average(r => r.Momentum20d!.Value));

            if (sectorMomentum.Count == 0)
            {
                Log($"[{dateKey}] No sector momentum data, skipping rebalance.");
                return;
            }

            var topSectors = sectorMomentum
                .OrderByDescending(kv => kv.Value)
                .Take(_topSectors)
                .Select(kv => kv.Key)
                .ToHashSet();

            var targetTsCodes = new HashSet<string>();
            foreach (var sector in topSectors)
            {
                var candidates = factors.Values
                    .Where(r => r.Sector == sector
                        && r.TotalMvWan.HasValue && r.TotalMvWan.Value > 0 && r.TotalMvWan.Value <= _maxMarketCapWan
                        && (!r.Pb.HasValue || r.Pb.Value <= _maxPb || r.Pb.Value <= 0))
                    .OrderBy(r => r.TotalMvWan!.Value)
                    .Take(_stocksPerSector)
                    .Select(r => r.TsCode)
                    .ToList();
                foreach (var code in candidates)
                    targetTsCodes.Add(code);
            }

            foreach (var sym in _positions.Keys.ToList())
            {
                var tsCode = ToTsCode(sym);
                if (targetTsCodes.Contains(tsCode)) continue;
                var pos = _positions[sym];
                var price = GetAdvisoryPrice(sym, pos.LastPrice);
                var fee = pos.Quantity * price * _feeRate;
                _advisoryCash += pos.Quantity * price - fee;
                _signals.Add(CreateSignal("SELL", sym, price, pos.Quantity,
                    $"rebalance: sector not in top-{_topSectors} or cap/pb filter", 0.8m, "normal"));
                _positions.Remove(sym);
            }

            var currentEquity = GetAdvisoryEquity();
            foreach (var tsCode in targetTsCodes)
            {
                var sym = _universe.FirstOrDefault(s => ToTsCode(s) == tsCode);
                if (sym == null || _positions.ContainsKey(sym)) continue;

                var price = GetAdvisoryPrice(sym, 0m);
                if (price <= 0) continue;

                var alloc = currentEquity * _positionSize;
                var qty = GetLotQuantity(Math.Min(_advisoryCash, alloc), price);
                if (qty <= 0) continue;

                var fee = qty * price * _feeRate;
                _advisoryCash -= qty * price + fee;
                _positions[sym] = new AdvisoryPosition
                {
                    Symbol = sym,
                    Quantity = qty,
                    EntryPrice = price,
                    LastPrice = price,
                    HoldingDays = 0,
                };
                var factor = factors.GetValueOrDefault(tsCode);
                _signals.Add(CreateSignal("BUY", sym, price, qty,
                    $"sector={factor?.Sector ?? "?"} mv={factor?.TotalMvWan:F0}万 pb={factor?.Pb:F2}",
                    0.75m, "normal"));
            }

            Log($"[{dateKey}] Rebalance: topSectors=[{string.Join(",", topSectors)}] target={targetTsCodes.Count} positions={_positions.Count}");
        }

        private void UpdatePositionPrices()
        {
            foreach (var pos in _positions.Values)
            {
                var price = GetAdvisoryPrice(pos.Symbol, pos.LastPrice);
                if (price > 0) pos.LastPrice = price;
            }
        }

        private decimal GetAdvisoryPrice(Symbol sym, decimal fallback)
        {
            try
            {
                var bars = History<TradeBar>(sym, 2, Resolution.Daily).ToList();
                if (bars.Count > 0) return bars[bars.Count - 1].Close;
            }
            catch { }
            return fallback > 0 ? fallback : (Securities.ContainsKey(sym) ? Securities[sym].Price : 0m);
        }

        private decimal GetAdvisoryEquity() =>
            _advisoryCash + _positions.Values.Sum(p => p.Quantity * p.LastPrice);

        private void RecordDailyRow(DateTime date)
        {
            var mv = _positions.Values.Sum(p => p.Quantity * p.LastPrice);
            _dailyRows.Add(new AdvisoryDailyRow
            {
                TradeDate = date,
                Cash = _advisoryCash,
                MarketValue = mv,
                TotalValue = _advisoryCash + mv,
                PositionCount = _positions.Count,
            });
        }

        private AdvisorySignal CreateSignal(string action, Symbol sym, decimal price, int qty, string reason, decimal confidence, string urgency) =>
            new AdvisorySignal
            {
                SignalId = $"{Time:yyyyMMddHHmmss}-{action}-{ToTsCode(sym)}",
                Timestamp = Time,
                Action = action,
                Symbol = ToTsCode(sym),
                Name = sym.Value,
                Price = price,
                Quantity = qty,
                Reason = reason,
                Confidence = confidence,
                Urgency = urgency,
            };

        private void PersistOutputs()
        {
            EnsureDir(_signalFilePath);
            EnsureDir(_portfolioSnapshotPath);
            EnsureDir(_dailySummaryPath);

            File.WriteAllText(_signalFilePath,
                JsonConvert.SerializeObject(_signals, Formatting.Indented), System.Text.Encoding.UTF8);

            var mv = _positions.Values.Sum(p => p.Quantity * p.LastPrice);
            var total = GetAdvisoryEquity();
            File.WriteAllText(_portfolioSnapshotPath, JsonConvert.SerializeObject(new
            {
                timestamp = Time,
                initial_capital = _initialCapital,
                cash = _advisoryCash,
                market_value = mv,
                total_value = total,
                total_pnl = total - _initialCapital,
                total_return = _initialCapital == 0 ? 0m : total / _initialCapital - 1m,
                positions = _positions.Values.Select(p => new
                {
                    symbol = ToTsCode(p.Symbol),
                    quantity = p.Quantity,
                    entry_price = p.EntryPrice,
                    last_price = p.LastPrice,
                    holding_days = p.HoldingDays,
                    unrealized_pnl = p.Quantity * (p.LastPrice - p.EntryPrice),
                }).ToList(),
            }, Formatting.Indented), System.Text.Encoding.UTF8);

            var lines = new List<string> { "trade_date,cash,market_value,total_value,position_count" };
            lines.AddRange(_dailyRows.Select(r =>
                $"{r.TradeDate:yyyy-MM-dd},{r.Cash},{r.MarketValue},{r.TotalValue},{r.PositionCount}"));
            File.WriteAllLines(_dailySummaryPath, lines, System.Text.Encoding.UTF8);

            PersistSignalHistory();
        }

        private void PersistSignalHistory()
        {
            foreach (var sig in _signals.Skip(_persistedSignalCount))
            {
                var dir = Path.GetDirectoryName(_signalFilePath) ?? ".";
                var histPath = Path.Combine(dir, $"signals-{sig.Timestamp:yyyyMMdd}.jsonl");
                File.AppendAllText(histPath,
                    JsonConvert.SerializeObject(sig, Formatting.None) + Environment.NewLine,
                    System.Text.Encoding.UTF8);
            }
            _persistedSignalCount = _signals.Count;
        }

        private void WriteSummary()
        {
            if (_dailyRows.Count == 0) return;
            EnsureDir(_summaryFilePath);
            var first = _dailyRows[0].TotalValue;
            var last = _dailyRows[_dailyRows.Count - 1].TotalValue;
            var totalReturn = first == 0 ? 0m : last / _initialCapital - 1m;
            var maxDrawdown = ComputeMaxDrawdown(_dailyRows.Select(r => r.TotalValue).ToList());
            var sharpe = ComputeSharpe(_dailyRows.Select(r => r.TotalValue).ToList());
            var score = (double)sharpe > 0 ? (double)sharpe : (double)totalReturn - Math.Abs((double)maxDrawdown);
            File.WriteAllText(_summaryFilePath, JsonConvert.SerializeObject(new
            {
                algorithm_id = "AShareSectorSmallCapAlgorithm",
                start_date = _dailyRows[0].TradeDate.ToString("yyyy-MM-dd"),
                end_date = _dailyRows[_dailyRows.Count - 1].TradeDate.ToString("yyyy-MM-dd"),
                initial_capital = _initialCapital,
                final_value = last,
                total_return = totalReturn,
                max_drawdown = maxDrawdown,
                sharpe_ratio = sharpe,
                score = (decimal)score,
                total_signals = _signals.Count,
                trading_days = _dailyRows.Count,
            }, Formatting.Indented), System.Text.Encoding.UTF8);
        }

        private void LoadFactorData()
        {
            if (!File.Exists(_factorFilePath))
            {
                Log($"Factor file not found: {_factorFilePath}. Universe will be empty.");
                return;
            }

            var lines = File.ReadAllLines(_factorFilePath, System.Text.Encoding.UTF8);
            if (lines.Length < 2) return;

            var header = lines[0].Split(',');
            var idxDate = Array.IndexOf(header, "trade_date");
            var idxCode = Array.IndexOf(header, "ts_code");
            var idxSector = Array.IndexOf(header, "sector");
            var idxMv = Array.IndexOf(header, "total_mv");
            var idxPb = Array.IndexOf(header, "pb");
            var idxMom = Array.IndexOf(header, "momentum_20d");

            foreach (var line in lines.Skip(1))
            {
                var cols = line.Split(',');
                if (cols.Length <= Math.Max(idxDate, Math.Max(idxCode, idxSector))) continue;
                var date = cols[idxDate].Trim().Replace("-", "");
                var tsCode = cols[idxCode].Trim();
                if (string.IsNullOrEmpty(date) || string.IsNullOrEmpty(tsCode)) continue;

                var row = new FactorRow
                {
                    TsCode = tsCode,
                    Sector = idxSector >= 0 && idxSector < cols.Length ? cols[idxSector].Trim() : "",
                    TotalMvWan = ParseDecimalNullable(idxMv >= 0 && idxMv < cols.Length ? cols[idxMv] : ""),
                    Pb = ParseDecimalNullable(idxPb >= 0 && idxPb < cols.Length ? cols[idxPb] : ""),
                    Momentum20d = ParseDecimalNullable(idxMom >= 0 && idxMom < cols.Length ? cols[idxMom] : ""),
                };

                if (!_factorData.ContainsKey(date))
                    _factorData[date] = new Dictionary<string, FactorRow>();
                _factorData[date][tsCode] = row;
                _allTsCodes.Add(tsCode);
            }

            Log($"Loaded factor data: {_factorData.Count} dates, {_allTsCodes.Count} unique symbols.");
        }

        private string FindClosestFactorDate(string dateKey)
        {
            return _factorData.Keys
                .Where(d => string.Compare(d, dateKey, StringComparison.Ordinal) <= 0)
                .OrderByDescending(d => d)
                .FirstOrDefault();
        }

        public static string ToTsCode(Symbol sym) =>
            $"{sym.Value}.{(sym.ID.Market == Market.SSE ? "SH" : "SZ")}";

        public static int GetLotQuantity(decimal capital, decimal price)
        {
            if (capital <= 0 || price <= 0) return 0;
            return (int)(Math.Floor(capital / (price * LotSize)) * LotSize);
        }

        public static decimal? ParseDecimalNullable(string s)
        {
            if (string.IsNullOrWhiteSpace(s) || s == "nan" || s == "None" || s == "NaN") return null;
            return decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : (decimal?)null;
        }

        public static decimal ComputeMaxDrawdown(IReadOnlyList<decimal> equity)
        {
            if (equity.Count < 2) return 0m;
            var peak = equity[0];
            var maxDd = 0m;
            foreach (var v in equity)
            {
                if (v > peak) peak = v;
                if (peak > 0)
                {
                    var dd = (peak - v) / peak;
                    if (dd > maxDd) maxDd = dd;
                }
            }
            return maxDd;
        }

        public static decimal ComputeSharpe(IReadOnlyList<decimal> equity)
        {
            if (equity.Count < 20) return 0m;
            var returns = new List<double>();
            for (int i = 1; i < equity.Count; i++)
                if (equity[i - 1] > 0)
                    returns.Add((double)(equity[i] / equity[i - 1] - 1m));
            if (returns.Count < 2) return 0m;
            var mean = returns.Average();
            var std = Math.Sqrt(returns.Select(r => (r - mean) * (r - mean)).Average());
            if (std < 1e-10) return 0m;
            return (decimal)(mean / std * Math.Sqrt(252));
        }

        public static List<string> SelectTopSectorsByMomentum(
            Dictionary<string, Dictionary<string, FactorRow>> factorData,
            string dateKey,
            int topN,
            int minStocksPerSector = 3)
        {
            if (!factorData.TryGetValue(dateKey, out var factors)) return new List<string>();
            var sectorMomentum = factors.Values
                .Where(r => r.Momentum20d.HasValue)
                .GroupBy(r => r.Sector)
                .Where(g => g.Count() >= minStocksPerSector)
                .ToDictionary(g => g.Key, g => g.Average(r => r.Momentum20d!.Value));
            return sectorMomentum
                .OrderByDescending(kv => kv.Value)
                .Take(topN)
                .Select(kv => kv.Key)
                .ToList();
        }

        public static List<string> SelectSmallCapStocksInSectors(
            Dictionary<string, FactorRow> factors,
            HashSet<string> sectors,
            int stocksPerSector,
            decimal maxMarketCapWan,
            decimal maxPb)
        {
            var result = new List<string>();
            foreach (var sector in sectors)
            {
                var candidates = factors.Values
                    .Where(r => r.Sector == sector
                        && r.TotalMvWan.HasValue && r.TotalMvWan.Value > 0 && r.TotalMvWan.Value <= maxMarketCapWan
                        && (!r.Pb.HasValue || r.Pb.Value <= maxPb || r.Pb.Value <= 0))
                    .OrderBy(r => r.TotalMvWan!.Value)
                    .Take(stocksPerSector)
                    .Select(r => r.TsCode);
                result.AddRange(candidates);
            }
            return result;
        }

        private decimal GetDecimalParameter(string name, decimal def)
        {
            var p = GetParameter(name);
            return decimal.TryParse(p, NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : def;
        }

        private int GetIntParameter(string name, int def)
        {
            var p = GetParameter(name);
            return int.TryParse(p, out var v) ? v : def;
        }

        private DateTime GetDateParameter(string name, DateTime def)
        {
            var p = GetParameter(name);
            return DateTime.TryParse(p, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : def;
        }

        private string ResolveOutputPath(string value, string defaultName)
        {
            var path = string.IsNullOrWhiteSpace(value) ? defaultName : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private static void EnsureDir(string path)
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        }

        public sealed class FactorRow
        {
            public string TsCode { get; set; }
            public string Sector { get; set; }
            public decimal? TotalMvWan { get; set; }
            public decimal? Pb { get; set; }
            public decimal? Momentum20d { get; set; }
        }

        private sealed class AdvisoryPosition
        {
            public Symbol Symbol { get; set; }
            public int Quantity { get; set; }
            public decimal EntryPrice { get; set; }
            public decimal LastPrice { get; set; }
            public int HoldingDays { get; set; }
        }

        private sealed class AdvisorySignal
        {
            [JsonProperty("signal_id")] public string SignalId { get; set; }
            [JsonProperty("timestamp")] public DateTime Timestamp { get; set; }
            [JsonProperty("action")] public string Action { get; set; }
            [JsonProperty("symbol")] public string Symbol { get; set; }
            [JsonProperty("name")] public string Name { get; set; }
            [JsonProperty("price")] public decimal Price { get; set; }
            [JsonProperty("quantity")] public int Quantity { get; set; }
            [JsonProperty("reason")] public string Reason { get; set; }
            [JsonProperty("confidence")] public decimal Confidence { get; set; }
            [JsonProperty("urgency")] public string Urgency { get; set; }
        }

        private sealed class AdvisoryDailyRow
        {
            public DateTime TradeDate { get; set; }
            public decimal Cash { get; set; }
            public decimal MarketValue { get; set; }
            public decimal TotalValue { get; set; }
            public int PositionCount { get; set; }
        }
    }
}
