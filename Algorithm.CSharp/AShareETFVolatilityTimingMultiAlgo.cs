/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * LEAN Algorithmic Trading Engine v2.0.
 *
 * Multi-ETF Volatility Timing Strategy (V2 - percentile-normalized)
 *
 * Uses PRE-COMPUTED option IV signals (from multiple A-share ETFs) to guide
 * multi-asset allocation. Monitors 3 ETFs (510050, 510300, 510500) and
 * allocates capital based on IV percentile ranking (cross-ETF fair comparison).
 *
 * V2 Improvements over V1:
 * - IV percentile (vs own 1Y history) replaces absolute IV → fair cross-ETF
 *   comparison. 510500's structurally-high IV no longer falsely ranks as "fear".
 * - Long backtest window 2020-2025 to validate over full 510300 history.
 * - Defensive mode: when all ETFs' IV percentile low (complacency), raise cash.
 *
 * Strategy Logic:
 * - For each ETF: iv_pct = rank of today's ATM IV in trailing 252-day IV history
 * - Score = iv_pct (higher = more elevated vs own history = fear)
 * - Rank ETFs by score; leader gets biggest allocation
 * - If best score < 0.30 (all complacent) → defensive, shift to cash
 * - Cooldown 5 days to curb over-trading
 *
 * Data sources:
 *   Data/alternative/ashare-implied-volatility/sse/daily/510050.csv (49 days, 2024)
 *   Data/alternative/ashare-implied-volatility/sse/daily/510300.csv (1005 days, 2020-2025)
 *   Data/alternative/ashare-implied-volatility/sse/daily/510500.csv (343 days, 2022-2025)
 *   Data/equity/sse/daily/*.csv (full price history 2018-2025)
 */

