using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L1 Universe: 固定 518880 SSE。AU.SHF/VIX/DFII10 不入 universe
    /// (因子内通过 AddData 取值,不参与下单/选股)。详见
    /// docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §5.1。
    /// </summary>
    public class Gold2UniverseSelectionModel : ManualUniverseSelectionModel
    {
        public Gold2UniverseSelectionModel() : base(new[]
        {
            Symbol.Create("518880", SecurityType.Equity, Market.SSE)
        })
        {
        }
    }
}
