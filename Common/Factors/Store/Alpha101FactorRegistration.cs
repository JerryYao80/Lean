/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Alpha101 独立 id 注册 (C# 读路径)。
 * 把 101 个 alphaNNN 各注册一个 RParquetAdapter，使 FactorStore.Get("alphaNNN")
 * 能单点读取 result/factor-zoo/alphaNNN/<date>/<code>.parquet。RParquetAdapter
 * 本就能读这种单行 shape，无需新适配器。
 */
using QuantConnect.Factors.Core;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Registers alpha001..alpha101 as 101 independent FactorStore ids, each backed
    /// by an RParquetAdapter reading result/factor-zoo/alphaNNN/&lt;date&gt;/&lt;code&gt;.parquet.
    /// Single-alpha reads: Get("alphaNNN") does 1 GIL + 1 parquet read.
    /// </summary>
    internal static class Alpha101FactorRegistration
    {
        public static void Register(FactorStore store)
        {
            for (int n = 1; n <= 101; n++)
            {
                var aid = $"alpha{n:D3}";
                store.Register(aid, new RParquetAdapter($"factor-zoo/{aid}", aid));
            }
        }
    }
}
