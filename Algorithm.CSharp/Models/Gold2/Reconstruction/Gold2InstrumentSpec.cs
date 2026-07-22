using System;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    /// <summary>
    /// Q-C 多标的统一(详见 docs/superpowers/specs/2026-07-21-gold2-real-llm-reconstruction-design.md §2):
    /// 单一 spec 描述一个 instrument 的所有可替换输入 —— ticker/market/data 源类型 + 数据 ticker +
    /// 两个因子工厂(RegimeFactorFactory / VolFactorFactory)。
    ///
    /// RegimeFactorFactory 接受**共享的** `GoldRealRateRegimeFactor` 内部实例 + cap,返回
    /// `Gold2RealRateCapFactor` 包装同一 inner。共享 inner 是 G0 的硬性不变量 —— DFII10 数据通过
    /// `OnData` 注入到 `_realrateInner`(Gold2BetaVolTargetStrategy.cs:144),而 `_realrate`
    /// 包装同一实例。任何 instrument 的 factory 必须把传入的 inner 透传给 cap factor,不得 `new`
    /// 新的 inner(否则 DFII10 注入会丢失,regime 永远为默认值)。
    ///
    /// 注意:518880 的 RegimeFactorFactory/VolFactorFactory 是**扩展点**,在基类
    /// Gold2ReconstructionCandidateBase.Initialize() 中并未被调用 —— 518880 的 factor 构造
    /// 保留内联(Gold2ReconstructionCandidateBase.cs:117-118),以维持 G0 byte-identical。
    /// 这两个工厂仅供未来 000300/000688 等新 instrument 在其各自的 factor 构造路径中使用:
    /// 调用方负责创建并维护共享 inner,然后把它传给 spec.RegimeFactorFactory。
    ///
    /// 当前唯一注册的 instrument 是 518880(SSE 黄金 ETF,AU.SHF 现货 + FRED VIX/DFII10 宏观)。
    /// </summary>
    public sealed record Gold2InstrumentSpec(
        string Ticker,
        string Market,
        Type GoldDataSource,
        string GoldDataTicker,
        Type MacroVixDataSource,
        string VixTicker,
        Type MacroRealRateDataSource,
        string RealRateTicker,
        Func<GoldRealRateRegimeFactor, decimal, Gold2RealRateCapFactor> RegimeFactorFactory,
        Func<decimal, decimal, int, decimal, Gold2VolRegimeFactor> VolFactorFactory);

    /// <summary>
    /// Instrument registry。新 instrument(沪深300/科创50)接入 = 在此加一条 case,
    /// 不写第二份策略、不写第二份基类、不写第二份复盘(spec §2.3)。
    /// </summary>
    public static class Gold2InstrumentRegistry
    {
        public static Gold2InstrumentSpec Get(string instrument) => instrument switch
        {
            // RegimeFactorFactory 透传 inner(不 new 新的)—— 正确共享语义。
            // VolFactorFactory 是无状态的,可直接调用。
            "518880" => new Gold2InstrumentSpec(
                "518880", "SSE",
                typeof(AuShfDailyBar), "AU.SHF",
                typeof(FredMacroData), "VIX",
                typeof(FredMacroData), "DFII10",
                (inner, cap) => new Gold2RealRateCapFactor(inner, cap),
                (l, vt, w, a) => new Gold2VolRegimeFactor(l, vt, w, a)),
            _ => throw new ArgumentException($"unknown instrument: {instrument}")
        };
    }
}
