/*
 * 期权波动率套利策略 (因子动物园三层管线版)
 *
 * 三层管线:
 *   Layer 1 (Factor): FactorRegistry (IV/HV/Skew/TermStructure)
 *   Layer 2 (Model):  OptionVolArbFactorZooAlphaModel + EqualWeightPortfolioModel + MaxDrawdownRiskModel
 *   Layer 3 (Strategy): 本文件 - 固定标的 510050 + 组合模型
 *
 * 替代原版 AShareOptionVolatilityArbitrageAlgorithm 的命令式 OnData 逻辑,
 * 改为 LEAN Framework 五层架构.
 */

using QuantConnect.Algorithm;
using QuantConnect.Algorithm.CSharp.Models;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;

namespace QuantConnect.Algorithm.CSharp
{
    public class OptionVolArbFactorZooStrategy : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2024, 2, 8);
            SetEndDate(2024, 6, 28);
            SetAccountCurrency("CNY");
            SetCash(1000000);

            // 标的: 510050 ETF (IV CSV 数据最全的标的)
            AddEquity("510050", Resolution.Daily, Market.SSE);

            // Layer 2: 模型动物园组件
            SetAlpha(new OptionVolArbFactorZooAlphaModel(
                ivRvZScoreThreshold: 2.0m,
                ivtsThreshold: 1.3m,
                skewPercentileHigh: 0.90m,
                skewPercentileLow: 0.10m));

            SetPortfolioConstruction(new EqualWeightPortfolioModel());
            SetRiskManagement(new MaxDrawdownRiskModel(maxDrawdown: 0.20m));

            SetBenchmark(x => 0);
            SetWarmUp(60, Resolution.Daily);

            Log("[OptionVolArb-FactorZoo] Three-layer pipeline strategy initialized");
            Log("[OptionVolArb-FactorZoo] Layer1: FactorRegistry (IV/HV/Skew/IVTS)");
            Log("[OptionVolArb-FactorZoo] Layer2: OptionVolArbFactorZooAlphaModel + EqualWeight + MaxDrawdown");
            Log("[OptionVolArb-FactorZoo] Layer3: OptionVolArbFactorZooStrategy (510050)");
        }

        public override void OnEndOfAlgorithm()
        {
            var totalReturn = (Portfolio.TotalPortfolioValue - 1000000m) / 1000000m;
            Log($"[OptionVolArb-FactorZoo] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[OptionVolArb-FactorZoo] Total return: {totalReturn:P2}");
            Log($"[OptionVolArb-FactorZoo] Total trades: {Transactions.OrdersCount}");
        }
    }
}
