using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
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
        private string _monteCarloFilePath;

        private readonly List<Symbol> _universe = new();
        private readonly List<AdvisorySignal> _signals = new();
        private readonly List<decimal> _dailyEquity = new();
        private int _persistedSignalCount;
        private DateTime _lastEvaluationDate;
        private DateTime _lastRebalanceMonth;

        private readonly Dictionary<string, Dictionary<string, FactorRow>> _factorData = new();
        private readonly HashSet<string> _allTsCodes = new();

        // Monte Carlo parameters
        private bool _monteCarloEnabled;
        private int _monteCarloTrials;
        private int _monteCarloHorizonDays;
        private int _monteCarloBlockSize;
        private int _monteCarloSeed;
        private decimal _monteCarloFactorPerturbationScale;

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

            _factorFilePath = ResolveOutputPath(GetParameter("factor-file"), "ashare-sector-smallcap-factors.csv");
            _signalFilePath = ResolveOutputPath(GetParameter("signal-file"), "ashare-sector-smallcap-signals.json");
            _portfolioSnapshotPath = ResolveOutputPath(GetParameter("portfolio-snapshot-file"), "ashare-sector-smallcap-portfolio.json");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "ashare-sector-smallcap-daily.csv");
            _summaryFilePath = ResolveOutputPath(GetParameter("summary-file"), "ashare-sector-smallcap-summary.json");
            _monteCarloFilePath = ResolveOutputPath(GetParameter("monte-carlo-file"), "ashare-sector-smallcap-monte-carlo.json");

            _monteCarloEnabled = GetBoolParameter("monte-carlo-enabled", true);
            _monteCarloTrials = GetIntParameter("monte-carlo-trials", 500);
            _monteCarloHorizonDays = GetIntParameter("monte-carlo-horizon-days", 63);
            _monteCarloBlockSize = GetIntParameter("monte-carlo-block-size", 5);
            _monteCarloSeed = GetIntParameter("monte-carlo-seed", 42);
            _monteCarloFactorPerturbationScale = GetDecimalParameter("monte-carlo-factor-perturbation-scale", 0.15m);

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
            Log($"AShareSectorSmallCapAlgorithm finished. Signals: {_signals.Count}");
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            if (orderEvent.Status == OrderStatus.Filled)
            {
                Log($"[ORDER] {orderEvent.Direction} {orderEvent.Symbol.Value} " +
                    $"qty={orderEvent.FillQuantity} price={orderEvent.FillPrice:C2} " +
                    $"fee={orderEvent.OrderFee:C2}");
            }
        }

        private void EvaluateDaily()
        {
            var today = Time.Date;
            if (_lastEvaluationDate == today) return;
            _lastEvaluationDate = today;

            var equity = Portfolio.TotalPortfolioValue;
            _dailyEquity.Add(equity);

            var isRebalanceDay = today.Month != _lastRebalanceMonth.Month || _lastRebalanceMonth == default;
            if (isRebalanceDay)
            {
                _lastRebalanceMonth = today;
                Rebalance(today);
            }

            var invested = Portfolio.Values.Where(h => h.Invested).ToList();
            var investedValue = invested.Sum(h => h.HoldingsValue);
            var netReturn = _initialCapital > 0 ? equity / _initialCapital - 1m : 0m;
            var drawdown = ComputeMaxDrawdown(_dailyEquity);

            SetRuntimeStatistic("Positions", invested.Count.ToString(CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Equity", equity.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Cash", Portfolio.Cash.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Invested", investedValue.ToString("F0", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Return", netReturn.ToString("P2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Drawdown", drawdown.ToString("P2", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("Signals", _signals.Count.ToString(CultureInfo.InvariantCulture));

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
                .ToDictionary(g => g.Key, g => g.Average(r => r.Momentum20d!.Value));

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

            var targetSymbols = new HashSet<Symbol>();
            foreach (var tsCode in targetTsCodes)
            {
                var sym = _universe.FirstOrDefault(s => ToTsCode(s) == tsCode);
                if (sym != null) targetSymbols.Add(sym);
            }

            foreach (var holding in Portfolio.Values.Where(h => h.Invested).ToList())
            {
                if (targetSymbols.Contains(holding.Symbol)) continue;
                var qty = (int)(Math.Floor(holding.Quantity / LotSize) * LotSize);
                if (qty > 0)
                {
                    MarketOrder(holding.Symbol, -qty);
                    _signals.Add(CreateSignal("SELL", holding.Symbol, holding.Price, qty,
                        $"rebalance: sector not in top-{_topSectors} or cap/pb filter", 0.8m, "normal"));
                }
            }

            var equity = Portfolio.TotalPortfolioValue;
            foreach (var sym in targetSymbols)
            {
                if (Portfolio.ContainsKey(sym) && Portfolio[sym].Invested) continue;
                var price = Securities[sym].Price;
                if (price <= 0) continue;
                var alloc = equity * _positionSize;
                var qty = GetLotQuantity(alloc, price);
                if (qty <= 0) continue;
                MarketOrder(sym, qty);
                var factor = factors.GetValueOrDefault(ToTsCode(sym));
                _signals.Add(CreateSignal("BUY", sym, price, qty,
                    $"sector={factor?.Sector ?? "?"} mv={factor?.TotalMvWan:F0}万 pb={factor?.Pb:F2}",
                    0.75m, "normal"));
            }

            Log($"[{dateKey}] Rebalance: topSectors=[{string.Join(",", topSectors)}] target={targetTsCodes.Count} held={Portfolio.Values.Count(h => h.Invested)}");
        }

        private void PersistOutputs()
        {
            EnsureDir(_signalFilePath);
            EnsureDir(_portfolioSnapshotPath);
            EnsureDir(_dailySummaryPath);

            File.WriteAllText(_signalFilePath,
                JsonConvert.SerializeObject(_signals, Formatting.Indented), System.Text.Encoding.UTF8);

            var invested = Portfolio.Values.Where(h => h.Invested).ToList();
            var mv = invested.Sum(h => h.HoldingsValue);
            var total = Portfolio.TotalPortfolioValue;
            File.WriteAllText(_portfolioSnapshotPath, JsonConvert.SerializeObject(new
            {
                timestamp = Time,
                initial_capital = _initialCapital,
                cash = Portfolio.Cash,
                market_value = mv,
                total_value = total,
                total_pnl = total - _initialCapital,
                total_return = _initialCapital == 0 ? 0m : total / _initialCapital - 1m,
                positions = invested.Select(h => new
                {
                    symbol = ToTsCode(h.Symbol),
                    quantity = h.Quantity,
                    average_price = h.AveragePrice,
                    market_price = h.Price,
                    market_value = h.HoldingsValue,
                    unrealized_pnl = h.UnrealizedProfit,
                }).ToList(),
            }, Formatting.Indented), System.Text.Encoding.UTF8);

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
            EnsureDir(_summaryFilePath);
            var total = Portfolio.TotalPortfolioValue;
            var totalReturn = _initialCapital == 0 ? 0m : total / _initialCapital - 1m;
            var netProfit = total - _initialCapital;
            var totalFees = Portfolio.TotalFees;
            var maxDrawdown = ComputeMaxDrawdown(_dailyEquity);
            var sharpe = ComputeSharpe(_dailyEquity);

            // Compute trade statistics from LEAN's closed trades
            var closedTrades = TradeBuilder.ClosedTrades;
            var totalTrades = closedTrades.Count;
            var winningTrades = closedTrades.Count(t => t.ProfitLoss > 0);
            var losingTrades = closedTrades.Count(t => t.ProfitLoss <= 0);
            var winRate = totalTrades > 0 ? (decimal)winningTrades / totalTrades : 0m;
            var lossRate = totalTrades > 0 ? (decimal)losingTrades / totalTrades : 0m;
            var avgWin = winningTrades > 0 ? closedTrades.Where(t => t.ProfitLoss > 0).Average(t => t.ProfitLoss) : 0m;
            var avgLoss = losingTrades > 0 ? closedTrades.Where(t => t.ProfitLoss <= 0).Average(t => Math.Abs(t.ProfitLoss)) : 1m;
            var profitLossRatio = avgLoss != 0 ? avgWin / avgLoss : 0m;
            var expectancy = winRate * profitLossRatio - lossRate;
            var totalOrders = Transactions.OrdersCount;

            // Portfolio turnover from daily equity changes
            var turnover = ComputePortfolioTurnover();

            File.WriteAllText(_summaryFilePath, JsonConvert.SerializeObject(new
            {
                algorithm_id = "AShareSectorSmallCapAlgorithm",
                start_date = StartDate.ToString("yyyy-MM-dd"),
                end_date = EndDate.ToString("yyyy-MM-dd"),
                initial_capital = _initialCapital,
                final_value = total,
                total_return = totalReturn,
                total_signals = _signals.Count,
                max_drawdown = maxDrawdown,
                sharpe_ratio = sharpe,
                total_fees = totalFees,
                total_orders = totalOrders,
                closed_trades = totalTrades,
                winning_trades = winningTrades,
                losing_trades = losingTrades,
                win_rate = winRate,
                profit_loss_ratio = profitLossRatio,
            }, Formatting.Indented), System.Text.Encoding.UTF8);

            // Publish summary statistics for Grafana (lean_backtest_stat measurement via export script)
            SetSummaryStatistic("Total Return", (double)totalReturn);
            SetSummaryStatistic("Net Profit", (double)netProfit);
            SetSummaryStatistic("Sharpe Ratio", (double)sharpe);
            SetSummaryStatistic("Drawdown", (double)maxDrawdown);
            SetSummaryStatistic("Win Rate", (double)winRate);
            SetSummaryStatistic("Loss Rate", (double)lossRate);
            SetSummaryStatistic("Profit-Loss Ratio", Math.Round((double)profitLossRatio, 2));
            SetSummaryStatistic("Expectancy", Math.Round((double)expectancy, 3));
            SetSummaryStatistic("Average Win", (double)avgWin);
            SetSummaryStatistic("Average Loss", (double)avgLoss);
            SetSummaryStatistic("Total Orders", totalOrders);
            SetSummaryStatistic("Total Trades", totalTrades);
            SetSummaryStatistic("Winning Trades", winningTrades);
            SetSummaryStatistic("Losing Trades", losingTrades);
            SetSummaryStatistic("Portfolio Turnover", (double)turnover);
            SetSummaryStatistic("Total Fees", (double)totalFees);
            SetSummaryStatistic("Total Signals", _signals.Count);
            SetSummaryStatistic("End Equity", (double)total);

            PublishMonteCarloSummary();
        }

        private decimal ComputePortfolioTurnover()
        {
            if (_dailyEquity.Count < 2) return 0m;
            var totalSaleVolume = Portfolio.TotalSaleVolume;
            var avgEquity = _dailyEquity.Average();
            return avgEquity > 0 ? totalSaleVolume / avgEquity / _dailyEquity.Count : 0m;
        }

        private void PublishMonteCarloSummary()
        {
            if (!_monteCarloEnabled) return;

            var dailyReturns = new List<StrategyMonteCarloDailyReturn>();
            for (int i = 1; i < _dailyEquity.Count; i++)
            {
                if (_dailyEquity[i - 1] > 0)
                {
                    dailyReturns.Add(new StrategyMonteCarloDailyReturn
                    {
                        TradeDate = StartDate.AddDays(i),
                        NetReturn = (double)(_dailyEquity[i] / _dailyEquity[i - 1] - 1m)
                    });
                }
            }

            if (dailyReturns.Count == 0)
            {
                Log("Monte Carlo summary skipped: insufficient daily returns.");
                return;
            }

            var summary = StrategyMonteCarloStatistics.Compute(
                new StrategyMonteCarloConfig
                {
                    Enabled = true,
                    Trials = _monteCarloTrials,
                    HorizonDays = _monteCarloHorizonDays,
                    BlockSize = _monteCarloBlockSize,
                    Seed = _monteCarloSeed,
                    FactorPerturbationScale = (double)_monteCarloFactorPerturbationScale
                },
                dailyReturns);

            if (!summary.HasData)
            {
                Log("Monte Carlo summary skipped: insufficient inputs to generate simulation paths.");
                return;
            }

            foreach (var statistic in summary.ToSummaryStatistics())
            {
                SetSummaryStatistic(statistic.Key, statistic.Value);
            }

            WriteMonteCarloJson(summary);
        }

        private void WriteMonteCarloJson(StrategyMonteCarloSummary summary)
        {
            if (summary == null || !summary.HasData) return;

            EnsureDir(_monteCarloFilePath);
            var payload = new
            {
                generatedAtUtc = DateTime.UtcNow.ToString("o"),
                scenarios = new Dictionary<string, Dictionary<string, object>>
                {
                    ["baseline"] = new Dictionary<string, object>
                    {
                        ["scenario"] = "baseline",
                        ["lossProbability"] = summary.BaselineLossProbability,
                        ["medianTotalReturn"] = summary.CombinedMedianReturn,
                        ["p95Drawdown"] = summary.CombinedP95Drawdown,
                    },
                    ["combinedStress"] = new Dictionary<string, object>
                    {
                        ["scenario"] = "combinedStress",
                        ["lossProbability"] = summary.CombinedLossProbability,
                        ["medianTotalReturn"] = summary.CombinedMedianReturn,
                        ["p95Drawdown"] = summary.CombinedP95Drawdown,
                    },
                },
            };

            File.WriteAllText(_monteCarloFilePath,
                JsonConvert.SerializeObject(payload, Formatting.Indented),
                System.Text.Encoding.UTF8);
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

        private bool GetBoolParameter(string name, bool def)
        {
            var p = GetParameter(name);
            if (string.IsNullOrEmpty(p)) return def;
            return p.Equals("true", StringComparison.OrdinalIgnoreCase) || p == "1";
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
    }
}
