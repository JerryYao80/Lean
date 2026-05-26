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
    /// <summary>
    /// Advisory A-share T+1 momentum strategy example.
    /// It stays isolated from the existing T+0 ETF workflows by generating synthetic
    /// signals and portfolio snapshots without submitting broker orders.
    /// </summary>
    public class AShareT1MomentumAlgorithm : QCAlgorithm
    {
        private const int LotSize = 100;
        private readonly List<AdvisorySignal> _signals = new();
        private readonly List<AdvisoryDailyRow> _dailyRows = new();
        private readonly Dictionary<Symbol, AdvisoryPosition> _positions = new();
        private readonly List<Symbol> _symbols = new();
        private decimal _initialCapital;
        private decimal _advisoryCash;
        private int _lookbackPeriod;
        private decimal _entryThreshold;
        private decimal _exitThreshold;
        private int _maxPositions;
        private decimal _positionSize;
        private decimal _feeRate;
        private DateTime _lastEvaluationDate;
        private int _persistedSignalCount;
        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private string _dailySummaryPath;

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1000000m);
            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetBenchmark(x => 0m);

            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2025, 12, 31)));

            _lookbackPeriod = GetIntParameter("lookback-period", 20);
            _entryThreshold = GetDecimalParameter("entry-threshold", 0.05m);
            _exitThreshold = GetDecimalParameter("exit-threshold", 0.0m);
            _maxPositions = GetIntParameter("max-positions", 10);
            _positionSize = GetDecimalParameter("position-size", 0.1m);
            _feeRate = GetDecimalParameter("fee-rate", 0.0013m);
            _advisoryCash = _initialCapital;

            _signalFilePath = ResolveOutputPath(GetParameter("signal-file"), "AShareT1MomentumAlgorithm-signals.json");
            _portfolioSnapshotPath = ResolveOutputPath(GetParameter("portfolio-snapshot-file"), "AShareT1MomentumAlgorithm-portfolio.json");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "AShareT1MomentumAlgorithm-daily.csv");

            foreach (var tsCode in ParseUniverse(GetParameter("universe")))
            {
                var parts = tsCode.Split('.');
                if (parts.Length != 2)
                {
                    continue;
                }

                var ticker = parts[0];
                var market = parts[1].Equals("SH", StringComparison.OrdinalIgnoreCase) ? Market.SSE : Market.SZSE;
                var equity = AddEquity(ticker, Resolution.Daily, market);
                equity.FeeModel = new AShareStockFeeModel();
                equity.FillModel = new AShareStockFillModel();
                equity.BuyingPowerModel = new AShareStockBuyingPowerModel();
                equity.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
                equity.Session.Size = 2;
                _symbols.Add(equity.Symbol);
            }

            if (_symbols.Count == 0)
            {
                throw new InvalidOperationException("No A-share symbols were configured for the T+1 momentum strategy.");
            }

            Schedule.On(DateRules.EveryDay(_symbols[0]), TimeRules.AfterMarketOpen(_symbols[0], 5), EvaluateSignals);
            PersistOutputs();
            Log($"AShareT1MomentumAlgorithm initialized with {_symbols.Count} symbols; synthetic advisory mode enabled.");
        }

        public override void OnEndOfAlgorithm()
        {
            PersistOutputs();
            Log($"Advisory outputs saved: signals={_signalFilePath}, portfolio={_portfolioSnapshotPath}, daily={_dailySummaryPath}");
        }

        public static decimal ComputeMomentum(IReadOnlyList<decimal> closes)
        {
            if (closes == null || closes.Count <= 1)
            {
                return 0m;
            }

            var first = closes[0];
            if (first <= 0m)
            {
                return 0m;
            }

            return closes[closes.Count - 1] / first - 1m;
        }

        public static IEnumerable<string> RankEntryCandidates(IReadOnlyDictionary<string, decimal> momentumScores, decimal entryThreshold, int maxPositions)
        {
            if (momentumScores == null || maxPositions <= 0)
            {
                return Enumerable.Empty<string>();
            }

            return momentumScores
                .Where(pair => pair.Value >= entryThreshold)
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key, StringComparer.Ordinal)
                .Take(maxPositions)
                .Select(pair => pair.Key)
                .ToList();
        }

        public static bool ShouldExit(decimal momentum, decimal exitThreshold, int holdingDays)
        {
            return holdingDays >= 1 && momentum <= exitThreshold;
        }

        public static decimal SelectAdvisoryPrice(IReadOnlyList<decimal> visibleCloses, decimal currentSecurityPrice)
        {
            return visibleCloses == null || visibleCloses.Count == 0 ? currentSecurityPrice : visibleCloses[visibleCloses.Count - 1];
        }

        public static bool ShouldEvaluateLiveCatchUp(bool isLiveMode, bool hasData, bool exchangeOpen, DateTime currentTime, DateTime lastEvaluationDate, TimeSpan? cutoff = null)
        {
            return AShareT1MeanReversionAlgorithm.ShouldEvaluateLiveCatchUp(isLiveMode, hasData, exchangeOpen, currentTime, lastEvaluationDate, cutoff);
        }

        public static bool ShouldPersistLiveSnapshot(bool isLiveMode, bool hasData, DateTime currentTime, DateTime lastSnapshotTime, TimeSpan? interval = null)
        {
            return AShareT1MeanReversionAlgorithm.ShouldPersistLiveSnapshot(isLiveMode, hasData, currentTime, lastSnapshotTime, interval);
        }

        public static int GetAvailableQuantity(int quantity, int holdingDays)
        {
            return AShareT1MeanReversionAlgorithm.GetAvailableQuantity(quantity, holdingDays);
        }

        public static string BuildSignalId(DateTime timestamp, string action, string symbol)
        {
            return AShareT1MeanReversionAlgorithm.BuildSignalId(timestamp, action, symbol);
        }

        public static string GetSignalHistoryFilePath(string signalSnapshotPath, DateTime timestamp)
        {
            return AShareT1MeanReversionAlgorithm.GetSignalHistoryFilePath(signalSnapshotPath, timestamp);
        }

        private void EvaluateSignals()
        {
            var tradeDate = Time.Date;
            if (_lastEvaluationDate == tradeDate)
            {
                return;
            }
            _lastEvaluationDate = tradeDate;

            foreach (var position in _positions.Values)
            {
                position.HoldingDays += 1;
            }

            var momentumScores = new Dictionary<Symbol, decimal>();
            var prices = new Dictionary<Symbol, decimal>();
            foreach (var symbol in _symbols)
            {
                var closes = History<TradeBar>(symbol, _lookbackPeriod, Resolution.Daily)
                    .Select(bar => bar.Close)
                    .ToList();
                if (closes.Count < _lookbackPeriod)
                {
                    continue;
                }

                momentumScores[symbol] = ComputeMomentum(closes);
                prices[symbol] = SelectAdvisoryPrice(closes, Securities[symbol].Price);
            }

            foreach (var position in _positions.Values)
            {
                if (prices.TryGetValue(position.Symbol, out var price))
                {
                    position.LastPrice = price;
                }
            }

            foreach (var symbol in _positions.Keys.ToList())
            {
                if (!momentumScores.TryGetValue(symbol, out var momentum))
                {
                    continue;
                }

                var position = _positions[symbol];
                if (!ShouldExit(momentum, _exitThreshold, position.HoldingDays))
                {
                    continue;
                }

                var fee = position.Quantity * position.LastPrice * _feeRate;
                _advisoryCash += position.Quantity * position.LastPrice - fee;
                _signals.Add(CreateSignal("SELL", symbol, position.LastPrice, position.Quantity,
                    $"momentum={momentum:F4} <= exit={_exitThreshold:F4} and holdingDays={position.HoldingDays}",
                    CalculateConfidence(momentum, _exitThreshold),
                    DetermineUrgency(momentum, _exitThreshold)));
                _positions.Remove(symbol);
            }

            var rankedEntries = RankEntryCandidates(
                momentumScores.ToDictionary(pair => ToTsCode(pair.Key), pair => pair.Value),
                _entryThreshold,
                _maxPositions)
                .ToList();

            var currentEquity = GetAdvisoryEquity();
            foreach (var tsCode in rankedEntries)
            {
                var symbol = _symbols.FirstOrDefault(candidate => ToTsCode(candidate) == tsCode);
                if (symbol == null || _positions.ContainsKey(symbol))
                {
                    continue;
                }
                if (_positions.Count >= _maxPositions)
                {
                    break;
                }
                if (!prices.TryGetValue(symbol, out var price) || price <= 0)
                {
                    continue;
                }

                var quantity = GetLotQuantity(Math.Min(_advisoryCash, currentEquity * _positionSize), price);
                if (quantity <= 0)
                {
                    continue;
                }

                var fee = quantity * price * _feeRate;
                _advisoryCash -= quantity * price + fee;
                _positions[symbol] = new AdvisoryPosition
                {
                    Symbol = symbol,
                    Quantity = quantity,
                    EntryPrice = price,
                    LastPrice = price,
                    HoldingDays = 0,
                };
                _signals.Add(CreateSignal("BUY", symbol, price, quantity,
                    $"momentum={momentumScores[symbol]:F4} >= entry={_entryThreshold:F4}",
                    CalculateConfidence(momentumScores[symbol], _entryThreshold),
                    DetermineUrgency(momentumScores[symbol], _entryThreshold)));
            }

            _dailyRows.Add(new AdvisoryDailyRow
            {
                TradeDate = tradeDate,
                Cash = _advisoryCash,
                MarketValue = _positions.Values.Sum(position => position.Quantity * position.LastPrice),
                TotalValue = GetAdvisoryEquity(),
                PositionCount = _positions.Count,
            });

            PersistOutputs();
        }

        private AdvisorySignal CreateSignal(string action, Symbol symbol, decimal price, int quantity, string reason, decimal confidence, string urgency)
        {
            var tsCode = ToTsCode(symbol);
            return new AdvisorySignal
            {
                SignalId = BuildSignalId(Time, action, tsCode),
                Timestamp = Time,
                Action = action,
                Symbol = tsCode,
                Name = symbol.Value,
                Price = price,
                Quantity = quantity,
                Reason = reason,
                Confidence = confidence,
                Urgency = urgency,
            };
        }

        private void PersistOutputs()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_signalFilePath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_portfolioSnapshotPath) ?? ".");
            Directory.CreateDirectory(Path.GetDirectoryName(_dailySummaryPath) ?? ".");

            File.WriteAllText(_signalFilePath, JsonConvert.SerializeObject(_signals, Formatting.Indented), System.Text.Encoding.UTF8);

            var marketValue = _positions.Values.Sum(position => position.Quantity * position.LastPrice);
            var totalValue = GetAdvisoryEquity();
            File.WriteAllText(_portfolioSnapshotPath, JsonConvert.SerializeObject(new
            {
                timestamp = Time,
                initial_capital = _initialCapital,
                cash = _advisoryCash,
                market_value = marketValue,
                total_value = totalValue,
                total_pnl = totalValue - _initialCapital,
                total_return = _initialCapital == 0 ? 0m : totalValue / _initialCapital - 1m,
                positions = _positions.Values.Select(position => new
                {
                    symbol = ToTsCode(position.Symbol),
                    name = position.Symbol.Value,
                    quantity = position.Quantity,
                    available_quantity = GetAvailableQuantity(position.Quantity, position.HoldingDays),
                    cost_price = position.EntryPrice,
                    last_price = position.LastPrice,
                    holding_days = position.HoldingDays,
                }).ToList()
            }, Formatting.Indented), System.Text.Encoding.UTF8);

            var lines = new List<string> { "trade_date,cash,market_value,total_value,position_count" };
            lines.AddRange(_dailyRows.Select(row => string.Join(",",
                row.TradeDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                row.Cash.ToString(CultureInfo.InvariantCulture),
                row.MarketValue.ToString(CultureInfo.InvariantCulture),
                row.TotalValue.ToString(CultureInfo.InvariantCulture),
                row.PositionCount.ToString(CultureInfo.InvariantCulture))));
            File.WriteAllLines(_dailySummaryPath, lines, System.Text.Encoding.UTF8);

            PersistSignalHistory();
        }

        private void PersistSignalHistory()
        {
            foreach (var signal in _signals.Skip(_persistedSignalCount))
            {
                var historyPath = GetSignalHistoryFilePath(_signalFilePath, signal.Timestamp);
                Directory.CreateDirectory(Path.GetDirectoryName(historyPath) ?? ".");
                File.AppendAllText(historyPath, JsonConvert.SerializeObject(signal, Formatting.None) + Environment.NewLine, System.Text.Encoding.UTF8);
            }

            _persistedSignalCount = _signals.Count;
        }

        private decimal GetAdvisoryEquity()
        {
            return _advisoryCash + _positions.Values.Sum(position => position.Quantity * position.LastPrice);
        }

        private static decimal CalculateConfidence(decimal score, decimal threshold)
        {
            var denominator = Math.Max(Math.Abs(threshold), 0.01m);
            return Math.Round(Math.Min(1m, Math.Abs(score) / denominator), 4);
        }

        private static string DetermineUrgency(decimal score, decimal threshold)
        {
            var denominator = Math.Max(Math.Abs(threshold), 0.01m);
            var ratio = Math.Abs(score) / denominator;
            if (ratio >= 1.5m)
            {
                return "high";
            }
            if (ratio >= 1m)
            {
                return "normal";
            }
            return "low";
        }

        private static int GetLotQuantity(decimal capital, decimal price)
        {
            if (capital <= 0 || price <= 0)
            {
                return 0;
            }

            return (int)(Math.Floor(capital / (price * LotSize)) * LotSize);
        }

        private static string ToTsCode(Symbol symbol)
        {
            var suffix = symbol.ID.Market == Market.SSE ? "SH" : "SZ";
            return $"{symbol.Value}.{suffix}";
        }

        private static IEnumerable<string> ParseUniverse(string parameter)
        {
            return (parameter ?? string.Empty)
                .Split(new[] { ',', ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(value => value.Trim())
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        private string ResolveOutputPath(string value, string defaultFileName)
        {
            var path = string.IsNullOrWhiteSpace(value) ? defaultFileName : value;
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(path, Environment.CurrentDirectory);
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var parameter = GetParameter(name);
            return decimal.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            var parameter = GetParameter(name);
            return int.TryParse(parameter, NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed)
                ? parsed
                : defaultValue;
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            var parameter = GetParameter(name);
            return DateTime.TryParse(parameter, CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var parsed)
                ? parsed
                : defaultValue;
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
            [JsonProperty("signal_id")]
            public string SignalId { get; set; }

            [JsonProperty("timestamp")]
            public DateTime Timestamp { get; set; }

            [JsonProperty("action")]
            public string Action { get; set; }

            [JsonProperty("symbol")]
            public string Symbol { get; set; }

            [JsonProperty("name")]
            public string Name { get; set; }

            [JsonProperty("price")]
            public decimal Price { get; set; }

            [JsonProperty("quantity")]
            public int Quantity { get; set; }

            [JsonProperty("reason")]
            public string Reason { get; set; }

            [JsonProperty("confidence")]
            public decimal Confidence { get; set; }

            [JsonProperty("urgency")]
            public string Urgency { get; set; }
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
