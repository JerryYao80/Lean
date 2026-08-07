using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 动量加速度因子（量价微观结构，日内领先）。
    /// 原理：bak_daily.attack（攻击波）与 strength（强弱度）构造动量加速度，比单纯动量更前瞻。
    /// 数据立足：tushare bak_daily 表，字段 attack（攻击波，当日主动买入强度）/strength（强弱度，综合动量分）。
    /// 计算立足：accel = 0.5 * sigmoid(attack) + 0.5 * sigmoid(strength)，归一化到 [0,1]。
    /// 消费立足：Layer A 选股短期动量加速 alpha；Layer B 风控做动量衰减预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 量价微观结构类。
    /// </summary>
    public class MomentumAccelerationFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "momentum_acceleration";
        public string Name => "Momentum Acceleration (Attack+Strength)";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.Both;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_bak_daily";

        public void InjectValue(Symbol symbol, decimal value) => _injected[symbol] = value;
        public void ClearInjectedValues() => _injected.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_injected.TryGetValue(symbol, out var value))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = value, RawValue = value };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injected.ContainsKey(symbol);

        /// <summary>
        /// 计算动量加速度 = 0.5*sigmoid(attack) + 0.5*sigmoid(strength)，归一化到 [0,1]。
        /// attack/strength 量级通常 0-100，用 sigmoid 映射。供 Python 导出器调用。
        /// </summary>
        public static decimal ComputeAcceleration(decimal attack, decimal strength)
        {
            static double Sigmoid(double x) => 1.0 / (1.0 + Math.Exp(-x / 10.0));
            var score = 0.5 * Sigmoid((double)attack) + 0.5 * Sigmoid((double)strength);
            return (decimal)Math.Max(0.0, Math.Min(1.0, score));
        }
    }
}
