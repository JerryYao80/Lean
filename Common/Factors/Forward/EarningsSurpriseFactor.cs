using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 业绩超预期幅度因子（基本面意外/预期差）。
    /// 原理：express vs forecast 的净利润区间比对，实际值相对预告中值的偏离。
    /// 数据立足：tushare express（业绩快报 net_profit）/forecast（预告 net_profit_min/max）。
    /// 计算立足：surprise = (express_net_profit - forecast_mid) / |forecast_mid|，正=超预期。
    /// 消费立足：Layer A 选股中低频 alpha 源；Layer B 风控做业绩雷预警。
    /// Precomputed 模式：由外部 Pipeline 注入。
    /// 详见 docs/qianzhan.md 基本面意外/预期差类。
    /// </summary>
    public class EarningsSurpriseFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "earnings_surprise";
        public string Name => "Earnings Surprise vs Forecast Mid";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "tushare_express+forecast";

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

        /// <summary>计算业绩超预期 = (express_net_profit - forecast_mid) / |forecast_mid|。夹到 [-5,5]。</summary>
        public static decimal ComputeSurprise(decimal expressNetProfit, decimal forecastMin, decimal forecastMax)
        {
            var mid = (forecastMin + forecastMax) / 2m;
            if (Math.Abs(mid) < 1m) return 0m;
            var surprise = (expressNetProfit - mid) / Math.Abs(mid);
            return Math.Max(-5m, Math.Min(5m, surprise));
        }
    }
}