using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.AShare;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;
using QuantConnect.Util;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.IO;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareETFVolatilityTimingMulti : QCAlgorithm
    {
        private readonly Dictionary<string, Symbol> _etfSymbols = new();
        private readonly Dictionary<string, List<AShareImpliedVolatilityData>> _ivData = new();
        private readonly Dictionary<Symbol, RollingWindow<decimal>> _priceHistory = new();

        // Strategy parameters
        private const int LotSize = 100;
        private const int LookbackDays = 20;               // For realized vol (price window)
        private const int CooldownDays = 5;                // Minimum days between rebalances
        private const int IvPercentileWindow = 252;        // 1Y IV history for percentile rank
        private const int IvPercentileMinHistory = 30;     // Min IV data points before scoring

        // Allocation tiers by aggregate fear level (best ETF's IV percentile)
        private const decimal LeaderAllocHigh = 0.40m;     // Fear regime (best pct > 0.6)
        private const decimal RunnerUpHigh = 0.21m;
        private const decimal LaggardHigh = 0.09m;

        private const decimal LeaderAllocMid = 0.25m;      // Normal regime (best pct 0.3-0.6)
        private const decimal RunnerUpMid = 0.15m;
        private const decimal LaggardMid = 0.05m;

        private const decimal LeaderAllocLow = 0.12m;      // Complacent regime (best pct < 0.3)
        private const decimal RunnerUpLow = 0.04m;
        private const decimal LaggardLow = 0.00m;

        private DateTime _lastRebalanceDate = DateTime.MinValue;

        public override void Initialize()
        {
            SetStartDate(2020, 1, 2);
            SetEndDate(2025, 4, 1);

            // Set CNY account currency for A-share trading
            SetAccountCurrency("CNY");
            SetCash(1000000);
            Portfolio.CashBook.Add("CNY", 1000000, 1.0m);

            // Add 3 ETFs to monitor
            AddETF("510050", "sse");
            AddETF("510300", "sse");
            AddETF("510500", "sse");

            // Load IV data for each ETF
            LoadIVData("510050", "/alternative/ashare-implied-volatility/sse/daily/510050.csv");
            LoadIVData("510300", "/alternative/ashare-implied-volatility/sse/daily/510300.csv");
            LoadIVData("510500", "/alternative/ashare-implied-volatility/sse/daily/510500.csv");

            // Benchmark vs 510300 buy-and-hold (CSI 300, most data-complete)
            SetBenchmark(_etfSymbols["510300"]);

            SetWarmUp(LookbackDays, Resolution.Daily);

            Log($"[INIT] Multi-ETF Volatility Timing Strategy V2 (percentile-normalized)");
            Log($"[INIT] Window: {Time:yyyy-MM-dd} ~ {EndDate:yyyy-MM-dd} (5+ years)");
            Log($"[INIT] Monitoring: 510050 (SSE 50), 510300 (CSI 300), 510500 (CSI 500)");
            Log($"[INIT] IV percentile window: {IvPercentileWindow}d, min history: {IvPercentileMinHistory}d");
            Log($"[INIT] Rebalance cooldown: {CooldownDays} days");
            Log($"[INIT] Alloc tiers: High(L40/R21/L9) Mid(L25/R15/L5) Low(L12/R4/L0)");
        }

        private void AddETF(string ticker, string market)
        {
            var equity = AddEquity(ticker, Resolution.Daily, market: market);
            equity.FeeModel = new AShareETFFeeModel();
            _etfSymbols.Add(ticker, equity.Symbol);
            _priceHistory.Add(equity.Symbol, new RollingWindow<decimal>(LookbackDays));
            Log($"[INIT] Added ETF: {ticker}, Symbol={equity.Symbol}");
        }

        private void LoadIVData(string ticker, string relativePath)
        {
            var csvPath = Globals.DataFolder + relativePath;
            if (!File.Exists(csvPath))
            {
                Log($"[INIT] WARNING: IV CSV not found: {csvPath}");
                _ivData.Add(ticker, new List<AShareImpliedVolatilityData>());
                return;
            }

            try
            {
                var ivList = new List<AShareImpliedVolatilityData>();
                var lines = File.ReadAllLines(csvPath);
                if (lines.Length < 2)
                {
                    _ivData.Add(ticker, ivList);
                    Log($"[INIT] Empty IV CSV for {ticker}");
                    return;
                }

                // Header-driven parsing: CSV column layout differs across ETFs
                // (510050 has 16 cols incl. iv_skew_surface_minus; 510300/510500 have 13 cols)
                var header = lines[0].Split(',');
                var colIdx = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                for (int i = 0; i < header.Length; i++)
                {
                    colIdx[header[i].Trim()] = i;
                }

                int idxAtm = colIdx.TryGetValue("atm_iv", out var a) ? a : -1;
                int idxSkew = colIdx.TryGetValue("skew", out var s) ? s : -1;
                int idxSurf = colIdx.TryGetValue("iv_skew_surface_minus", out var sf) ? sf : -1;
                int idxVix = colIdx.TryGetValue("vix", out var v) ? v : -1;
                int idxDate = colIdx.TryGetValue("trade_date", out var d) ? d : 0;

                for (int li = 1; li < lines.Length; li++)
                {
                    var parts = lines[li].Split(',');
                    if (parts.Length < 2) continue;

                    try
                    {
                        var tradeDate = DateTime.ParseExact(parts[idxDate].Trim(), "yyyyMMdd", CultureInfo.InvariantCulture);
                        var ivData = new AShareImpliedVolatilityData
                        {
                            Time = tradeDate,
                            AtmIv = GetColumnDecimal(parts, idxAtm),
                            Skew = GetColumnDecimal(parts, idxSkew),
                            IvSkewSurfaceMinus = GetColumnDecimal(parts, idxSurf),
                            Vix = GetColumnDecimal(parts, idxVix)
                        };
                        ivList.Add(ivData);
                    }
                    catch { }
                }

                _ivData.Add(ticker, ivList);
                Log($"[INIT] Loaded {ivList.Count} IV data points for {ticker} (cols: {header.Length}, has_surface={idxSurf >= 0})");
            }
            catch (Exception ex)
            {
                Log($"[INIT] Error loading IV CSV for {ticker}: {ex.Message}");
                _ivData.Add(ticker, new List<AShareImpliedVolatilityData>());
            }
        }

        private static decimal GetColumnDecimal(string[] parts, int idx)
        {
            if (idx < 0 || idx >= parts.Length) return 0m;
            return decimal.TryParse(parts[idx].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var val) ? val : 0m;
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Track price history for each ETF
            foreach (var kvp in _etfSymbols)
            {
                if (data.Bars.TryGetValue(kvp.Value, out var bar))
                {
                    _priceHistory[kvp.Value].Add(bar.Close);
                }
            }

            // Need price history for all ETFs to compute RV
            if (_priceHistory.Values.Any(h => h.Count < 10))
            {
                return;
            }

            // Rebalance once per cooldown period
            if ((Time.Date - _lastRebalanceDate).TotalDays >= CooldownDays)
            {
                RebalancePositions();
            }
        }

        private void RebalancePositions()
        {
            var scoredETFs = new List<(string ticker, Symbol symbol, decimal ivPct, decimal score, string reason)>();

            foreach (var (ticker, symbol) in _etfSymbols)
            {
                if (!_priceHistory.ContainsKey(symbol) || _priceHistory[symbol].Count < 10)
                    continue;

                // Compute IV percentile (vs own trailing 252-day history)
                var ivPct = ComputeIVPercentile(ticker, Time.Date);
                if (ivPct < 0) continue;  // Insufficient history

                var todayIv = GetIVForDate(ticker, Time.Date);
                var atmIv = todayIv?.AtmIv ?? 0.2m;
                var skew = todayIv?.Skew ?? 0m;
                var rv = ComputeRealizedRV(symbol);

                // Score = IV percentile (primary) + skew bonus (secondary)
                // Higher percentile = IV elevated vs own history = fear signal
                var score = ivPct;

                // Small bonus for positive skew (put wing elevated = downside fear)
                if (skew > 0.02m) score += 0.02m;

                // Small penalty for negative skew (call wing elevated = complacency)
                if (skew < -0.02m) score -= 0.01m;

                var reason = $"IV_pct={ivPct:P2}, IV={atmIv:P2}, RV={rv:P2}, Skew={skew:P2}";
                scoredETFs.Add((ticker, symbol, ivPct, score, reason));
            }

            if (scoredETFs.Count == 0) return;

            // Sort by score (highest first)
            scoredETFs.Sort((a, b) => b.score.CompareTo(a.score));

            // Determine regime based on best ETF's IV percentile
            var bestPct = scoredETFs[0].ivPct;

            // V3 Strategy: Pure fear-based timing
            // - Fear (best pct > 60%): Buy ranked ETFs (leader 40%, runner 21%, laggard 9%)
            // - Normal (best pct 30-60%): Hold current positions, no rebalance
            // - Complacent (best pct < 30%): Sell all, go to 100% cash

            if (bestPct > 0.60m)
            {
                // FEAR REGIME: Buy the fear
                Log($"[REGIME] FEAR (best pct={bestPct:P2}) → BUY ranked ETFs");

                var allocations = new Dictionary<Symbol, decimal>();
                var allocs = new[] { LeaderAllocHigh, RunnerUpHigh, LaggardHigh };
                for (int i = 0; i < scoredETFs.Count && i < 3; i++)
                {
                    allocations[scoredETFs[i].symbol] = allocs[i];
                }

                // Execute buy trades
                foreach (var (symbol, targetPercent) in allocations)
                {
                    ExecuteTrade(symbol, targetPercent);
                }

                var top = string.Join(" | ", scoredETFs.Take(3).Select(a => $"{a.ticker}:{a.reason}"));
                Log($"[BUY_FEAR] {Time:yyyyMMdd} - {top}");
            }
            else if (bestPct > 0.30m)
            {
                // NORMAL REGIME: Hold current positions, no action
                Log($"[REGIME] NORMAL (best pct={bestPct:P2}) → HOLD current positions, no rebalance");
                // Skip rebalance - maintain existing positions
            }
            else
            {
                // COMPLACENT REGIME: Sell all, go to cash
                Log($"[REGIME] COMPLACENT (best pct={bestPct:P2}) → SELL ALL, go to 100% cash");

                foreach (var symbol in _etfSymbols.Values)
                {
                    ExecuteTrade(symbol, 0.0m);  // Target 0% = sell all
                }

                Log($"[SELL_ALL] {Time:yyyyMMdd} - All positions cleared, cash={Portfolio.CashBook["CNY"].Amount:N2}");
            }

            _lastRebalanceDate = Time.Date;
        }

        /// <summary>
        /// Compute IV percentile: where today's ATM IV ranks in trailing 252-day IV history.
        /// Returns -1 if insufficient history (< 30 points).
        /// </summary>
        private decimal ComputeIVPercentile(string ticker, DateTime today)
        {
            if (!_ivData.ContainsKey(ticker)) return -1m;
            var ivList = _ivData[ticker];
            if (ivList.Count < IvPercentileMinHistory) return -1m;

            // Get today's IV (fallback to most recent)
            var todayIv = GetIVForDate(ticker, today);
            if (todayIv == null || todayIv.AtmIv <= 0) return -1m;

            var todayAtm = todayIv.AtmIv;

            // Gather trailing IvPercentileWindow days of IV history (up to today)
            var windowStart = today.Date.AddDays(-IvPercentileWindow);
            var histIvs = ivList
                .Where(d => d.Time.Date >= windowStart && d.Time.Date <= today.Date)
                .Select(d => d.AtmIv)
                .Where(iv => iv > 0)
                .ToList();

            if (histIvs.Count < IvPercentileMinHistory) return -1m;

            // Sort and find percentile
            histIvs.Sort();
            var idx = 0;
            for (int i = 0; i < histIvs.Count; i++)
            {
                if (histIvs[i] >= todayAtm) { idx = i; break; }
                idx = histIvs.Count; // Today's IV is highest
            }

            // Percentile = rank / total (0 = lowest, 1 = highest)
            return (decimal)idx / histIvs.Count;
        }

        private AShareImpliedVolatilityData GetIVForDate(string ticker, DateTime date)
        {
            if (!_ivData.ContainsKey(ticker)) return null;
            var ivList = _ivData[ticker];
            if (ivList.Count == 0) return null;

            // Most recent IV on or before today
            return ivList.Where(d => d.Time.Date <= date.Date)
                         .OrderByDescending(d => d.Time.Date)
                         .FirstOrDefault();
        }

        private decimal ComputeRealizedRV(Symbol symbol)
        {
            if (_priceHistory.TryGetValue(symbol, out var prices) && prices.Count >= 10)
            {
                var pricesList = prices.ToList();
                pricesList.Reverse();

                var returns = new List<decimal>();
                for (int i = 1; i < pricesList.Count; i++)
                {
                    if (pricesList[i - 1] > 0)
                    {
                        returns.Add((pricesList[i] - pricesList[i - 1]) / pricesList[i - 1]);
                    }
                }

                if (returns.Count < 5) return 0.2m;

                var mean = returns.Average();
                var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
                return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
            }

            return 0.2m;
        }

        private void ExecuteTrade(Symbol symbol, decimal targetPercent)
        {
            var portfolioValue = Portfolio.TotalPortfolioValue;
            if (portfolioValue == 0) return;

            var targetValue = portfolioValue * targetPercent;
            var currentQuantity = Portfolio[symbol]?.Quantity ?? 0;
            var currentPrice = Securities[symbol].Price;
            if (currentPrice <= 0) return;

            var targetQuantity = (int)(targetValue / currentPrice / LotSize) * LotSize;
            var tradeQuantity = targetQuantity - currentQuantity;

            if (Math.Abs(tradeQuantity) >= LotSize)
            {
                try
                {
                    if (tradeQuantity > 0)
                    {
                        Buy(symbol, tradeQuantity);
                        Log($"[BUY] {symbol}: {tradeQuantity} shares @ {currentPrice:N3}, target={targetPercent:P2}");
                    }
                    else
                    {
                        Sell(symbol, -tradeQuantity);
                        Log($"[SELL] {symbol}: {-tradeQuantity} shares @ {currentPrice:N3}, target={targetPercent:P2}");
                    }
                }
                catch (Exception ex)
                {
                    Log($"[ERROR] Failed to trade {symbol}: {ex.Message}");
                }
            }
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[SUMMARY] ===== Multi-ETF Volatility Timing Strategy V3 =====");
            Log($"[SUMMARY] Pure fear-based timing (no normal-regime rebalancing)");
            Log($"[SUMMARY] Final Portfolio Value: {Portfolio.TotalPortfolioValue:N2}");
            var totalReturn = (Portfolio.TotalPortfolioValue - 1000000m) / 1000000m;
            Log($"[SUMMARY] Total Return: {totalReturn:P2}");

            foreach (var kvp in _etfSymbols)
            {
                var holdings = Securities[kvp.Value].Holdings;
                var value = holdings.HoldingsValue;
                var pct = Portfolio.TotalPortfolioValue > 0 ? value / Portfolio.TotalPortfolioValue : 0;
                Log($"[SUMMARY] {kvp.Key}: {holdings.Quantity:N0} shares ({pct:P2})");
            }

            var cashPct = Portfolio.TotalPortfolioValue > 0
                ? Portfolio.CashBook["CNY"].Amount / Portfolio.TotalPortfolioValue
                : 0m;
            Log($"[SUMMARY] Cash: {Portfolio.CashBook["CNY"].Amount:N2} ({cashPct:P2})");

            Log($"[SUMMARY] Total Trades: {Transactions.OrdersCount}");
            Log($"[SUMMARY] Total Fees: {Portfolio.TotalFees:N2}");
            Log($"[SUMMARY] ");
            Log($"[SUMMARY] Strategy Logic:");
            Log($"[SUMMARY] - Fear (IV_pct > 60%): BUY ranked ETFs (leader 40%, runner 21%, laggard 9%)");
            Log($"[SUMMARY] - Normal (IV_pct 30-60%): HOLD current positions, no action");
            Log($"[SUMMARY] - Complacent (IV_pct < 30%): SELL ALL, 100% cash");
            Log($"[SUMMARY] ");
            Log($"[SUMMARY] Key insight: Only trade when IV percentile is extreme (fear or complacent)");
            Log($"[SUMMARY] - Normal regime = noise, no rebalancing to reduce churn");
        }
    }
}
