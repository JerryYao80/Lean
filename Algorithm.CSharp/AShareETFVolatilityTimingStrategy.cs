/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0.
 *
 * AShare ETF Volatility Timing Strategy
 *
 * Uses PRE-COMPUTED option IV signals (from AShareImpliedVolatilityData CSV)
 * to guide ETF buy/sell timing. NO option trading required - only trades ETF.
 *
 * Strategy Logic (for stock/ETF investors who cannot trade options):
 * - IV-RV spread high (> 15%) → Market over-panicked → Buy ETF (buy the fear)
 * - IV-RV spread low (< 5%) → Market complacent → Reduce position (sell the greed)
 * - Put skew extreme (> 20%) → Panic overdone → Strong buy signal
 * - Surface skew negative → Put wing steep → Defensive, reduce
 *
 * Data source: Data/alternative/ashare-implied-volatility/sse/daily/510050.csv
 *   pre-computed by Scripts/export_ashare_implied_volatility_data.py
 */

using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.AShare;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Util;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareETFVolatilityTimingStrategy : QCAlgorithm
    {
        private Symbol _etfSymbol;
        private List<AShareImpliedVolatilityData> _ivData;

        // Strategy parameters
        private const decimal IvHighThreshold = 0.25m;    // ATM IV > 25% → high volatility regime
        private const decimal IvLowThreshold = 0.15m;     // ATM IV < 15% → low volatility regime
        private const decimal SkewExtremeThreshold = 0.05m; // Put skew > 5% → fear
        private const decimal SurfaceSkewThreshold = -0.02m; // Surface skew < -2% → steep put wing
        private const decimal MaxPositionPercent = 0.95m;
        private const decimal MinPositionPercent = 0.0m;
        private const int LotSize = 100;

        // Realized vol tracking
        private RollingWindow<decimal> _priceHistory = new RollingWindow<decimal>(20);

        public override void Initialize()
        {
            SetStartDate(2024, 2, 8);
            SetEndDate(2024, 6, 28);

            // Set account currency to CNY for A-share trading
            SetAccountCurrency("CNY");
            SetCash(1000000); // 1M CNY

            // Explicitly add CNY to cash book to avoid margin model errors
            Portfolio.CashBook.Add("CNY", 1000000, 1.0m);

            // Disable benchmark since we're trading Chinese ETFs
            SetBenchmark(x => 0);

            // Add ETF (510050 - SSE 50ETF) - this is what we actually trade
            var equity = AddEquity("510050", Resolution.Daily, market: Market.SSE);

            // Set custom fee model for A-share ETFs (correct commission calculation)
            // Note: For DAILY resolution, we do NOT use AShareETFFillModel/BuyingPowerModel
            // because they enforce intraday market hours checks that don't apply to daily bars.
            // Daily bars have timestamp at midnight (00:00), which falls outside 9:30-11:30/13:00-15:00.
            // Orders placed from daily OnData will be rejected by FillModel market hours validation.
            // For daily strategies, use default EquityFillModel which fills at bar close price.
            equity.FeeModel = new AShareETFFeeModel();
            _etfSymbol = equity.Symbol;

            // Load pre-computed IV data from CSV
            LoadIvDataCsv();

            SetWarmUp(20, Resolution.Daily);

            Log($"[INIT] ETF Volatility Timing Strategy");
            Log($"[INIT] Trades: 510050 ETF (stocks only - NO options)");
            Log($"[INIT] Signal: Pre-computed IV data from options market");
            Log($"[INIT] IV High threshold: {IvHighThreshold:P} (fear regime → buy)");
            Log($"[INIT] IV Low threshold: {IvLowThreshold:P} (complacent → reduce)");
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Track ETF price history for realized vol
            if (data.Bars.TryGetValue(_etfSymbol, out var etfBar))
            {
                _priceHistory.Add(etfBar.Close);
            }

            // Need price history
            if (_priceHistory.Count < 10)
            {
                return;
            }

            if (!data.Bars.TryGetValue(_etfSymbol, out var bar))
            {
                return;
            }

            // Look up IV data for this date from CSV (loaded in Initialize)
            var todayIv = GetIvDataForDate(Time);
            if (todayIv == null && Time.Day <= 5)
            {
                Log($"[{Time:yyyyMMdd}] No IV data for today");
            }

            var currentPrice = bar.Close;
            var portfolioValue = Portfolio.TotalPortfolioValue;
            var etfValue = Portfolio[_etfSymbol].HoldingsValue;
            var currentPosition = portfolioValue > 0 ? etfValue / portfolioValue : 0;

            // Compute realized vol from price history
            var rv = ComputeRealizedVol();

            // Get IV values (use fallback if no data for today)
            var atmIv = todayIv?.AtmIv ?? 0.2m;
            var skew = todayIv?.Skew ?? 0m;
            var surfaceSkew = todayIv?.IvSkewSurfaceMinus ?? 0m;
            var vix = todayIv?.Vix ?? 0m;

            var ivRvSpread = atmIv - rv;

            Log($"[{Time:yyyyMMdd}] ETF={currentPrice}, Pos={currentPosition:P2}, " +
                $"IV={atmIv:P2}, RV={rv:P2}, Spread={ivRvSpread:P2}, " +
                $"Skew={skew:P2}, VIX={vix}");

            // Generate timing signal
            var (signal, targetPercent) = GenerateTimingSignal(
                atmIv, rv, ivRvSpread, skew, surfaceSkew, currentPosition);

            if (signal != "HOLD")
            {
                Log($"[{Time:yyyyMMdd}] Signal: {signal}, Target: {targetPercent:P2}");
                ExecuteTrade(targetPercent, currentPrice);
            }
        }

        private AShareImpliedVolatilityData GetIvDataForDate(DateTime date)
        {
            // Load IV CSV if not already loaded
            if (_ivData == null)
            {
                LoadIvDataCsv();
            }

            return _ivData?.FirstOrDefault(d => d.Time.Date == date.Date);
        }

        private void LoadIvDataCsv()
        {
            try
            {
                var csvPath = Globals.DataFolder + "/alternative/ashare-implied-volatility/sse/daily/510050.csv";
                if (!File.Exists(csvPath))
                {
                    Log($"[INIT] IV CSV not found: {csvPath}");
                    return;
                }

                _ivData = new List<AShareImpliedVolatilityData>();
                var lines = File.ReadAllLines(csvPath).Skip(1); // Skip header

                foreach (var line in lines)
                {
                    var parts = line.Split(',');
                    if (parts.Length < 15) continue;

                    try
                    {
                        var tradeDate = DateTime.ParseExact(parts[0], "yyyyMMdd", CultureInfo.InvariantCulture);
                        var ivData = new AShareImpliedVolatilityData
                        {
                            Time = tradeDate,
                            AtmIv = decimal.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out var atm) ? atm : 0,
                            Skew = decimal.TryParse(parts[4], NumberStyles.Float, CultureInfo.InvariantCulture, out var skew) ? skew : 0,
                            IvSkewSurfaceMinus = decimal.TryParse(parts[5], NumberStyles.Float, CultureInfo.InvariantCulture, out var surf) ? surf : 0,
                            Vix = decimal.TryParse(parts[11], NumberStyles.Float, CultureInfo.InvariantCulture, out var vix) ? vix : 0
                        };
                        _ivData.Add(ivData);
                    }
                    catch { }
                }

                Log($"[INIT] Loaded {_ivData.Count} IV data points from CSV");
            }
            catch (Exception ex)
            {
                Log($"[INIT] Error loading IV CSV: {ex.Message}");
            }
        }

        private decimal ComputeRealizedVol()
        {
            if (_priceHistory.Count < 10) return 0.2m;

            var prices = _priceHistory.ToList();
            prices.Reverse(); // oldest first

            var returns = new List<decimal>();
            for (int i = 1; i < prices.Count; i++)
            {
                if (prices[i - 1] > 0)
                {
                    returns.Add((prices[i] - prices[i - 1]) / prices[i - 1]);
                }
            }

            if (returns.Count < 5) return 0.2m;

            var mean = returns.Average();
            var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
        }

        private (string signal, decimal targetPercent) GenerateTimingSignal(
            decimal atmIv, decimal rv, decimal ivRvSpread,
            decimal skew, decimal surfaceSkew, decimal currentPosition)
        {
            // STRONG BUY: High IV (fear) + extreme put skew + steep surface skew
            // Market over-panicked → excellent buy opportunity for stocks
            if (atmIv > IvHighThreshold && skew > SkewExtremeThreshold)
            {
                return ("STRONG BUY", MaxPositionPercent);
            }

            // BUY: IV elevated above RV (volatility premium = fear)
            if (ivRvSpread > 0.05m && atmIv > IvLowThreshold)
            {
                var target = Math.Min(currentPosition + 0.25m, MaxPositionPercent);
                return ("BUY", target);
            }

            // REDUCE: IV very low (complacency) or surface skew steeply negative
            if (atmIv < IvLowThreshold || surfaceSkew < SurfaceSkewThreshold)
            {
                var target = Math.Max(currentPosition - 0.30m, MinPositionPercent);
                return ("REDUCE", target);
            }

            // HOLD: normal conditions
            return ("HOLD", currentPosition);
        }

        private void ExecuteTrade(decimal targetPercent, decimal currentPrice)
        {
            var portfolioValue = Portfolio.TotalPortfolioValue;
            var targetValue = portfolioValue * targetPercent;
            var targetQuantity = (int)(targetValue / currentPrice / LotSize) * LotSize;
            var currentQuantity = Portfolio[_etfSymbol].Quantity;
            var tradeQuantity = targetQuantity - currentQuantity;

            if (Math.Abs(tradeQuantity) >= LotSize)
            {
                if (tradeQuantity > 0)
                {
                    Buy(_etfSymbol, tradeQuantity);
                    Log($"[{Time:yyyyMMdd}] >>> BUY {tradeQuantity} shares @ {currentPrice}");
                }
                else
                {
                    Sell(_etfSymbol, -tradeQuantity);
                    Log($"[{Time:yyyyMMdd}] >>> SELL {-tradeQuantity} shares @ {currentPrice}");
                }
            }
        }

        public override void OnEndOfAlgorithm()
        {
            var portfolioValue = Portfolio.TotalPortfolioValue;
            var returnPct = (portfolioValue - 1000000m) / 1000000m;

            Log($"[SUMMARY] ===== ETF Volatility Timing Strategy Results =====");
            Log($"[SUMMARY] Final Portfolio Value: {portfolioValue:N2}");
            Log($"[SUMMARY] Total Return: {returnPct:P2}");
            Log($"[SUMMARY] ETF Holdings: {Portfolio[_etfSymbol].Quantity} shares");
            Log($"[SUMMARY] Total Trades: {Transactions.OrdersCount}");
            Log($"[SUMMARY] ");
            Log($"[SUMMARY] ===== Strategy Value for Stock Investors =====");
            Log($"[SUMMARY] This strategy uses OPTION IV signals to guide STOCK timing");
            Log($"[SUMMARY] - You only trade ETFs/stocks (NO option trading needed)");
            Log($"[SUMMARY] - IV signals reveal market fear/greed from option market");
            Log($"[SUMMARY] - Buy stocks when IV high (market over-panicked)");
            Log($"[SUMMARY] - Reduce stocks when IV low (market complacent)");
            Log($"[SUMMARY] - Put skew reveals downside fear (contrarian buy signal)");
            Log($"[SUMMARY] ");
            Log($"[SUMMARY] Data source: Pre-computed IV CSV (no live option chain needed)");
        }
    }
}