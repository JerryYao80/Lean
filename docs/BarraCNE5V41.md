# BarraCNE5 V4.1 — 五层架构改进分析

## 概述

BarraCNE5V4.1 是 V4 的修复版本。V4 采用 LEAN 原生五层架构（Universe → Alpha → Portfolio Construction → Risk Management → Execution），但存在三层设计缺陷导致 Sharpe = -0.045。V4.1 修复了 Alpha Model、Portfolio Construction、Risk Management 三层，同时解决了上游 LEAN 框架中 BlackLitterman 模型的 IndexOutOfRangeException 崩溃问题。

回测区间: 2018-04-02 至 2025-12-31，CSI300 宇宙，初始资金 100 万人民币。

## V4 原始问题诊断

### 问题 1: Portfolio Construction — InsightWeighting 无风险优化

V4 使用 `InsightWeightingPortfolioConstructionModel`，按 Insight.Weight 比例分配资金：
- 无协方差矩阵、无风险预算、无 Sharpe 最优化
- BL 参数（delta=2.2, tau=0.05）在 config 中存在但从未接入
- 比例权重与等权几乎无差异（weight ∝ score，score 方差极小）

### 问题 2: Alpha Model — Down Insight 污染 + Magnitude 语义错误

- Down insight 在 Long-only 策略中是死重：BL 的 Long bias 会将其权重清零，但 TryGetViews 在构建 P 矩阵时将 |Magnitude| 累加到分母，放大 Q 向量
- Magnitude 值为 0-1 区间，但 BL 将其解读为 0-100% 预期收益率，严重扭曲 view 向量
- 比例权重（weight ∝ score）与等权几乎无差异

### 问题 3: Risk Management — Sharpe Exposure Scaling 导致持续低配

- 自定义 Risk Model 的 Sharpe-based exposure scaling 将目标暴露控制在 ~70%
- Stop-loss 在 100 次再平衡中从未触发
- Vol target scaling 上限为 1.0，只能减少暴露不能增加
- 组合持续 under-invested，拖累收益

### 问题 4: LEAN 框架 Bug — BL 模型 IndexOutOfRangeException

LEAN 原生 `BlackLittermanOptimizationPortfolioConstructionModel` 在 `DetermineTargetPercent` 中存在维度不匹配 Bug：

```
symbols = activeInsights.Select(x => x.Symbol).Distinct().ToList();  // 25 个 symbol
returns = _symbolDataDict.FormReturnsMatrix(symbols);                 // 可能少于 25 列
W = _optimizer.Optimize(returns, Π, Σ);                              // W.Length < 25
var weight = W[sidx];  // CRASH: IndexOutOfRangeException
```

根因: `FormReturnsMatrix` 对 symbol 做 inner join，缺少历史数据的 symbol 被过滤掉，导致 returns 矩阵列数 < symbols.Count。优化器输出 W 的长度 = returns 列数 < symbols.Count，遍历时越界。

此 Bug 存在于 BL、MeanVariance、RiskParity 三个 LEAN 原生优化模型中。

---

## V4.1 改进方案

### Layer 2: Alpha Model 改进

文件: `Algorithm.CSharp/AShareBarraCNE5V4AlphaModel.cs`

#### 改进 2A — 仅发出 Up Insight

```csharp
// Skip stocks with negative normalized scores (dead weight for long-only)
var longCandidates = selected.Where(p => p.Value > 0m).ToList();
```

Long-only 策略中 Down insight 被 BL 清零但不参与优化。移除后 P 矩阵构建更干净。

#### 改进 2B — 等权 + Magnitude 作为预期收益率

```csharp
var targetAnnualReturn = 0.15;   // 最优股票映射到 15% 预期年化收益
var equalWeight = 1.0 / longCandidates.Count;
var magnitude = (double)(score / maxScore) * targetAnnualReturn;
```

- Weight = 1/N 等权（BL 不使用 Weight 构建 view）
- Magnitude = 按分数排名缩放到 [0, 15%] 作为预期收益率（BL 将 Magnitude 解读为 view return）

