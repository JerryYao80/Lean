using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Chip
{
    /// <summary>
    /// ChipPeak综合因子 - 多因子融合（筹码+资金流+估值）.
    /// Precomputed模式: 因子值由外部Pipeline预计算注入（ChipDataLoader+ChipPeakFactors.py）.
    /// 数据源: cyq_perf parquet.
    /// </summary>
    public class ChipPeakCompositeFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injectedValues = new();

        public string Id => "chip_peak_composite";
        public string Name => "Chip Peak Composite";
        public FactorCategory Category => FactorCategory.Chip;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "cyq_perf+moneyflow";

        /// <summary>注入预计算的因子值（由AlphaModel在批量扫描后调用）</summary>
        public void InjectValue(Symbol symbol, decimal value)
        {
            _injectedValues[symbol] = value;
        }

        /// <summary>清除注入的值（每个时间步结束后调用）</summary>
        public void ClearInjectedValues()
        {
            _injectedValues.Clear();
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history)
        {
            if (_injectedValues.TryGetValue(symbol, out var value))
            {
                return new FactorResult
                {
                    Symbol = symbol, Time = time, FactorId = Id,
                    Quality = FactorDataQuality.Valid, Value = value, RawValue = value
                };
            }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time)
        {
            return new FactorRankResult { Time = time, FactorId = Id, ValidCount = 0, MissingCount = 0 };
        }

        public bool IsAvailable(Symbol symbol, DateTime time) => _injectedValues.ContainsKey(symbol);
    }
}