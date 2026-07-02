using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Volatility;
using QuantConnect.Indicators;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    /// <summary>
    /// 期权波动率套利 AlphaModel (因子动物园三层管线版).
    /// Layer 1 (Factor): IVPercentileFactor/HVFactor/IVSkewFactor/IVTermStructureFactor
    /// Layer 2 (Alpha):  本模型从 FactorRegistry 取因子值 → 生成 Insight
    /// Layer 3 (Strategy): 策略组合 Alpha + Portfolio + Risk
    /// </summary>
    public class OptionVolArbFactorZooAlphaModel : IAlphaModel
    {
        private readonly IVPercentileFactor _ivFactor;
        private readonly HVFactor _hvFactor;
        private readonly IVSkewFactor _skewFactor;
        private readonly IVTermStructureFactor _ivtsFactor;

        private readonly decimal _ivRvZScoreThreshold;
        private readonly decimal _ivtsThreshold;
        private readonly decimal _skewPercentileHigh;
        private readonly decimal _skewPercentileLow;
        private readonly int _rvLookbackDays;

        private readonly RollingWindow<decimal> _ivHistory;
        private readonly RollingWindow<decimal> _rvHistory;
        private readonly RollingWindow<decimal> _skewHistory;

        public string Name => "OptionVolArbFactorZoo";

        public OptionVolArbFactorZooAlphaModel(
            decimal ivRvZScoreThreshold = 2.0m,
            decimal ivtsThreshold = 1.3m,
            decimal skewPercentileHigh = 0.90m,
            decimal skewPercentileLow = 0.10m,
            int rvLookbackDays = 20,
            int ivLookbackDays = 60)
        {
            _ivRvZScoreThreshold = ivRvZScoreThreshold;
            _ivtsThreshold = ivtsThreshold;
            _skewPercentileHigh = skewPercentileHigh;
            _skewPercentileLow = skewPercentileLow;
            _rvLookbackDays = rvLookbackDays;

            // Layer 1: 从因子动物园获取因子
            FactorRegistry.Initialize();
            _ivFactor = FactorRegistry.Get("iv_pct_252d") as IVPercentileFactor ?? new IVPercentileFactor(252);
            _hvFactor = FactorRegistry.Get("hv_20d") as HVFactor ?? new HVFactor(20);
            _skewFactor = FactorRegistry.Get("iv_skew_252d") as IVSkewFactor ?? new IVSkewFactor(252);
            _ivtsFactor = FactorRegistry.Get("iv_term_structure") as IVTermStructureFactor ?? new IVTermStructureFactor();

            _ivHistory = new RollingWindow<decimal>(ivLookbackDays);
            _rvHistory = new RollingWindow<decimal>(rvLookbackDays);
            _skewHistory = new RollingWindow<decimal>(ivLookbackDays);
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var insights = new List<Insight>();
            foreach (var kvp in data.Bars)
            {
                var symbol = kvp.Key;
                var history = algorithm.History<TradeBar>(symbol, _rvLookbackDays, Resolution.Daily);
                var ivR = _ivFactor.Compute(symbol, algorithm.Time, null);
                var rvR = _hvFactor.Compute(symbol, algorithm.Time, history);
                var skewR = _skewFactor.Compute(symbol, algorithm.Time, null);
                var ivtsR = _ivtsFactor.Compute(symbol, algorithm.Time, null);

                if (ivR.Quality != FactorDataQuality.Valid || rvR.Quality != FactorDataQuality.Valid) continue;

                _ivHistory.Add(ivR.RawValue);
                _rvHistory.Add(rvR.RawValue);
                if (skewR.Quality == FactorDataQuality.Valid) _skewHistory.Add(skewR.RawValue);
                var ivts = ivtsR.Quality == FactorDataQuality.Valid ? ivtsR.RawValue : 1.0m;

                var insight = GenerateInsight(symbol, algorithm.Time, ivR.RawValue, rvR.RawValue,
                    skewR.Quality == FactorDataQuality.Valid ? skewR.RawValue : (decimal?)null, ivts);
                if (insight != null) insights.Add(insight);
            }
            return insights;
        }

        private Insight GenerateInsight(Symbol symbol, DateTime time, decimal iv, decimal rv, decimal? skew, decimal ivts)
        {
            var period = TimeSpan.FromDays(1);
            if (_ivHistory.Count >= 20 && _rvHistory.Count >= 20)
            {
                var minCount = Math.Min(_ivHistory.Count, _rvHistory.Count);
                var ivArr = _ivHistory.Take(minCount).ToList();
                var rvArr = _rvHistory.Take(minCount).ToList();
                var spreads = ivArr.Select((iv_, i) => iv_ - rvArr[i]).ToList();
                var mean = spreads.Average();
                var std = (decimal)Math.Sqrt((double)spreads.Select(x => (x - mean) * (x - mean)).Sum() / Math.Max(1, spreads.Count - 1));
                var z = std > 0.001m ? (iv - rv - mean) / std : 0m;

                if (z > _ivRvZScoreThreshold)
                    return Insight.Price(symbol, period, InsightDirection.Down, (double)Math.Abs(z), null, Name);
                if (z < -_ivRvZScoreThreshold)
                    return Insight.Price(symbol, period, InsightDirection.Up, (double)Math.Abs(z), null, Name);
            }

            if (ivts > _ivtsThreshold)
                return Insight.Price(symbol, period, InsightDirection.Down, (double)(ivts - 1.0m), null, Name);
            if (ivts < 1.0m / _ivtsThreshold)
                return Insight.Price(symbol, period, InsightDirection.Up, (double)(1.0m - ivts), null, Name);

            if (skew.HasValue && _skewHistory.Count >= 40)
            {
                var sorted = _skewHistory.OrderBy(x => x).ToList();
                var highPct = sorted[(int)(_skewHistory.Count * _skewPercentileHigh)];
                var lowPct = sorted[(int)(_skewHistory.Count * _skewPercentileLow)];
                if (skew.Value > highPct)
                    return Insight.Price(symbol, period, InsightDirection.Down, (double)(skew.Value - highPct), null, Name);
                if (skew.Value < lowPct)
                    return Insight.Price(symbol, period, InsightDirection.Up, (double)(lowPct - skew.Value), null, Name);
            }
            return null;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