#### 改进 3C — 基于 |IC| 的 Confidence 评分

```csharp
// 替换 IR-based: 0.5 + 0.45 * avgIR（IR ≈ 0 时固定 0.55）
// 使用 |IC|: 0.5 + 0.3 * avgAbsIC
var avgAbsIC = absICs.Average();
return Math.Min(0.95, Math.Max(0.3, 0.5 + 0.3 * (double)avgAbsIC));
```

IR = IC_mean / IC_std，当 IC 波动大时 IR ≈ 0 导致 confidence 恒定。改用 |IC| 均值更直接反映因子预测能力。

### Layer 3: Portfolio Construction — FixedBlackLitterman

文件: `Algorithm.Framework/Portfolio/FixedBlackLittermanPortfolioConstructionModel.cs`

创建 `FixedBlackLittermanPortfolioConstructionModel` 修复 LEAN 上游 Bug：

1. **过滤无数据 Symbol**: 只将 `_symbolDataDict` 中有足够历史收益的 symbol 传入优化器
2. **过滤 P 矩阵列**: 移除无数据 symbol 对应的列，保持 P 与 returns 维度一致
3. **空矩阵保护**: 当 returns 为空时直接返回零权重，不调用优化器
4. **优化器输出校验**: 若 W.Length ≠ validSymbols.Count，回退到等权

参数配置:
```csharp
new FixedBlackLittermanPortfolioConstructionModel(
    TimeSpan.FromDays(30),     // 月度再平衡
    PortfolioBias.Long,        // 只做多
    lookback: 1, period: 63,   // 63 天历史窗口
    riskFreeRate: 0.03,        // SHIBOR 1Y ≈ 3%
    delta: 2.2,                // 风险厌恶系数
    tau: 0.05                  // View 不确定性
);
```

BL 工作流程:
1. `OnSecuritiesChanged` 加载 63 天历史价格，通过 ROC(1) 计算日收益率
2. `GetEquilibriumReturns` 计算均衡收益 Π = δΣw（CAPM 隐含超额收益）
3. `TryGetViews` 从 Insight.Magnitude 构建 P 矩阵和 Q 向量
4. `ApplyBlackLittermanMasterFormula` 混合均衡与 view: Π_posterior = Π + A(Q - PΠ)
5. `MaximumSharpeRatioPortfolioOptimizer` 在 [0, 1] 约束下最大化 Sharpe ratio

### Layer 4: Risk Management — LEAN 原生 Composite 模型

```csharp
new CompositeRiskManagementModel(
    new TrailingStopRiskManagementModel(0.15m),                   // 个股 15% 追踪止损
    new MaximumDrawdownPercentPerSecurity(0.10m),                  // 个股 10% 最大亏损
    new MaximumDrawdownPercentPortfolio(0.20m, isTrailing: true)   // 组合 20% 追踪回撤熔断
);
```

移除的自定义组件及其问题:
| 组件 | 问题 |
|------|------|
| Sharpe exposure scaling | 组合持续 ~70% 仓位，BL 已内置风险-收益权衡 |
| Vol target scaling | 上限 1.0 只能减不能增，BL 协方差矩阵自然调整波动 |
| Cooldown period | 止损后阻止重新入场，即使 BL 想分配资金 |
| Max single weight | BL 优化器 [0,1] 约束 + Sharpe 目标自然分散 |

Layer 1 (Universe) 和 Layer 5 (Execution) 无变更。

---

## 回测结果

### V4 vs V4.1 核心指标对比

| 指标 | V4 (InsightWeighting) | V4.1 (FixedBL) | 变化 |
|------|----------------------|----------------|------|
| **Net Profit** | +24.36% | **+54.05%** | +29.69pp |
| **Sharpe Ratio** | -0.045 | **+0.166** | +0.211 |
| **Sortino Ratio** | — | **+0.199** | — |
| **CAGR** | ~3.1% | **5.73%** | +2.6pp |
| **Max Drawdown** | ~15% | 51.3% | -36pp |
| **Drawdown Recovery** | — | 300 天 | — |
| **Win Rate** | ~43% | **54%** | +11pp |
| **Loss Rate** | ~57% | 46% | -11pp |
| **Profit-Loss Ratio** | — | 0.95 | — |
| **Annual Std Dev** | — | 17% | — |
| **Expectancy** | — | 0.047 | — |
| **Portfolio Turnover** | — | 8.31% | — |
| **Total Fees** | — | ¥175,390 | — |
| **Total Orders** | ~350 | 8,415 | +23x |
| **End Equity** | ¥1,240,000 | **¥1,540,478** | +¥300k |

