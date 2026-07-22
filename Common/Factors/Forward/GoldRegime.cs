using System;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 实际利率 regime 状态。UNAVAILABLE 显式区分"数据缺失"与"利率稳定(STABLE)"，
    /// 即使当前行为相同，为将来 FRED 接入留可解释性。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §4.1。
    /// </summary>
    public enum GoldRegime
    {
        /// <summary>实际利率快速上行（5d > 阈值 且 20d > 0）</summary>
        RISING_FAST,
        /// <summary>实际利率快速下行</summary>
        FALLING_FAST,
        /// <summary>实际利率稳定（|20d| ≤ 阈值）</summary>
        STABLE,
        /// <summary>方向漂移（介于阈值之间或方向不一致）</summary>
        DRIFTING,
        /// <summary>DFII10 数据缺失（FRED_API_KEY 未配置）。行为同 STABLE，状态区分。</summary>
        UNAVAILABLE
    }

    /// <summary>skip_reason 正交字段：核心信号/数据失效原因，与 regime 独立。</summary>
    public enum GoldSkipReason
    {
        NONE,
        /// <summary>AU/518880 时间戳滞后 > 2h 或回测 T 日缺行。核心信号故障。</summary>
        DATA_STALE,
        /// <summary>Z 计算所需 60 日信号历史不足（冷启动）。</summary>
        INSUFFICIENT_HISTORY,
        /// <summary>AU 与 FXCM XAUUSD 方向背离且幅度超阈值。</summary>
        CROSS_CHECK_FAIL,
        /// <summary>|Z| ≤ 1.5，正常不进场。非故障。</summary>
        NO_EDGE
    }
}
