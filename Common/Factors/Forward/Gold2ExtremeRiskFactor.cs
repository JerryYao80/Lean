using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 极端风险开关因子(Runtime)。Trigger=VIX_t>VIX_P95 OR RVol_60d>RVol_P95。
    /// 窗口满 minWindow(默认252)才计算 P95。详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.3。
    /// 注:Common 项目无法引用 Indicators 项目(会形成循环依赖),故内置轻量 FIFO 滚动窗口(对齐 Gold2TrendFactor.RollingMean 模式)。
    /// </summary>
    public class Gold2ExtremeRiskFactor : IFactor
    {
        private readonly RollingWindowDecimal _vixHist;
        private readonly RollingWindowDecimal _rvolHist;
        private readonly int _minWindow;
        private decimal _todayVix, _todayRvol;

        public string Id => "gold2_extreme_risk";
        public string Name => "Gold2 Extreme Risk (VIX/RVol P95)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "fred_vix+518880_realized_vol";

        public Gold2ExtremeRiskFactor(int vixWindow = 1260, int minWindow = 252)
        {
            if (vixWindow <= 0) throw new ArgumentOutOfRangeException(nameof(vixWindow), "vixWindow must be positive");
            if (minWindow <= 0) throw new ArgumentOutOfRangeException(nameof(minWindow), "minWindow must be positive");
            if (minWindow > vixWindow) throw new ArgumentOutOfRangeException(nameof(minWindow), "minWindow must be <= vixWindow");
            _vixHist = new RollingWindowDecimal(vixWindow);
            _rvolHist = new RollingWindowDecimal(vixWindow);
            _minWindow = minWindow;
        }

        public void UpdateVix(decimal vix, DateTime time)
        {
            _vixHist.Add(vix);
            _todayVix = vix;
        }

        public void UpdateRvol60(decimal rvolAnn, DateTime time)
        {
            _rvolHist.Add(rvolAnn);
            _todayRvol = rvolAnn;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_vixHist.Count < _minWindow && _rvolHist.Count < _minWindow)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m, RawValue = _todayRvol };

            bool triggered = false;
            if (_vixHist.Count >= _minWindow)
            {
                var p95 = Percentile(_vixHist.Snapshot(), 0.95);
                if (_todayVix > p95) triggered = true;
            }
            if (!triggered && _rvolHist.Count >= _minWindow)
            {
                var p95 = Percentile(_rvolHist.Snapshot(), 0.95);
                if (_todayRvol > p95) triggered = true;
            }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = triggered ? 1m : 0m, RawValue = _todayRvol };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _vixHist.Count >= _minWindow || _rvolHist.Count >= _minWindow;

        /// <summary>
        /// 经验分位数(nearest-rank, ceil(q*n)-1 index)。对 P95 选第 95 百分位的最近实际样本值。
        /// 空 xs 返回 decimal.MaxValue(使任何 today 值不触发,安全默认)。
        /// </summary>
        public static decimal Percentile(IEnumerable<decimal> xs, double q)
        {
            var sorted = xs.OrderBy(x => x).ToList();
            if (sorted.Count == 0) return decimal.MaxValue;
            int idx = (int)Math.Ceiling(q * sorted.Count) - 1;
            if (idx < 0) idx = 0;
            if (idx >= sorted.Count) idx = sorted.Count - 1;
            return sorted[idx];
        }

        /// <summary>
        /// 轻量 FIFO 滚动窗口(避免 Common→Indicators 循环依赖)。
        /// 容量 cap:超过则丢弃队首;Count 返回当前元素数(对齐 RollingWindow&lt;T&gt;.Count 语义)。
        /// </summary>
        private sealed class RollingWindowDecimal
        {
            private readonly int _capacity;
            private readonly Queue<decimal> _queue;

            public RollingWindowDecimal(int capacity)
            {
                if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity));
                _capacity = capacity;
                _queue = new Queue<decimal>(capacity);
            }

            public int Count => _queue.Count;

            public void Add(decimal value)
            {
                _queue.Enqueue(value);
                if (_queue.Count > _capacity)
                {
                    _queue.Dequeue();
                }
            }

            public IEnumerable<decimal> Snapshot() => _queue;
        }
    }
}