### 运行统计

| 维度 | 值 |
|------|-----|
| 回测区间 | 2018-04-02 → 2025-12-31 |
| 再平衡次数 | 94 次 |
| 跳过再平衡（spread 不足） | 40 次 |
| Regime 切换 | 53 次 |
| 平均 Confidence | 0.55 - 0.65 |
| Insight 总数 | 2,350 (全部 Up) |
| 成交订单 | 8,163 笔 |
| 买入力不足错误 | 186 次 |
| 卖出超持仓错误 | 55 次 |

### 逐年 Regime 分布

Alpha Model 检测到三种波动率 Regime 并切换因子权重:
- **low_vol** (< 15% 年化波动): 增长、动量因子权重提升
- **mid_vol** (15-25%): 基准权重
- **high_vol** (> 25%): 质量、价值因子权重提升，动量降低

典型 Regime 事件:
- 2018-11: mid → high（A 股大跌）
- 2020-03: mid → high（疫情冲击）
- 2022 全年: 频繁 high_vol
- 2024-2025: 多次 low_vol ↔ mid_vol 切换

---

## 已知问题

### 1. 最大回撤 51.3% 过高

Composite Risk Model 的 20% 组合追踪回撤限制未能有效约束。可能原因:
- 组合级别回撤计算基于 `Portfolio.TotalPortfolioValue` 峰值，在 BL 每月大规模调仓后峰值重置
- 追踪止损 15% 和个股止损 10% 可能被 BL 下个月的再平衡覆盖（先止损再重建仓位）
- BL 优化器不知道止损约束，可能反复分配高波动资产

### 2. 买入力不足错误

186 次 "Insufficient buying power" 和 55 次 "Sell quantity exceeds available" 错误:
- BL 计算的目标权重是连续值（如 4.3%），但 A 股必须 100 股整数倍
- Execution Model 四舍五入到 100 股后，实际仓位与目标偏差
- T+1 结算制度下，卖出资金次日才可用，但 BL 假设即时再投资

### 3. 手续费消耗高

¥175,390 手续费占净利润的 ~32%:
- 月度再平衡每次调仓 ~25 只股票，双向交易 50 笔
- 年化换手率 8.31%，对应每年约 100 次调仓
- 万 3 费率 × 双向 × 高频调仓 = 显著摩擦成本

### 4. Confidence 评分未能有效区分

Confidence 范围窄（0.55-0.65），大部分时间固定在 0.55:
- 改用 |IC| 均值后，大部分因子的 |IC| 在 0.1-0.2 区间
- 映射公式 0.5 + 0.3 × avgAbsIC → 0.53-0.56，区分度低
- BL 的 tau 参数（view 不确定性）应吸收 confidence 信息，但当前实现未联动

---

## 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `Algorithm.CSharp/AShareBarraCNE5V4AlphaModel.cs` | 修改 | Up-only insight, 等权+Magnitude, |IC| confidence |
| `Algorithm.CSharp/AShareBarraCNE5V4Algorithm.cs` | 修改 | FixedBL 替换 InsightWeighting, Composite 替换自定义 Risk |
| `Algorithm.Framework/Portfolio/FixedBlackLittermanPortfolioConstructionModel.cs` | 新增 | 修复 LEAN BL 维度不匹配 Bug |
| `Launcher/config/config-barra-cne5v4-backtest.json` | 不变 | 参数与 V4 完全相同，仅算法层变更 |

Layer 1 (Universe: ManualUniverseSelectionModel) 和 Layer 5 (Execution: AShareLotSizeExecutionModel) 无变更。
