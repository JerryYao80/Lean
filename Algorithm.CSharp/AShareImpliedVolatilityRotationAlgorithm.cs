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
    /// Implied Volatility Rotation strategy using real computed IV from options data.
    ///
    /// Three IV-driven signals replace realized-vol proxies:
    /// 1. Skew — 25-delta put-call risk reversal (puts expensive → fear)
    /// 2. IVTS — implied vol term structure ratio (backwardation → fear)
    /// 3. IV Level — ATM implied volatility absolute level (high IV → fear)
    ///
    /// When fear is high: rotate from equity ETFs to gold/money-market ETFs.
    /// When fear is low: rotate back to equity ETFs.
    /// Volatility-target scaling for position sizing.
    /// </summary>
    public class AShareImpliedVolatilityRotationAlgorithm : QCAlgorithm
    {
        private const int LotSize = 100;

        private decimal _initialCapital;
        private decimal _skewHighThreshold;
        private decimal _skewLowThreshold;
        private decimal _ivtsHighThreshold;
        private decimal _ivtsLowThreshold;
        private decimal _ivLevelHighThreshold;
        private decimal _ivLevelLowThreshold;
        private decimal _targetVol;
        private int _rebalanceFrequencyDays;
        private decimal _feeRate;
        private decimal _equityWeightFear;
        private decimal _equityWeightGreed;
        private decimal _safeWeightFear;
        private decimal _safeWeightGreed;

        private string _signalFilePath;
        private string _portfolioSnapshotPath;
        private string _dailySummaryPath;
        private string _summaryFilePath;
        private string _ivDataPath;

        private HashSet<string> _equityETFs;
        private HashSet<string> _safeETFs;
        private List<string> _universe;

        private int _daysSinceRebalance;
        private List<Symbol> _symbols;
        private Dictionary<Symbol, string> _symbolToTsCode;
        private Dictionary<Symbol, List<decimal>> _priceHistory;
        private Dictionary<Symbol, Symbol> _ivToUnderlying;
        private Dictionary<Symbol, AShareImpliedVolatilityData> _latestIvData;

        private AShareImpliedVolatilitySignalSettings _signalSettings;

        private decimal _currentSkewSignal;
        private decimal _currentIVTSSignal;
        private decimal _currentIVLevelSignal;
        private string _currentRegime;

        public override void Initialize()
        {
            _initialCapital = GetParam("initial-capital", 1000000m);
            _targetVol = GetParam("target-vol", 0.15m);
            _rebalanceFrequencyDays = (int)GetParam("rebalance-frequency-days", 5m);
            _feeRate = GetParam("fee-rate", 0.0005m);
            _equityWeightFear = GetParam("equity-weight-fear", 0.10m);
            _equityWeightGreed = GetParam("equity-weight-greed", 0.60m);
            _safeWeightFear = GetParam("safe-weight-fear", 0.70m);
            _safeWeightGreed = GetParam("safe-weight-greed", 0.20m);

            _signalSettings = new AShareImpliedVolatilitySignalSettings
            {
                SkewHighThreshold = GetParam("skew-high-threshold", 0.05m),
                SkewLowThreshold = GetParam("skew-low-threshold", -0.02m),
                IvtsHighThreshold = GetParam("ivts-high-threshold", 1.2m),
                IvtsLowThreshold = GetParam("ivts-low-threshold", 0.8m),
                IvLevelHighThreshold = GetParam("iv-level-high-threshold", 0.30m),
                IvLevelLowThreshold = GetParam("iv-level-low-threshold", 0.15m),
                SkewWeight = GetParam("skew-weight", 0.35m),
                IvtsWeight = GetParam("ivts-weight", 0.35m),
                IvLevelWeight = GetParam("iv-level-weight", 0.30m),
                MinOptionCount = (int)GetParam("min-option-count", 4m),
            };

            _signalFilePath = GetParam("signal-file", "");
            _portfolioSnapshotPath = GetParam("portfolio-snapshot-file", "");
            _dailySummaryPath = GetParam("daily-summary-file", "");
            _summaryFilePath = GetParam("summary-file", "");
            _ivDataPath = GetParam("iv-data-path", "");

            SetStartDate(ParseDate(GetParam("start-date", "2020-01-01")));
            SetEndDate(ParseDate(GetParam("end-date", "2025-12-31")));
            SetCash(_initialCapital);

            _equityETFs = new HashSet<string> { "510050.SH", "510300.SH", "510500.SH" };
            _safeETFs = new HashSet<string> { "518880.SH", "511010.SH", "511260.SH" };
            _universe = ParseUniverse(GetParam("universe",
                "510050.SH,510300.SH,510500.SH,518880.SH,511010.SH,511260.SH"));

            _symbols = new List<Symbol>();
            _symbolToTsCode = new Dictionary<Symbol, string>();
            _priceHistory = new Dictionary<Symbol, List<decimal>>();
            _ivToUnderlying = new Dictionary<Symbol, Symbol>();
            _latestIvData = new Dictionary<Symbol, AShareImpliedVolatilityData>();

            if (!string.IsNullOrWhiteSpace(_ivDataPath))
                AShareImpliedVolatilityData.SetBaseDirectory(_ivDataPath);

            // ETFs with options data — add IV custom data subscription
            var ivTsCodes = new HashSet<string> { "510050.SH", "510300.SH", "510500.SH" };

            foreach (var tsCode in _universe)
            {
                var (ticker, market) = ParseTsCode(tsCode);
                var equity = AddEquity(ticker, Resolution.Daily, market);
                equity.FeeModel = new ConstantFeeModel(5m);
                equity.FillModel = new AShareStockFillModel();
                _symbols.Add(equity.Symbol);
                _symbolToTsCode[equity.Symbol] = tsCode;
                _priceHistory[equity.Symbol] = new List<decimal>();

                if (ivTsCodes.Contains(tsCode))
                {
                    var ivSecurity = AddData<AShareImpliedVolatilityData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                    _ivToUnderlying[ivSecurity.Symbol] = equity.Symbol;
                }
            }

            _daysSinceRebalance = 0;
            _currentSkewSignal = 0m;
            _currentIVTSSignal = 1.0m;
            _currentIVLevelSignal = 0.20m;
            _currentRegime = "neutral";
        }

        public override void OnData(Slice data)
        {
            // Consume IV custom data
            foreach (var pair in data.Get<AShareImpliedVolatilityData>())
            {
                if (pair.Value == null) continue;
                if (_ivToUnderlying.TryGetValue(pair.Key, out var underlying))
                    _latestIvData[underlying] = pair.Value;
            }

            // Use History<TradeBar>() for prices (TushareHistoryProvider with normal scale)
            var lookback = 70;
            foreach (var symbol in _symbols)
            {
                var bars = History<TradeBar>(symbol, lookback, Resolution.Daily);
                if (bars.Any())
                    _priceHistory[symbol] = bars.Select(b => b.Close).ToList();
            }

            var symbolsWithData = _symbols.Where(s => _priceHistory[s].Count > 0).ToList();
            if (symbolsWithData.Count == 0) return;
            var minLen = symbolsWithData.Min(s => _priceHistory[s].Count);
            if (minLen < 10) return;

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
            var ivByCode = _latestIvData
                .Where(kvp => _symbolToTsCode.ContainsKey(kvp.Key))
                .ToDictionary(kvp => _symbolToTsCode[kvp.Key], kvp => kvp.Value);
            var (skew, ivts, ivLevel) = AShareImpliedVolatilitySignalModel.ComputeSignals(
                ivByCode, _signalSettings);

            _currentSkewSignal = skew;
            _currentIVTSSignal = ivts;
            _currentIVLevelSignal = ivLevel;
            _currentRegime = AShareImpliedVolatilitySignalModel.DetermineRegime(
                skew, ivts, ivLevel, _signalSettings);

            SetRuntimeStatistic("Regime", _currentRegime);
            SetRuntimeStatistic("SkewSignal", _currentSkewSignal.ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("IVTSSignal", _currentIVTSSignal.ToString("F4", CultureInfo.InvariantCulture));
            SetRuntimeStatistic("IVLevelSignal", _currentIVLevelSignal.ToString("F4", CultureInfo.InvariantCulture));
        }

        private void Rebalance()
        {
            var portfolioVol = ComputePortfolioVolatility();
            var volScale = portfolioVol > 0 ? _targetVol / portfolioVol : 1.0m;
            volScale = Math.Max(0.3m, Math.Min(2.0m, volScale));

            var (equityWeight, safeWeight) = AShareImpliedVolatilitySignalModel.ComputeAllocation(
                _currentRegime, _equityWeightFear, _equityWeightGreed,
                _safeWeightFear, _safeWeightGreed);

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
                var price = GetRecentPrice(symbol);
                if (price <= 0) continue;

                var targetWeight = targets.GetValueOrDefault(symbol, 0m);
                var targetQuantity = GetLotQuantity(Portfolio.TotalPortfolioValue * targetWeight, price);
                var currentQuantity = Portfolio[symbol].Quantity;
                var delta = targetQuantity - currentQuantity;

                if (delta != 0)
                    MarketOrder(symbol, delta);
            }
        }

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
                if (prices.Count < 20) continue;
                var recent = prices.Skip(prices.Count - 60).ToList();
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
            return (int)Math.Floor(value / price / LotSize) * LotSize;
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
            if (DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var dt))
                return dt;
            if (DateTime.TryParseExact(dateStr, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out dt))
                return dt;
            return new DateTime(2020, 1, 1);
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
                    algorithm = "AShareImpliedVolatilityRotationAlgorithm",
                    date = Time.ToString("yyyy-MM-dd"),
                    skew_signal = Math.Round(_currentSkewSignal, 6),
                    ivts_signal = Math.Round(_currentIVTSSignal, 4),
                    iv_level_signal = Math.Round(_currentIVLevelSignal, 6),
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
                    algorithm = "AShareImpliedVolatilityRotationAlgorithm",
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
                    writer.WriteLine("date,portfolio_value,skew_signal,ivts_signal,iv_level_signal,regime");
                writer.WriteLine($"{Time:yyyy-MM-dd},{Portfolio.TotalPortfolioValue:F2},{_currentSkewSignal:F6},{_currentIVTSSignal:F4},{_currentIVLevelSignal:F6},{_currentRegime}");
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
                    algorithm = "AShareImpliedVolatilityRotationAlgorithm",
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
