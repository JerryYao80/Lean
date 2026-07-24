using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Volatility Skew Rotation strategy based on:
    /// - Doran, Peterson &amp; Tarrant (2007) "Is there information in the volatility skew?"
    /// - Wang et al. (2024) "VIX constant maturity futures trading strategy" (PLoS ONE)
    ///
    /// Uses realized volatility as a proxy for implied volatility when option data
    /// is not directly available in LEAN. The strategy computes:
    /// 1. Realized volatility ratio (short-term vs long-term) → contango/backwardation signal
    /// 2. Downside deviation ratio → proxy for volatility skew (fear indicator)
    /// 3. When fear is high (elevated downside vol, inverted term structure):
    ///    rotate from equity ETFs to gold/money-market ETFs
    /// 4. When fear is low: rotate back to equity ETFs
    /// 5. Volatility scaling for position sizing
    ///
    /// Uses History&lt;TradeBar&gt;() via TushareHistoryProvider for prices (normal scale),
    /// since the LEAN CSV data uses 10000x price scaling for A-shares.
    /// </summary>
    public class AShareVolatilitySkewRotationAlgorithm : QCAlgorithm
    {
        private const int LotSize = 100;

        private decimal _initialCapital;
        private int _shortVolWindow;
        private int _longVolWindow;
        private decimal _skewHighThreshold;
        private decimal _skewLowThreshold;
        private decimal _targetVol;
        private decimal _equityWeightHighSkew;
        private decimal _equityWeightLowSkew;
        private decimal _safeWeightHighSkew;
        private decimal _safeWeightLowSkew;
        private int _rebalanceFrequencyDays;
        private decimal _feeRate;
        private decimal _ivtsHighThreshold;
        private decimal _ivtsLowThreshold;

        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private string _dailySummaryPath;
        private string _summaryFilePath;

        private HashSet<string> _equityETFs;
        private HashSet<string> _safeETFs;
        private List<string> _universe;

        private int _daysSinceRebalance;
        private List<Symbol> _symbols;
        private Dictionary<Symbol, string> _symbolToTsCode;
        private Dictionary<Symbol, List<decimal>> _priceHistory;

        private decimal _currentSkewSignal;
        private decimal _currentIVTSSignal;
        private string _currentRegime;

        public override void Initialize()
        {
            _initialCapital = GetParam("initial-capital", 1000000m);
            _shortVolWindow = (int)GetParam("short-vol-window", 5m);
            _longVolWindow = (int)GetParam("long-vol-window", 60m);
            _skewHighThreshold = GetParam("skew-high-threshold", 1.3m);
            _skewLowThreshold = GetParam("skew-low-threshold", 0.9m);
            _targetVol = GetParam("target-vol", 0.15m);
            _equityWeightHighSkew = GetParam("equity-weight-high-skew", 0.1m);
            _equityWeightLowSkew = GetParam("equity-weight-low-skew", 0.6m);
            _safeWeightHighSkew = GetParam("safe-weight-high-skew", 0.7m);
            _safeWeightLowSkew = GetParam("safe-weight-low-skew", 0.2m);
            _rebalanceFrequencyDays = (int)GetParam("rebalance-frequency-days", 5m);
            _feeRate = GetParam("fee-rate", 0.0005m);
            _ivtsHighThreshold = GetParam("ivts-high-threshold", 1.2m);
            _ivtsLowThreshold = GetParam("ivts-low-threshold", 0.8m);

            _signalFilePath = GetParam("signal-file", "");
            _portfolioSnapshotPath = GetParam("portfolio-snapshot-file", "");
            _dailySummaryPath = GetParam("daily-summary-file", "");
            _summaryFilePath = GetParam("summary-file", "");

            SetStartDate(ParseDate(GetParam("start-date", "2020-01-01")));
            SetEndDate(ParseDate(GetParam("end-date", "2025-12-31")));
            SetCash(_initialCapital);

            _equityETFs = new HashSet<string> { "510050.SH", "510300.SH", "510500.SH", "159919.SZ" };
            _safeETFs = new HashSet<string> { "518880.SH", "511010.SH", "511260.SH" };
            _universe = ParseUniverse(GetParam("universe",
                "518880.SH,510050.SH,510300.SH,510500.SH,510880.SH,511010.SH,511260.SH,159919.SZ"));

            _symbols = new List<Symbol>();
            _symbolToTsCode = new Dictionary<Symbol, string>();
            _priceHistory = new Dictionary<Symbol, List<decimal>>();

            foreach (var tsCode in _universe)
            {
                var (ticker, market) = ParseTsCode(tsCode);
                var equity = AddEquity(ticker, Resolution.Daily, market);
                equity.FeeModel = new ConstantFeeModel(5m);
                equity.FillModel = new AShareStockFillModel();
                _symbols.Add(equity.Symbol);
                _symbolToTsCode[equity.Symbol] = tsCode;
                _priceHistory[equity.Symbol] = new List<decimal>();
            }

            _daysSinceRebalance = 0;
            _currentSkewSignal = 1.0m;
            _currentIVTSSignal = 1.0m;
            _currentRegime = "neutral";
        }

        public override void OnData(Slice data)
        {
            // Use History<TradeBar>() for prices (from TushareHistoryProvider with normal scale),
            // since LEAN CSV data uses 10000x price scaling for A-shares.
            var lookback = _longVolWindow + 10;
            foreach (var symbol in _symbols)
            {
                var bars = History<TradeBar>(symbol, lookback, Resolution.Daily);
                if (bars.Any())
                {
                    _priceHistory[symbol] = bars.Select(b => b.Close).ToList();
                }
            }

            // Warmup check: need enough price history for all symbols that have data
            var symbolsWithData = _symbols.Where(s => _priceHistory[s].Count > 0).ToList();
            if (symbolsWithData.Count == 0) return;

            var minLen = symbolsWithData.Min(s => _priceHistory[s].Count);
            if (minLen < _longVolWindow + 5) return;

            _daysSinceRebalance++;
            if (_daysSinceRebalance < _rebalanceFrequencyDays) return;
            _daysSinceRebalance = 0;

            ComputeSignals();
            Rebalance();
            WriteSignalFile();
            WritePortfolioSnapshot();
            WriteDailySummary();
        }

        private void ComputeSignals()
        {
            // 1. Downside deviation ratio (skew proxy)
            var downsideRatios = new List<decimal>();
            foreach (var symbol in _symbols)
            {
                var tsCode = _symbolToTsCode[symbol];
                if (!_equityETFs.Contains(tsCode)) continue;
                var prices = _priceHistory[symbol];
                if (prices.Count < _longVolWindow) continue;
                var recentPrices = prices.Skip(prices.Count - _longVolWindow).ToList();
                var returns = ComputeDailyReturns(recentPrices);
                if (returns.Count < 10) continue;

                var downsideReturns = returns.Where(r => r < 0).ToList();
                if (returns.Count > 0 && downsideReturns.Count > 0)
                {
                    var downsideVol = (decimal)Math.Sqrt((double)downsideReturns.Select(r => r * r).Average()) * (decimal)Math.Sqrt(252);
                    var overallVol = (decimal)Math.Sqrt((double)returns.Select(r => r * r).Average()) * (decimal)Math.Sqrt(252);
                    if (overallVol > 0)
                        downsideRatios.Add(downsideVol / overallVol);
                }
            }

            _currentSkewSignal = downsideRatios.Count > 0 ? downsideRatios.Average() : 1.0m;

            // 2. IV term structure proxy (short-term vs long-term realized vol ratio)
            var ivtsRatios = new List<decimal>();
            foreach (var symbol in _symbols)
            {
                var tsCode = _symbolToTsCode[symbol];
                if (!_equityETFs.Contains(tsCode)) continue;
                var prices = _priceHistory[symbol];
                if (prices.Count < _longVolWindow + _shortVolWindow) continue;

                var recentShort = prices.Skip(prices.Count - _shortVolWindow).ToList();
                var recentLong = prices.Skip(prices.Count - _longVolWindow).ToList();

                var shortReturns = ComputeDailyReturns(recentShort);
                var longReturns = ComputeDailyReturns(recentLong);

                if (shortReturns.Count >= 3 && longReturns.Count >= 10)
                {
                    var shortVol = ComputeAnnualizedVol(shortReturns);
                    var longVol = ComputeAnnualizedVol(longReturns);
                    if (longVol > 0)
                        ivtsRatios.Add(shortVol / longVol);
                }
            }

            _currentIVTSSignal = ivtsRatios.Count > 0 ? ivtsRatios.Average() : 1.0m;

            // 3. Determine regime
            var compositeFear = (_currentSkewSignal / _skewLowThreshold + _currentIVTSSignal / _ivtsLowThreshold) / 2m;
            if (compositeFear > 1.3m)
                _currentRegime = "fear";
            else if (compositeFear < 0.9m)
                _currentRegime = "greed";
            else
                _currentRegime = "neutral";
        }

        private void Rebalance()
        {
            var portfolioVol = ComputePortfolioVolatility();
            var volScale = portfolioVol > 0 ? _targetVol / portfolioVol : 1.0m;
            volScale = Math.Max(0.3m, Math.Min(2.0m, volScale));

            decimal equityWeight, safeWeight;
            var skewFactor = _currentSkewSignal / ((_skewHighThreshold + _skewLowThreshold) / 2m);
            var ivtsFactor = _currentIVTSSignal / ((_ivtsHighThreshold + _ivtsLowThreshold) / 2m);
            var fearScore = (skewFactor + ivtsFactor) / 2m;

            var fearNorm = Math.Max(0m, Math.Min(1m, (fearScore - 0.8m) / 0.6m));
            equityWeight = _equityWeightLowSkew + (_equityWeightHighSkew - _equityWeightLowSkew) * fearNorm;
            safeWeight = _safeWeightLowSkew + (_safeWeightHighSkew - _safeWeightLowSkew) * fearNorm;

            equityWeight *= volScale;
            safeWeight *= volScale;

            var equitySymbols = _symbols.Where(s => _equityETFs.Contains(_symbolToTsCode[s])).ToList();
            var safeSymbols = _symbols.Where(s => _safeETFs.Contains(_symbolToTsCode[s])).ToList();

            var targets = new Dictionary<Symbol, decimal>();
            if (equitySymbols.Count > 0)
            {
                var perETF = equityWeight / equitySymbols.Count;
                foreach (var sym in equitySymbols) targets[sym] = perETF;
            }
            if (safeSymbols.Count > 0)
            {
                var perETF = safeWeight / safeSymbols.Count;
                foreach (var sym in safeSymbols) targets[sym] = perETF;
            }

            foreach (var symbol in _symbols)
            {
                var tsCode = _symbolToTsCode[symbol];
                var price = GetRecentPrice(symbol);
                if (price <= 0) continue;

                var targetWeight = targets.GetValueOrDefault(symbol, 0m);
                var targetQuantity = GetLotQuantity(Portfolio.TotalPortfolioValue * targetWeight, price);

                var currentQuantity = Portfolio[symbol].Quantity;
                var delta = targetQuantity - currentQuantity;

                if (delta != 0)
                {
                    MarketOrder(symbol, delta);
                }
            }
        }

        /// <summary>
        /// Gets the most recent price from price history (TushareHistoryProvider-sourced).
        /// Falls back to Security.GetLastData() if history is empty.
        /// </summary>
        private decimal GetRecentPrice(Symbol symbol)
        {
            var prices = _priceHistory[symbol];
            if (prices.Count > 0)
                return prices[prices.Count - 1];

            var security = Securities[symbol];
            return security.GetLastData()?.Price ?? 0m;
        }

        private decimal ComputePortfolioVolatility()
        {
            var allReturns = new List<decimal>();
            foreach (var symbol in _symbols)
            {
                var prices = _priceHistory[symbol];
                if (prices.Count < _longVolWindow) continue;
                var recent = prices.Skip(prices.Count - _longVolWindow).ToList();
                allReturns.AddRange(ComputeDailyReturns(recent));
            }
            if (allReturns.Count < 10) return 0.2m;
            return ComputeAnnualizedVol(allReturns);
        }

        #region Utility Methods

        public static List<decimal> ComputeDailyReturns(List<decimal> prices)
        {
            var returns = new List<decimal>();
            for (int i = 1; i < prices.Count; i++)
            {
                if (prices[i - 1] > 0)
                    returns.Add((prices[i] - prices[i - 1]) / prices[i - 1]);
            }
            return returns;
        }

        public static decimal ComputeAnnualizedVol(List<decimal> returns)
        {
            if (returns.Count < 2) return 0.2m;
            var mean = returns.Average();
            var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
        }

        public static decimal ComputeMaxDrawdown(List<decimal> prices)
        {
            if (prices.Count < 2) return 0m;
            decimal peak = prices[0];
            decimal maxDD = 0m;
            foreach (var price in prices)
            {
                if (price > peak) peak = price;
                var dd = (peak - price) / peak;
                if (dd > maxDD) maxDD = dd;
            }
            return maxDD;
        }

        public static decimal ComputeSharpe(List<decimal> returns, decimal riskFreeRate = 0.02m)
        {
            if (returns.Count < 2) return 0m;
            var annualReturn = returns.Average() * 252m;
            var vol = ComputeAnnualizedVol(returns);
            return vol > 0 ? (annualReturn - riskFreeRate) / vol : 0m;
        }

        public static List<string> ParseUniverse(string universeStr)
        {
            return universeStr.Split(',').Select(s => s.Trim()).Where(s => !string.IsNullOrEmpty(s)).ToList();
        }

        public static (string ticker, string market) ParseTsCode(string tsCode)
        {
            var parts = tsCode.Split('.');
            var ticker = parts[0];
            var market = parts.Length > 1 && parts[1] == "SZ" ? Market.SZSE : Market.SSE;
            return (ticker, market);
        }

        public static int GetLotQuantity(decimal value, decimal price)
        {
            if (price <= 0) return 0;
            var shares = (int)Math.Floor(value / price / LotSize) * LotSize;
            return shares;
        }

        private decimal GetParam(string key, decimal defaultValue)
        {
            var val = GetParameter(key);
            if (string.IsNullOrEmpty(val)) return defaultValue;
            return decimal.TryParse(val, NumberStyles.Any, CultureInfo.InvariantCulture, out var result) ? result : defaultValue;
        }

        private string GetParam(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }

        private DateTime ParseDate(string dateStr)
        {
            return DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var dt)
                ? dt : new DateTime(2020, 1, 1);
        }

        #endregion

        #region File Output

        private void WriteSignalFile()
        {
            if (string.IsNullOrEmpty(_signalFilePath)) return;
            try
            {
                var dir = Path.GetDirectoryName(_signalFilePath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                var signal = new
                {
                    algorithm = "AShareVolatilitySkewRotationAlgorithm",
                    date = Time.ToString("yyyy-MM-dd"),
                    skew_signal = Math.Round(_currentSkewSignal, 4),
                    ivts_signal = Math.Round(_currentIVTSSignal, 4),
                    regime = _currentRegime,
                    portfolio_value = Math.Round(Portfolio.TotalPortfolioValue, 2),
                };
                File.WriteAllText(_signalFilePath, Newtonsoft.Json.JsonConvert.SerializeObject(signal, Newtonsoft.Json.Formatting.Indented));
            }
            catch { }
        }

        private void WritePortfolioSnapshot()
        {
            if (string.IsNullOrEmpty(_portfolioSnapshotPath)) return;
            try
            {
                var dir = Path.GetDirectoryName(_portfolioSnapshotPath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                var holdings = new List<object>();
                foreach (var kvp in Portfolio)
                {
                    if (kvp.Value.Quantity != 0)
                    {
                        holdings.Add(new
                        {
                            symbol = kvp.Key.Value,
                            quantity = kvp.Value.Quantity,
                            average_price = kvp.Value.AveragePrice,
                            market_value = kvp.Value.HoldingsValue,
                            unrealized_pnl = kvp.Value.UnrealizedProfit,
                        });
                    }
                }
                var snapshot = new
                {
                    algorithm = "AShareVolatilitySkewRotationAlgorithm",
                    timestamp = Time.ToString("o"),
                    total_value = Portfolio.TotalPortfolioValue,
                    cash = Portfolio.Cash,
                    holdings,
                };
                File.WriteAllText(_portfolioSnapshotPath, Newtonsoft.Json.JsonConvert.SerializeObject(snapshot, Newtonsoft.Json.Formatting.Indented));
            }
            catch { }
        }

        private void WriteDailySummary()
        {
            if (string.IsNullOrEmpty(_dailySummaryPath)) return;
            try
            {
                var dir = Path.GetDirectoryName(_dailySummaryPath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                var header = !File.Exists(_dailySummaryPath) || new FileInfo(_dailySummaryPath).Length == 0;
                using var writer = new StreamWriter(_dailySummaryPath, append: true);
                if (header)
                    writer.WriteLine("date,portfolio_value,skew_signal,ivts_signal,regime");
                writer.WriteLine($"{Time:yyyy-MM-dd},{Portfolio.TotalPortfolioValue:F2},{_currentSkewSignal:F4},{_currentIVTSSignal:F4},{_currentRegime}");
            }
            catch { }
        }

        #endregion

        public override void OnEndOfAlgorithm()
        {
            if (string.IsNullOrEmpty(_summaryFilePath)) return;
            try
            {
                var dir = Path.GetDirectoryName(_summaryFilePath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                var summary = new
                {
                    algorithm = "AShareVolatilitySkewRotationAlgorithm",
                    total_return = Math.Round((Portfolio.TotalPortfolioValue - _initialCapital) / _initialCapital, 6),
                    final_value = Math.Round(Portfolio.TotalPortfolioValue, 2),
                    initial_capital = _initialCapital,
                    end_date = Time.ToString("yyyy-MM-dd"),
                };
                File.WriteAllText(_summaryFilePath, Newtonsoft.Json.JsonConvert.SerializeObject(summary, Newtonsoft.Json.Formatting.Indented));
            }
            catch { }
        }
    }
}
