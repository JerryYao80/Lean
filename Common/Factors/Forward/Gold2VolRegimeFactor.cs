using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 波动率目标仓位因子(Runtime,有状态 EWMA)。
    /// r=ln(P_t/P_{t-1}); σ²_t=λσ²_{t-1}+(1-λ)r²_{t-1}; w=min(1,σ_target/σ_ann); w_smooth=αw+(1-α)w_prev。
    /// Update 喂 close 并入队收益;Compute(time) 惰性折叠 timestamp &lt; time 的待处理收益(无前视:不读 t 及之后的收益)。
    /// Value=w_smooth, RawValue=σ_ann。详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.2。
    /// 注：Common 项目无法引用 Indicators 项目(会形成循环依赖)，故用 Queue<decimal>+math 自持。
    /// </summary>
    public class Gold2VolRegimeFactor : IFactor
    {
        private readonly decimal _lambda, _volTarget, _alpha;
        private readonly int _warmup;
        private readonly Queue<decimal> _warmupReturns = new Queue<decimal>();
        // 待折叠收益(ewmaReady 之后入队),按时间单调递增。Compute(time) 折叠 time 之前的全部收益。
        private readonly Queue<ReturnPoint> _pendingReturns = new Queue<ReturnPoint>();
        private decimal _sigma2Prev, _wSmoothPrev, _prevClose;
        private bool _ewmaReady;

        public string Id => "gold2_vol_regime";
        public string Name => "Gold2 Vol-Target (EWMA λ=0.94)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "518880_fund_daily";

        public Gold2VolRegimeFactor(decimal lambda, decimal volTarget, int warmup, decimal alpha)
        {
            if (warmup <= 0) throw new ArgumentOutOfRangeException(nameof(warmup), "warmup must be positive");
            if (lambda <= 0m || lambda >= 1m) throw new ArgumentOutOfRangeException(nameof(lambda), "lambda must be in (0,1)");
            if (volTarget <= 0m) throw new ArgumentOutOfRangeException(nameof(volTarget), "volTarget must be positive");
            if (alpha < 0m || alpha > 1m) throw new ArgumentOutOfRangeException(nameof(alpha), "alpha must be in [0,1]");
            _lambda = lambda; _volTarget = volTarget; _warmup = warmup; _alpha = alpha;
        }

        public void Update(decimal close, DateTime time)
        {
            if (_prevClose > 0)
            {
                var r = (decimal)Math.Log((double)(close / _prevClose));
                if (!_ewmaReady)
                {
                    _warmupReturns.Enqueue(r);
                    if (_warmupReturns.Count >= _warmup)
                    {
                        _sigma2Prev = Variance(_warmupReturns);
                        _ewmaReady = true;
                    }
                }
                else
                {
                    _pendingReturns.Enqueue(new ReturnPoint(time, r));
                }
            }
            _prevClose = close;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (!_ewmaReady)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m };
            // 惰性折叠:仅吸收 timestamp 严格小于 `time` 的收益(无前视)。
            // σ²_t 使用 r_{t-1}(lag-1),Compute(t) 不读 t 及之后的收益。
            while (_pendingReturns.Count > 0 && _pendingReturns.Peek().Time < time)
            {
                var rp = _pendingReturns.Dequeue();
                _sigma2Prev = _lambda * _sigma2Prev + (1m - _lambda) * rp.Value * rp.Value;
                var sigmaAnn = (decimal)Math.Sqrt((double)_sigma2Prev) * (decimal)Math.Sqrt(252);
                var w = sigmaAnn > 0 ? Math.Min(1.0m, _volTarget / sigmaAnn) : 1.0m;
                _wSmoothPrev = _alpha * w + (1m - _alpha) * _wSmoothPrev;
            }
            var sigmaAnnCur = (decimal)Math.Sqrt((double)_sigma2Prev) * (decimal)Math.Sqrt(252);
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = _wSmoothPrev, RawValue = sigmaAnnCur };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _ewmaReady;

        /// <summary>总体方差(n 分母,非样本方差 n-1)。EWMA warmup seeding 用总体方差;
        /// HVFactor 实现波动用样本方差(n-1),二者用途不同( seeding vs 估计)故刻意不统一。
        /// 供本类 warmup 与后续任务(RVol 等)复用。</summary>
        public static decimal Variance(IEnumerable<decimal> xs)
        {
            var list = new List<decimal>(xs);
            if (list.Count < 2) return 0m;
            var mean = 0m; foreach (var x in list) mean += x; mean /= list.Count;
            var v = 0m; foreach (var x in list) v += (x - mean) * (x - mean);
            return v / list.Count;
        }

        private readonly struct ReturnPoint
        {
            public DateTime Time { get; }
            public decimal Value { get; }
            public ReturnPoint(DateTime time, decimal value) { Time = time; Value = value; }
        }
    }
}
