using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 趋势方向因子(Runtime)。MA20/120 sign + AU.SHF 同向确认。
    /// Update518880/UpdateAu 由 Strategy OnData 喂 close;Compute 查询当前 SMA。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.1。
    /// 注：Common 项目无法引用 Indicators 项目(会形成循环依赖)，故内置轻量 RollingMA。
    /// </summary>
    public class Gold2TrendFactor : IFactor
    {
        private readonly RollingMean _ma518880Short;
        private readonly RollingMean _ma518880Long;
        private readonly RollingMean _maAuShort;
        private readonly RollingMean _maAuLong;

        public string Id => "gold2_trend";
        public string Name => "Gold2 Trend (MA20/120 + AU confirm)";
        public FactorCategory Category => FactorCategory.Trend;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "518880_fund_daily+au_shf_fut_daily";

        public Gold2TrendFactor(int shortPeriod = 20, int longPeriod = 120)
        {
            _ma518880Short = new RollingMean(shortPeriod);
            _ma518880Long = new RollingMean(longPeriod);
            _maAuShort = new RollingMean(shortPeriod);
            _maAuLong = new RollingMean(longPeriod);
        }

        public void Update518880(decimal close, DateTime time)
        {
            _ma518880Short.Update(close);
            _ma518880Long.Update(close);
        }

        public void UpdateAu(decimal close, DateTime time)
        {
            _maAuShort.Update(close);
            _maAuLong.Update(close);
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (!_ma518880Long.IsReady || !_maAuLong.IsReady)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m };
            decimal trend518880 = Math.Sign(_ma518880Short.Current - _ma518880Long.Current);
            decimal trendAu = Math.Sign(_maAuShort.Current - _maAuLong.Current);
            decimal confirm = (trend518880 == trendAu && trend518880 != 0m) ? 1m : 0.5m;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = trend518880, RawValue = confirm };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _ma518880Long.IsReady && _maAuLong.IsReady;

        /// <summary>轻量滚动均值(避免 Common→Indicators 循环依赖)。</summary>
        private sealed class RollingMean
        {
            private readonly int _period;
            private readonly Queue<decimal> _window;
            private decimal _sum;

            public RollingMean(int period)
            {
                if (period <= 0) throw new ArgumentOutOfRangeException(nameof(period));
                _period = period;
                _window = new Queue<decimal>(period);
            }

            public bool IsReady => _window.Count >= _period;
            public decimal Current => _window.Count == 0 ? 0m : _sum / _window.Count;

            public void Update(decimal value)
            {
                _window.Enqueue(value);
                _sum += value;
                if (_window.Count > _period)
                {
                    _sum -= _window.Dequeue();
                }
            }
        }
    }
}
