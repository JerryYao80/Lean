/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Technical indicator 独立 id 注册 (C# 读路径)。
 * 把 48 个 tech_* 各注册一个 RParquetAdapter，使 FactorStore.Get("tech_macd")
 * 能单点读取 result/factor-zoo/tech_macd/<date>.parquet (多行, ts_code 过滤)。
 * 镜像 Alpha101FactorRegistration 的逐 id 注册模式。
 *
 * fid 列表与 data-source/tushare/technical/panel_loader.py 的 FACTOR_IDS 一致
 * (dir == value column == fid, e.g. tech_macd / tech_kdj_j / tech_rsi_6)。
 */
using QuantConnect.Factors.Core;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Registers the 48 technical-indicator fids (tech_macd, tech_rsi_6, ...)
    /// as independent FactorStore ids, each backed by an RParquetAdapter reading
    /// result/factor-zoo/&lt;fid&gt;/&lt;date&gt;.parquet. Single-factor reads:
    /// Get("tech_macd") does 1 GIL + 1 parquet read.
    /// </summary>
    internal static class TechnicalFactorRegistration
    {
        /// <summary>48 technical fids; mirrors technical/panel_loader.py FACTOR_IDS.</summary>
        public static readonly string[] FactorIds =
        {
            "tech_macd", "tech_macd_dif", "tech_macd_dea",
            "tech_rsi_6", "tech_rsi_12", "tech_rsi_24",
            "tech_kdj_k", "tech_kdj_d", "tech_kdj_j",
            "tech_boll_upper", "tech_boll_mid", "tech_boll_lower",
            "tech_bias1", "tech_bias2", "tech_bias3",
            "tech_cci", "tech_wr", "tech_mfi", "tech_mtm", "tech_roc",
            "tech_obv", "tech_psy", "tech_trix", "tech_dpo", "tech_cr",
            "tech_emv", "tech_mass", "tech_asi", "tech_bbi", "tech_atr",
            "tech_vr",
            "tech_dmi_adx", "tech_dmi_pdi", "tech_dmi_mdi",
            "tech_expma_12", "tech_expma_50",
            "tech_ktn_upper", "tech_ktn_mid", "tech_ktn_down",
            "tech_taq_up", "tech_taq_mid", "tech_taq_down",
            "tech_dfma_dif", "tech_dfma_difma",
            "tech_xsii_td1", "tech_xsii_td2", "tech_xsii_td3", "tech_xsii_td4",
        };

        public static void Register(FactorStore store)
        {
            foreach (var fid in FactorIds)
            {
                store.Register(fid, new RParquetAdapter($"factor-zoo/{fid}", fid));
            }
        }
    }
}
