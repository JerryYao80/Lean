# Barra CNE5 V3.2 量化策略实现逻辑

V3.2 **没有使用任何 LEAN 原生的选股、择时、权重分配、优化器、风控接口**。它完全绕过了 LEAN 的 Alpha Framework 五层架构，所有逻辑都是自研的。

## 对比：LEAN 原生 vs V3.2 自研

| 功能 | LEAN 原生接口 | V3.2 自研实现 |
|------|--------------|--------------|
| **选股** | `IUniverseSelectionModel`（CoarseFundamental, EmaCross 等） | `SelectPortfolio()` / `SelectPortfolioStratified()` — 按 Barra 因子加权 z-score 排名选 TopN |
| **择时** | `IAlphaModel`（EmaCross, MACD, RSI 等） | 无独立择时模块，通过 Sharpe-based exposure scaling 和 regime switching 间接调整仓位大小 |
| **权重分配** | `IPortfolioConstructionModel`（EqualWeighting, BlackLitterman 等） | `BuildBlackLittermanKellyWeights()` — 自研 BL 模型，用等权先验 + 因子 z-score 作为 view |
| **优化器** | `IPortfolioOptimizer`（MaxSharpe, MinVariance 等） | `NormalizeAndCapWeights()` — 单一权重上限约束 + 目标暴露度缩放 |
| **风控** | `IRiskManagementModel`（MaxDrawdown, TrailingStop 等） | `ApplyRiskManagementExits()` — 自研止损/移动止损/利润保护 + 冷却期 |
| **执行** | `IExecutionModel`（Immediate, VWAP 等） | `ExecuteRebalance()` — 直接调用 `SetHoldings()` / `Liquidate()` |

## V3.2 的完整流程

1. **因子数据输入** — `AShareBarraCNE5V2FactorData`（15 个 Barra CNE5 因子）通过自定义数据流订阅
2. **Regime 检测** — `DetectRegime()` 计算市场波动率，划分为 low_vol / mid_vol / high_vol 三种状态，切换因子权重
3. **IC/IR 校准** — `ComputeFactorIC()` + `ApplyIRWeightAdjustment()` 跟踪因子 IC 历史，用 Information Ratio 动态调整因子权重
4. **选股评分** — `ComputeScores()` 对每个股票做因子 z-score 加权求和
5. **行业分层选股** — `SelectPortfolioStratified()` 按行业比例分配 TopN 名额
6. **Sharpe-based 仓位缩放** — `ComputeSharpeBasedExposureScale()` 用滚动 Sharpe 替代 Kelly 公式
7. **波动率目标** — `ComputeVolTargetScale()` 根据已实现 vs 目标波动率缩放暴露度
8. **Black-Litterman 权重** — `BuildBlackLittermanKellyWeights()` 等权先验 + 因子 score 作为 view
9. **换手率约束** — `ApplyTurnoverConstraint()` 限制单次调仓换手率不超过阈值
10. **风控退出** — 止损(-12%)、利润保护激活(+18%后启用移动止损)、移动止损(-8%)、冷却期(5天)
11. **Monte Carlo** — `RunMonteCarloSimulation()` 因子扰动模拟计算置信区间

## 为什么不用 LEAN 原生接口？

因为 LEAN 原生框架是为美股设计的标准化五层管道（选股→择时→权重→风控→执行），而 Barra CNE5 V3.2 是一个**多因子量化策略**，它的逻辑无法自然地映射到这五层：

- **选股和择时耦合**：V3.2 的"择时"不是技术指标信号（EMA/MACD），而是 regime + IC/IR + Sharpe 共同决定选股范围和仓位大小
- **权重分配高度定制**：BL 模型的先验、view、confidence 都是因子相关的，无法用 LEAN 的 `BlackLittermanOptimizationPortfolioConstructionModel`（它需要协方差矩阵）
- **风控有 A 股特有规则**：T+1、冷却期、利润保护激活等
- **执行层不需要优化**：A 股 T+1 限制下，日内 VWAP/标准差执行没有意义

## 关键代码位置

| 模块 | 文件 | 方法 |
|------|------|------|
| 因子评分 | `AShareBarraCNE5V3_2SignalModel.cs` | `ComputeScores()` |
| IC/IR 校准 | `AShareBarraCNE5V3_2SignalModel.cs` | `ComputeFactorIC()` + `ApplyIRWeightAdjustment()` |
| 行业分层选股 | `AShareBarraCNE5V3_2SignalModel.cs` | `SelectPortfolioStratified()` |
| Black-Litterman 权重 | `AShareBarraCNE5V3_2SignalModel.cs` | `BuildBlackLittermanKellyWeights()` |
| Sharpe 仓位缩放 | `AShareBarraCNE5V3_2Algorithm.cs` | `ComputeSharpeBasedExposureScale()` |
| Regime 检测 | `AShareBarraCNE5V3_2Algorithm.cs` | `DetectRegime()` |
| 波动率目标 | `AShareBarraCNE5V3_2Algorithm.cs` | `ComputeVolTargetScale()` |
| 换手率约束 | `AShareBarraCNE5V3_2Algorithm.cs` | `ApplyTurnoverConstraint()` |
| 风控退出 | `AShareBarraCNE5V3_2Algorithm.cs` | `ApplyRiskManagementExits()` |
| 执行调仓 | `AShareBarraCNE5V3_2Algorithm.cs` | `ExecuteRebalance()` |
| Monte Carlo | `AShareBarraCNE5V3_2Algorithm.cs` | `RunMonteCarloSimulation()` |
