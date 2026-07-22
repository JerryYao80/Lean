// file: Algorithm.CSharp/Models/Alpha/VarFactorZooAlphaModel.cs
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Risk;
using QuantConnect.Factors.Volatility;
using QuantConnect.Indicators;
using QuantConnect.Risk.VaR;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// 复合 Alpha 模型：VaR regime + 因子动物园 IV-RV Z-score + 价格动量趋势过滤。
    ///
    /// 三信号融合（仅在趋势与风控一致时下单，降低换手）：
    /// 1. 价格动量趋势（方向主信号，来自因子动物园 MomentumFactor）：
    ///    - price > MA20 → 上升趋势 → 倾向做多
    ///    - price < MA20 → 下降趋势 → 倾向做空/空仓
    /// 2. IV-RV Z-score（时机增强，因子动物园 OptionVolArbFactorZoo）：
    ///    - z > threshold → IV 恐慌溢价 → 波动率将回落 → 价格回升 → 增强做多
    ///    - z < -threshold → IV 过低 → 波动率将上升 → 价格下跌 → 增强做空
    /// 3. VaR regime（风控维度）：
    ///    - regime >= 0.80（stressed） → 强制 Down（减仓规避风险）
    ///    - regime calm → 正常下单
    /// </summary>
    public class VarFactorZooAlphaModel : IAlphaModel
    {
        private readonly VaRFactor _varFactor;
        private readonly IVPercentileFactor _ivFactor;
        private readonly HVFactor _hvFactor;
        private readonly int _lookbackDays;
        private readonly int _rvLookbackDays;
        private readonly int _ivLookbackDays;
        private readonly decimal _regimeHighThreshold;
        private readonly decimal _ivRvZScoreThreshold;
        private readonly TimeSpan _insightPeriod;

        private readonly Dictionary<Symbol, RollingWindow<decimal>> _ivHistory = new();
        private readonly Dictionary<Symbol, RollingWindow<decimal>> _rvHistory = new();
        // Track last emitted direction per symbol to suppress duplicate insights (cut turnover)
        private readonly Dictionary<Symbol, InsightDirection> _lastDirection = new();

        public string Name => "VarFactorZooAlphaModel";

        public VarFactorZooAlphaModel(
            VaRFactor varFactor,
            int lookbackDays = 252,
            int rvLookbackDays = 20,
            int ivLookbackDays = 60,
            decimal regimeHighThreshold = 0.80m,
            decimal ivRvZScoreThreshold = 1.5m,
            int insightPeriodDays = 10)
        {
            _varFactor = varFactor ?? throw new ArgumentNullException(nameof(varFactor));
            _lookbackDays = lookbackDays;
            _rvLookbackDays = rvLookbackDays;
            _ivLookbackDays = ivLookbackDays;
            _regimeHighThreshold = regimeHighThreshold;
            _ivRvZScoreThreshold = ivRvZScoreThreshold;
            _insightPeriod = TimeSpan.FromDays(insightPeriodDays);

            FactorRegistry.Initialize();
            _ivFactor = FactorRegistry.Get("iv_pct_252d") as IVPercentileFactor ?? new IVPercentileFactor(252);
            _hvFactor = FactorRegistry.Get("hv_20d") as HVFactor ?? new HVFactor(20);
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();
            if (algorithm.IsWarmingUp) return insights;

            foreach (var kvp in data.Bars)
            {
                var symbol = kvp.Key;
                var bar = kvp.Value;
                if (bar.Close <= 0) continue;
                if (!algorithm.Securities.ContainsKey(symbol)) continue;

                // === Signal 1: VaR regime (risk dimension) ===
                var history = algorithm.History<TradeBar>(symbol, _lookbackDays + 10, Resolution.Daily);
                var varResult = _varFactor.Compute(symbol, algorithm.Time, history);
                decimal regimePercentile = varResult.Quality == FactorDataQuality.Valid
                    ? varResult.Value : 0m;
                bool regimeStressed = regimePercentile >= _regimeHighThreshold;

                // === Signal 2: IV-RV Z-score (directional alpha from factor zoo) ===
                var rvHistory = algorithm.History<TradeBar>(symbol, _rvLookbackDays, Resolution.Daily);
                var ivR = _ivFactor.Compute(symbol, algorithm.Time, null);
                var rvR = _hvFactor.Compute(symbol, algorithm.Time, rvHistory);

                double? ivRvZ = null;
                InsightDirection? ivRvDirection = null;
                double ivRvMagnitude = 0;

                if (ivR.Quality == FactorDataQuality.Valid && rvR.Quality == FactorDataQuality.Valid)
                {
                    if (!_ivHistory.ContainsKey(symbol))
                    {
                        _ivHistory[symbol] = new RollingWindow<decimal>(_ivLookbackDays);
                        _rvHistory[symbol] = new RollingWindow<decimal>(_ivLookbackDays);

                        // Seed rolling windows with historical data so z-score can fire immediately
                        var seedBars = algorithm.History<TradeBar>(symbol, _ivLookbackDays, Resolution.Daily).ToList();
                        foreach (var seedBar in seedBars)
                        {
                            var seedIv = _ivFactor.Compute(symbol, seedBar.EndTime, null);
                            var seedRv = _hvFactor.Compute(symbol, seedBar.EndTime,
                                algorithm.History<TradeBar>(symbol, _rvLookbackDays, Resolution.Daily));
                            if (seedIv.Quality == FactorDataQuality.Valid && seedRv.Quality == FactorDataQuality.Valid)
                            {
                                _ivHistory[symbol].Add(seedIv.RawValue);
                                _rvHistory[symbol].Add(seedRv.RawValue);
                            }
                        }
                    }
                    _ivHistory[symbol].Add(ivR.RawValue);
                    _rvHistory[symbol].Add(rvR.RawValue);

                    if (_ivHistory[symbol].Count >= 20 && _rvHistory[symbol].Count >= 20)
                    {
                        var minCount = Math.Min(_ivHistory[symbol].Count, _rvHistory[symbol].Count);
                        var ivArr = _ivHistory[symbol].Take(minCount).ToList();
                        var rvArr = _rvHistory[symbol].Take(minCount).ToList();
                        var spreads = ivArr.Select((iv_, i) => iv_ - rvArr[i]).ToList();
                        var mean = spreads.Average();
                        var std = (decimal)Math.Sqrt((double)spreads.Select(x => (x - mean) * (x - mean)).Sum() / Math.Max(1, spreads.Count - 1));
                        if (std > 0.001m)
                        {
                            var z = (ivR.RawValue - rvR.RawValue - mean) / std;
                            ivRvZ = (double)z;
                            // IV-RV spread direction (mean-reversion of volatility premium):
                            //   z > threshold → IV over RV (panic premium) → expect vol to fall → price recovers → BUY
                            //   z < -threshold → IV under RV (complacency) → expect vol to rise → price drops → SELL
                            if (z > _ivRvZScoreThreshold)
                            {
                                ivRvDirection = InsightDirection.Up;
                                ivRvMagnitude = Math.Min(1.0, (double)Math.Abs(z) / 2.0);
                            }
                            else if (z < -_ivRvZScoreThreshold)
                            {
                                ivRvDirection = InsightDirection.Down;
                                ivRvMagnitude = Math.Min(1.0, (double)Math.Abs(z) / 2.0);
                            }
                        }
                    }
                }

                // === Signal 3: Price trend filter (MA20) — trend-following ===
                // Use the same daily history we already fetched for VaR
                var closes = history.OfType<TradeBar>().Select(b => b.Close).TakeLast(20).ToList();
                InsightDirection? trendDirection = null;
                double trendMagnitude = 0;
                if (closes.Count >= 20)
                {
                    var ma20 = closes.Average();
                    var price = bar.Close;
                    // Trend strength: distance from MA20, normalized
                    var trendStrength = (double)((price - ma20) / ma20);
                    if (trendStrength > 0.02)
                    {
                        trendDirection = InsightDirection.Up;
                        trendMagnitude = Math.Min(1.0, Math.Abs(trendStrength) * 15);
                    }
                    else if (trendStrength < -0.02)
                    {
                        trendDirection = InsightDirection.Down;
                        trendMagnitude = Math.Min(1.0, Math.Abs(trendStrength) * 15);
                    }
                }

                // === Fusion decision (priority: risk > trend > IV-RV) ===
                // Rationale: in Feb-Jun 2024 the market drifted up ~3-5%.
                // Trend-following should capture this drift; IV-RV mean-reversion
                // adds timing but is secondary.
                InsightDirection? finalDirection = null;
                double finalMagnitude = 0;

                if (regimeStressed)
                {
                    // Risk-off priority: stressed regime forces reduction
                    finalDirection = InsightDirection.Down;
                    finalMagnitude = 0.5 + (double)((regimePercentile - _regimeHighThreshold) / (1.0m - _regimeHighThreshold)) * 0.5;
                }
                else if (trendDirection.HasValue)
                {
                    // Trend is primary signal — follow the drift
                    finalDirection = trendDirection;
                    finalMagnitude = trendMagnitude;
                    // IV-RV confluence enhances
                    if (ivRvDirection == trendDirection)
                        finalMagnitude = Math.Min(1.0, finalMagnitude + ivRvMagnitude * 0.3);
                }
                else if (ivRvDirection.HasValue)
                {
                    // No trend but IV-RV signal — smaller mean-reversion bet
                    finalDirection = ivRvDirection;
                    finalMagnitude = ivRvMagnitude * 0.5;
                }

                if (finalDirection.HasValue)
                {
                    insights.Add(Insight.Price(symbol, _insightPeriod, finalDirection.Value,
                        magnitude: finalMagnitude, confidence: (double)varResult.RawValue, sourceModel: Name));
                    algorithm.Debug($"[VarFactorZoo] {symbol} regime={regimePercentile:F2} IV-RV z={ivRvZ:F2} trend={trendDirection} dir={finalDirection} mag={finalMagnitude:F2}");
                }
                else
                {
                    // Flat insight to maintain current position (avoid unnecessary rebalance)
                    insights.Add(Insight.Price(symbol, _insightPeriod, InsightDirection.Flat,
                        magnitude: 0, confidence: null, sourceModel: Name));
                }
            }
            return insights;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
