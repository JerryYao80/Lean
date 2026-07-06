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
using QuantConnect.Algorithm.CSharp.Common;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Overnight Return Anomaly strategy based on arXiv 2010.01727
    /// (Knuteson, 2020: "Strikingly Suspicious Overnight and Intraday Returns").
    ///
    /// Key insight adapted for A-shares: certain T+0 ETFs exhibit systematically
    /// positive overnight returns (close-to-open gap). This strategy:
    /// 1. Ranks T+0 ETFs by trailing overnight return momentum
    /// 2. Allocates to top-N ETFs with the strongest overnight premium
    /// 3. Uses volatility scaling to target a constant portfolio volatility
    /// 4. Rebalances weekly
    ///
    /// Unlike the US market pattern (positive overnight, negative intraday),
    /// A-share equity ETFs show the OPPOSITE pattern (negative overnight, positive
    /// intraday). However, gold ETFs (518880) and money market ETFs show the
    /// classic positive overnight premium. This strategy exploits that anomaly.
    /// </summary>
    public class AShareOvernightAnomalyAlgorithm : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private const int LotSize = 100;

        private decimal _initialCapital;
        private int _lookbackDays;
        private int _topN;
        private decimal _targetVol;
        private int _volLookbackDays;
        private decimal _positionSize;
        private decimal _feeRate;
        private int _rebalanceFrequencyDays;
        private decimal _minOvernightMomentum;

        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private string _dailySummaryPath;
        private string _summaryFilePath;

        private readonly List<Symbol> _universe = new();
        private readonly List<OvernightSignal> _signals = new();
        private readonly List<decimal> _dailyEquity = new();
        private readonly Dictionary<Symbol, List<decimal>> _overnightReturns = new();
        private readonly Dictionary<Symbol, List<decimal>> _intradayReturns = new();
        private int _persistedSignalCount;
        private DateTime _lastEvaluationDate;
        private DateTime _lastRebalanceDate;

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1_000_000m);
            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetBenchmark(x => 0m);

            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2025, 12, 31)));

            _lookbackDays = GetIntParameter("lookback-days", 20);
            _topN = GetIntParameter("top-n", 3);
            _targetVol = GetDecimalParameter("target-vol", 0.12m);
            _volLookbackDays = GetIntParameter("vol-lookback-days", 60);
            _positionSize = GetDecimalParameter("position-size", 0.30m);
            _feeRate = GetDecimalParameter("fee-rate", 0.0005m);
            _rebalanceFrequencyDays = GetIntParameter("rebalance-frequency-days", 5);
            _minOvernightMomentum = GetDecimalParameter("min-overnight-momentum", 0.001m);

            _signalFilePath = ResolveOutputPath(GetParameter("signal-file"), "ashare-overnight-anomaly-signals.json");
            _portfolioSnapshotPath = ResolveOutputPath(GetParameter("portfolio-snapshot-file"), "ashare-overnight-anomaly-portfolio.json");
            _dailySummaryPath = ResolveOutputPath(GetParameter("daily-summary-file"), "ashare-overnight-anomaly-daily.csv");
            _summaryFilePath = ResolveOutputPath(GetParameter("summary-file"), "ashare-overnight-anomaly-summary.json");

            // T+0 ETFs with known overnight return patterns
            var defaultUniverse = "518880.SH,510050.SH,510300.SH,510500.SH,510880.SH,511010.SH,511260.SH,159919.SZ";
            var tsCodes = ParseUniverse(GetParameter("universe") ?? defaultUniverse);

            foreach (var tsCode in tsCodes)
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
                    equity.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
                    _universe.Add(equity.Symbol);
                    _overnightReturns[equity.Symbol] = new List<decimal>();
                    _intradayReturns[equity.Symbol] = new List<decimal>();
                }
                catch { }
            }

            if (_universe.Count == 0)
                throw new InvalidOperationException("No symbols loaded. Check universe parameter.");

            Schedule.On(DateRules.EveryDay(_universe[0]), TimeRules.AfterMarketOpen(_universe[0], 5), EvaluateDaily);
            PersistOutputs();

            Log($"AShareOvernightAnomalyAlgorithm: {_universe.Count} ETFs, lookback={_lookbackDays}, topN={_topN}, targetVol={_targetVol:P0}");
        }

        public override void OnData(Slice data)
        {
            // Track overnight and intraday returns for each symbol
            foreach (var sym in _universe)
            {
                if (!data.Bars.TryGetValue(sym, out var bar)) continue;
                var security = Securities[sym];
                if (security == null || bar.Open <= 0 || bar.Close <= 0) continue;

                var prevCloseValue = security.GetLastData()?.Price ?? 0m;
                if (prevCloseValue <= 0)
                {
                    continue;
                }

                var overnightRet = bar.Open / prevCloseValue - 1m;
                var intradayRet = bar.Close / bar.Open - 1m;

                _overnightReturns[sym].Add(overnightRet);
                _intradayReturns[sym].Add(intradayRet);

                // Keep only the last volLookbackDays observations
                if (_overnightReturns[sym].Count > _volLookbackDays + 10)
                {
                    _overnightReturns[sym].RemoveRange(0, _overnightReturns[sym].Count - _volLookbackDays - 10);
                    _intradayReturns[sym].RemoveRange(0, _intradayReturns[sym].Count - _volLookbackDays - 10);
                }
            }
        }

        public override void OnEndOfAlgorithm()
        {
            PersistOutputs();
            WriteSummary();
            Log($"AShareOvernightAnomalyAlgorithm finished. Signals: {_signals.Count}");
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

            var shouldRebalance = _lastRebalanceDate == default
                || (today - _lastRebalanceDate).Days >= _rebalanceFrequencyDays;

            if (shouldRebalance)
            {
                Rebalance(today);
                _lastRebalanceDate = today;
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
            // 1. Compute overnight return metrics for each ETF
            var scores = new List<(Symbol Sym, decimal OvernightMomentum, decimal OvernightSharpe, decimal RealizedVol)>();

            foreach (var sym in _universe)
            {
                var ovrRets = _overnightReturns[sym];
                if (ovrRets.Count < _lookbackDays) continue;

                var recent = ovrRets.Skip(ovrRets.Count - _lookbackDays).ToList();
                var overnightMomentum = recent.Average();

                // Overnight return Sharpe ratio (risk-adjusted signal)
                var ovStd = recent.Count > 1 ? (decimal)Math.Sqrt((double)recent.Select(r => (r - overnightMomentum) * (r - overnightMomentum)).Average()) : 1m;
                var overnightSharpe = ovStd > 0.0001m ? overnightMomentum / ovStd * (decimal)Math.Sqrt(252) : 0m;

                // Compute realized volatility from total daily returns
                var totalRets = new List<decimal>();
                var ov = _overnightReturns[sym];
                var id = _intradayReturns[sym];
                var minLen = Math.Min(ov.Count, id.Count);
                var startIdx = Math.Max(0, minLen - _volLookbackDays);
                for (int i = startIdx; i < minLen; i++)
                {
                    totalRets.Add(ov[i] + id[i]); // approximate: (1+ov)(1+id)-1 ≈ ov+id
                }

                var realizedVol = ComputeAnnualizedVol(totalRets);
                scores.Add((sym, overnightMomentum, overnightSharpe, realizedVol));
            }

            if (scores.Count == 0)
            {
                Log($"[{today:yyyyMMdd}] No scores available, skipping rebalance.");
                return;
            }

            // 2. Select top-N ETFs by overnight Sharpe ratio (risk-adjusted overnight premium)
            //    Only select ETFs with positive overnight Sharpe (persistent overnight premium)
            var candidates = scores
                .Where(s => s.OvernightSharpe > 0)
                .OrderByDescending(s => s.OvernightSharpe)
                .Take(_topN)
                .ToList();

            // Fallback: if no positive Sharpe, take the best one
            if (candidates.Count == 0)
            {
                candidates = scores.OrderByDescending(s => s.OvernightSharpe).Take(1).ToList();
            }

            var selected = candidates;

            var targetSymbols = selected.Select(s => s.Sym).ToHashSet();

            // 3. Compute volatility-scaled position sizes
            var portfolioVol = ComputeAnnualizedVol(
                _dailyEquity.Count > _volLookbackDays
                    ? DailyReturns(_dailyEquity, _volLookbackDays)
                    : DailyReturns(_dailyEquity, Math.Max(2, _dailyEquity.Count)));

            var volScale = portfolioVol > 0.01m ? _targetVol / portfolioVol : 1m;
            volScale = Math.Max(0.25m, Math.Min(2.0m, volScale));

            var allocPerPosition = _positionSize * volScale / selected.Count;

            // 4. Liquidate positions not in target
            foreach (var holding in Portfolio.Values.Where(h => h.Invested).ToList())
            {
                if (targetSymbols.Contains(holding.Symbol)) continue;
                var qty = (int)(Math.Floor(holding.Quantity / LotSize) * LotSize);
                if (qty > 0)
                {
                    MarketOrder(holding.Symbol, -qty);
                    var score = scores.FirstOrDefault(s => s.Sym == holding.Symbol);
                    _signals.Add(CreateSignal("SELL", holding.Symbol, holding.Price, qty,
                        $"rebalance: ov_sharpe not in top-{_topN}", 0.8m, "normal"));
                }
            }

            // 5. Enter new positions
            var equity = Portfolio.TotalPortfolioValue;
            foreach (var item in selected)
            {
                var price = Securities[item.Sym].Price;
                if (price <= 0) continue;

                var currentQty = Portfolio.ContainsKey(item.Sym) ? (int)Portfolio[item.Sym].Quantity : 0;
                var targetQty = GetLotQuantity(equity * allocPerPosition, price);

                if (targetQty > currentQty)
                {
                    var buyQty = targetQty - currentQty;
                    if (buyQty >= LotSize)
                    {
                        MarketOrder(item.Sym, buyQty);
                        _signals.Add(CreateSignal("BUY", item.Sym, price, buyQty,
                            $"ov_sharpe={item.OvernightSharpe:F2} ovm={item.OvernightMomentum:F4} vol={item.RealizedVol:F2%} vol_scale={volScale:F2} alloc={allocPerPosition:P1}",
                            0.75m, "normal"));
                    }
                }
                else if (targetQty < currentQty)
                {
                    var sellQty = currentQty - targetQty;
                    sellQty = (int)(Math.Floor((decimal)sellQty / LotSize) * LotSize);
                    if (sellQty >= LotSize)
                    {
                        MarketOrder(item.Sym, -sellQty);
                        _signals.Add(CreateSignal("REDUCE", item.Sym, price, sellQty,
                            $"vol_scale={volScale:F2} alloc={allocPerPosition:P1}", 0.7m, "normal"));
                    }
                }
            }

            var selectedNames = string.Join(", ", selected.Select(s => $"{ToTsCode(s.Sym)}(ovm={s.OvernightMomentum:F4},ovs={s.OvernightSharpe:F2})"));
            Log($"[{today:yyyyMMdd}] Rebalance: volScale={volScale:F2} selected=[{selectedNames}] held={Portfolio.Values.Count(h => h.Invested)}");
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

            // Compute overnight stats for portfolio snapshot
            var ovStats = new Dictionary<string, string>();
            foreach (var sym in _universe)
            {
                var ovrRets = _overnightReturns[sym];
                if (ovrRets.Count >= _lookbackDays)
                {
                    var recent = ovrRets.Skip(ovrRets.Count - _lookbackDays).ToList();
                    ovStats[ToTsCode(sym)] = $"ovm={recent.Average():F4}";
                }
            }

            File.WriteAllText(_portfolioSnapshotPath, JsonConvert.SerializeObject(new
            {
                timestamp = Time,
                initial_capital = _initialCapital,
                cash = Portfolio.Cash,
                market_value = mv,
                total_value = total,
                total_pnl = total - _initialCapital,
                total_return = _initialCapital == 0 ? 0m : total / _initialCapital - 1m,
                overnight_momentum_stats = ovStats,
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
            var totalOrders = Transactions.OrdersCount;

            File.WriteAllText(_summaryFilePath, JsonConvert.SerializeObject(new
            {
                algorithm_id = "AShareOvernightAnomalyAlgorithm",
                paper_source = "arXiv 2010.01727 (Knuteson, 2020)",
                strategy = "Overnight Return Anomaly: hold ETFs with strongest overnight return premium, vol-scaled",
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
            }, Formatting.Indented), System.Text.Encoding.UTF8);

            SetSummaryStatistic("Total Return", (double)totalReturn);
            SetSummaryStatistic("Net Profit", (double)netProfit);
            SetSummaryStatistic("Sharpe Ratio", (double)sharpe);
            SetSummaryStatistic("Drawdown", (double)maxDrawdown);
            SetSummaryStatistic("Total Orders", totalOrders);
            SetSummaryStatistic("Total Fees", (double)totalFees);
            SetSummaryStatistic("End Equity", (double)total);
        }

        // --- Utility methods ---

        public static string ToTsCode(Symbol sym) =>
            $"{sym.Value}.{(sym.ID.Market == Market.SSE ? "SH" : "SZ")}";

        public static int GetLotQuantity(decimal capital, decimal price)
        {
            if (capital <= 0 || price <= 0) return 0;
            return (int)(Math.Floor(capital / (price * LotSize)) * LotSize);
        }

        public static List<string> ParseUniverse(string universeStr)
        {
            if (string.IsNullOrWhiteSpace(universeStr)) return new List<string>();
            return universeStr.Split(new[] { ',', ' ', ';' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(s => s.Trim())
                .Where(s => !string.IsNullOrEmpty(s))
                .Distinct()
                .ToList();
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

        public static decimal ComputeAnnualizedVol(IReadOnlyList<decimal> dailyReturns)
        {
            if (dailyReturns.Count < 5) return 0.2m;
            var mean = dailyReturns.Average();
            var variance = dailyReturns.Select(r => (r - mean) * (r - mean)).Average();
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
        }

        public static List<decimal> DailyReturns(IReadOnlyList<decimal> equity, int lookback)
        {
            var returns = new List<decimal>();
            var start = Math.Max(1, equity.Count - lookback);
            for (int i = start; i < equity.Count; i++)
            {
                if (equity[i - 1] > 0)
                    returns.Add(equity[i] / equity[i - 1] - 1m);
            }
            return returns;
        }

        private OvernightSignal CreateSignal(string action, Symbol sym, decimal price, int qty, string reason, decimal confidence, string urgency) =>
            new OvernightSignal
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

        /// <summary>IOptimizableStrategy: 与 manifest.parameter_space 一致 (manifest_lint 校验).</summary>
        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "lookback-days", "top-n", "target-vol", "vol-lookback-days",
            "position-size", "rebalance-frequency-days", "min-overnight-momentum"
        };

        /// <summary>IRlStateExportable: 序列化 RL 状态 JSON, 字段须与 manifest.state_schema 一致.
        /// 简化版 (mature strategy, 零侵入): pnl_1d/days_held/drawdown 首期置 0/基础值.</summary>
        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new {
                    sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv,
                    pnl_1d = 0m, days_held = 0
                }).ToList();
            return JsonConvert.SerializeObject(new {
                ts = algo.Time.ToString("o"), strategy = "AShareOvernightAnomalyAlgorithm",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                drawdown = 0m, n_open_positions = positions.Count
            });
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

        public sealed class OvernightSignal
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
