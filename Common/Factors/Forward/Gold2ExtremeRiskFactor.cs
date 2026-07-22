using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Indicators;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 极端风险开关因子(Runtime)。Trigger=VIX_t>VIX_P95 OR RVol_60d>RVol_P95。
    /// P95 用 ≤ t-1 历史(不含当日 VIX/RVol)。VIX 缺失日: 因子用 lastVix 前值填充(从历史中查 Time ≤ time 的最近值)。
    /// 窗口满 MinWindow(默认252)才计算 P95。详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.3。
    /// </summary>
    public class Gold2ExtremeRiskFactor : IFactor
    {
        /// <summary>VIX 历史窗口容量(约5年交易日)。</summary>
        public const int DefaultVixWindow = 1260;

        /// <summary>最小窗口长度(约1年交易日),窗口不满则不触发。</summary>
        public const int DefaultMinWindow = 252;

        private readonly RollingWindow<HistoryEntry> _vixHist;
        private readonly RollingWindow<HistoryEntry> _rvolHist;
        private readonly int _minWindow;

        public string Id => "gold2_extreme_risk";
        public string Name => "Gold2 Extreme Risk (VIX/RVol P95)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "fred_vix+518880_realized_vol";

        public Gold2ExtremeRiskFactor(int vixWindow = DefaultVixWindow, int minWindow = DefaultMinWindow)
        {
            if (vixWindow <= 0) throw new ArgumentOutOfRangeException(nameof(vixWindow), "vixWindow must be positive");
            if (minWindow <= 0) throw new ArgumentOutOfRangeException(nameof(minWindow), "minWindow must be positive");
            if (minWindow > vixWindow) throw new ArgumentOutOfRangeException(nameof(minWindow), "minWindow must be <= vixWindow");
            _vixHist = new RollingWindow<HistoryEntry>(vixWindow);
            _rvolHist = new RollingWindow<HistoryEntry>(vixWindow);
            _minWindow = minWindow;
        }

        /// <summary>
        /// 更新 VIX 值。若当日 VIX 缺失则不调用此方法,Compute 时自动从历史中前值填充(lastVix = Time ≤ time 的最近值)。
        /// </summary>
        public void UpdateVix(decimal vix, DateTime time)
        {
            _vixHist.Add(new HistoryEntry(time, vix));
        }

        /// <summary>
        /// 更新 RVol60 值。若当日 RVol 缺失则不调用此方法,Compute 时自动从历史中前值填充。
        /// </summary>
        public void UpdateRvol60(decimal rvolAnn, DateTime time)
        {
            _rvolHist.Add(new HistoryEntry(time, rvolAnn));
        }

        /// <summary>
        /// 计算极端风险触发状态。无前视:
        ///  - VIX_t/RVol_t = 历史中 Time ≤ time 的最近值(前值填充缺失日;不读 time 之后的数据)。
        ///  - P95 = 仅使用 Time &lt; time 的历史(严格排除当日)。
        /// </summary>
        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            var (currentVix, vixHistory) = SplitCurrentAndHistory(_vixHist, time);
            var (currentRvol, rvolHistory) = SplitCurrentAndHistory(_rvolHist, time);

            if (vixHistory.Count < _minWindow && rvolHistory.Count < _minWindow)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m, RawValue = currentRvol };

            bool triggered = false;
            if (vixHistory.Count >= _minWindow)
            {
                var p95 = Percentile(vixHistory, 0.95);
                if (currentVix > p95) triggered = true;
            }
            if (!triggered && rvolHistory.Count >= _minWindow)
            {
                var p95 = Percentile(rvolHistory, 0.95);
                if (currentRvol > p95) triggered = true;
            }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = triggered ? 1m : 0m, RawValue = currentRvol };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => SplitCurrentAndHistory(_vixHist, time).History.Count >= _minWindow || SplitCurrentAndHistory(_rvolHist, time).History.Count >= _minWindow;

        /// <summary>
        /// 将窗口拆分为"当前值(Time ≤ time 的最近值,前值填充)"与"历史值(Time &lt; time,严格排除当日)"。
        /// 无前视:不读 Time &gt; time 的条目。
        /// </summary>
        private static (decimal Current, List<decimal> History) SplitCurrentAndHistory(RollingWindow<HistoryEntry> window, DateTime time)
        {
            var history = new List<decimal>();
            decimal current = 0m;
            DateTime currentTime = DateTime.MinValue;
            foreach (var entry in window)
            {
                if (entry.Time < time)
                {
                    history.Add(entry.Value);
                    // 当前值 = Time ≤ time 的最近值(前值填充);取 Time 最大的那个。
                    if (entry.Time >= currentTime)
                    {
                        currentTime = entry.Time;
                        current = entry.Value;
                    }
                }
                // Time == time 的当日条目算作"当前值"候选(Time ≤ time),但不进 P95 历史。
                else if (entry.Time == time)
                {
                    if (entry.Time >= currentTime)
                    {
                        currentTime = entry.Time;
                        current = entry.Value;
                    }
                }
                // Time > time 的未来条目:忽略(无前视)。
            }
            return (current, history);
        }

        /// <summary>
        /// 经验分位数(nearest-rank, ceil(q*n)-1 index)。对 P95 选第 95 百分位的最近实际样本值。
        /// 空 xs 返回 decimal.MaxValue(使任何 current 值不触发,安全默认)。
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
        /// 历史条目:存储值与时间戳,支持无前视 P95 计算与前值填充。
        /// </summary>
        private readonly struct HistoryEntry
        {
            public DateTime Time { get; }
            public decimal Value { get; }
            public HistoryEntry(DateTime time, decimal value) { Time = time; Value = value; }
        }
    }
}
