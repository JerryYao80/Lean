# LEAN 量化策略框架五层架构

LEAN 采用五层框架（Framework）架构组织量化策略，数据流如下：

```
Universe Selection → Alpha Model → Portfolio Construction → Risk Management → Execution
   (选股宇宙)         (信号生成)       (组合构建)            (风控管理)        (执行下单)
```

每一层通过接口（`I*Model`）解耦，可独立替换和组合。仅 V4 策略完整使用五层框架；V1~V3.2 使用 SignalModel 手动下单，不走框架流水线。

---

## 第一层：Universe Selection Model（选股宇宙）

**接口**: `IUniverseSelectionModel`（`Algorithm/Selection/`）
**职责**: 决定策略关注哪些股票，定期更新股票池

### 基础实现（Algorithm/Selection/）

| 类名 | 功能 |
|------|------|
| `ManualUniverseSelectionModel` | 手动指定固定股票列表，不随时间变化 |
| `CustomUniverseSelectionModel` | 自定义函数式选股，传入 Func 选择器 |
| `CompositeUniverseSelectionModel` | 组合多个选股模型，取并集 |
| `OptionChainedUniverseSelectionModel` | 从标的选择期权链，用于期权策略 |
| `NullUniverseSelectionModel` | 空实现，不选任何股票 |

### 框架实现（Algorithm.Framework/Selection/）

| 类名 | 功能 |
|------|------|
| `FundamentalUniverseSelectionModel` | 基本面选股基类，支持 Coarse/Fine 两级筛选 |
| `CoarseFundamentalUniverseSelectionModel` | 粗粒度基本面选股（成交量、价格等快速指标） |
| `FineFundamentalUniverseSelectionModel` | 细粒度基本面选股（PE/PB/行业等深度指标） |
| `QC500UniverseSelectionModel` | QC500 宇宙，按成交额选前 500 只美股 |
| `EmaCrossUniverseSelectionModel` | 按 EMA 交叉信号强度选股 |
| `ETFConstituentsUniverseSelectionModel` | 选 ETF 成分股作为宇宙 |
| `ScheduledUniverseSelectionModel` | 按时间规则（日/周/月）定期触发选股 |
| `FutureUniverseSelectionModel` | 期货合约链选股 |
| `OpenInterestFutureUniverseSelectionModel` | 按持仓量筛选期货合约 |
| `OptionUniverseSelectionModel` | 期权合约链选股 |
| `InceptionDateUniverseSelectionModel` | 按上市日期选股，过滤新股 |

### 预置 ETF 宇宙

| 类名 | 功能 |
|------|------|
| `EnergyETFUniverse` | 能源板块 ETF 宇宙 |
| `MetalsETFUniverse` | 金属板块 ETF 宇宙 |
| `LiquidETFUniverse` | 高流动性 ETF 宇宙 |
| `TechnologyETFUniverse` | 科技板块 ETF 宇宙 |
| `VolatilityETFUniverse` | 波动率 ETF 宇宙 |
| `SP500SectorsETFUniverse` | S&P500 行业 ETF 宇宙 |
| `USTreasuriesETFUniverse` | 美国国债 ETF 宇宙 |

### A 股专用实现

| 类名 | 功能 |
|------|------|
| `AShareBarraCNE5V4UniverseSelectionModel` | Barra CNE5 V4 选股模型，继承 FundamentalUniverseSelectionModel，从有因子数据的 A 股中筛选正价格股票，使用 China 市场 |

---

## 第二层：Alpha Model（信号生成）

**接口**: `IAlphaModel`（`Algorithm/Alphas/`）
**职责**: 对宇宙中的每只股票生成交易信号（Insight），包含方向（Up/Down/Flat）、强度、置信度和有效期

### 基础实现（Algorithm/Alphas/）

| 类名 | 功能 |
|------|------|
| `CompositeAlphaModel` | 组合多个 Alpha 模型，汇总各自的 Insight |
| `NullAlphaModel` | 空实现，不生成任何信号 |

### 框架实现（Algorithm.Framework/Alphas/）

| 类名 | 功能 |
|------|------|
| `ConstantAlphaModel` | 常量信号，始终输出固定方向/强度的 Insight |
| `EmaCrossAlphaModel` | EMA 均线交叉信号，金叉看多/死叉看空 |
| `HistoricalReturnsAlphaModel` | 历史收益率信号，按过去收益方向生成 Insight |
| `MacdAlphaModel` | MACD 信号，DIF/DEA 交叉产生多空信号 |
| `RsiAlphaModel` | RSI 信号，低于 30 看多/高于 70 看空 |
| `BasePairsTradingAlphaModel` | 配对交易信号基类 |
| `PearsonCorrelationPairsTradingAlphaModel` | 基于 Pearson 相关系数的配对交易，选最高相关性配对，多空交替 |

### A 股 Barra 因子信号模型（SignalModel，非 IAlphaModel）

V1~V3.2 的信号模型是静态辅助类（SignalModel），不走框架 Alpha 流水线，由算法直接调用生成评分后手动下单。

| 类名 | 版本 | 因子数 | 功能 |
|------|------|--------|------|
| `AShareBarraCNE5SignalModel` | V1 | 10 | 10 因子截面评分：Beta、动量、规模、盈利收益率、残差波动率、成长性、账面市值比、杠杆、流动性、非线性规模。支持等权/市值权/BL-Kelly 权 |
| `AShareBarraCNE5V2SignalModel` | V2 | 15 | V1 的 10 因子 + 资金流、质量、北向、保证金、筹码成本。支持 BL/Kelly 权重 |
| `AShareBarraCNE5V2_1SignalModel` | V2.1 | 15 | 同 V2 的 15 因子，调整权重：提高动量和质量权重，降低规模惩罚 |
| `AShareBarraCNE5V3SignalModel` | V3 | 15 | V2.1 基础上增加：市场状态依赖权重切换、IC/IR 因子校准、分层行业选股、换手约束、波动目标 |
| `AShareBarraCNE5V3_2SignalModel` | V3.2 | 15 | 与 V3 功能相同，独立实现副本 |

### A 股专用信号模型（SignalModel，非 IAlphaModel）

| 类名 | 功能 |
|------|------|
| `AShareEtfT0FeatureSignalModel` | ETF T+0 日内策略信号，基于 NAV 溢价 Z-Score 特征生成交易信号 |
| `AShareImpliedVolatilitySignalModel` | 隐含波动率信号，计算偏斜（Skew）、IV 期限结构（IVTS）、IV 水平三个信号，输出市场状态分类（恐惧/中性/贪婪） |
| `AShareLlmQuantSignalModel` | LLM 量化信号，结合动量、价值（P/B、P/S）、股息率、资金流、大单流、低波动、短期反转等多因子 |
| `AShareMultiFamilySignalModel` | 多家族信号，融合动量/反转、价值/质量、资金流、盈余惊喜、筹码成本、ETF 溢价、行业轮动七大因子家族 |
| `AShareT1MeanReversionSignalModel` | T+1 均值回归信号，计算滚动窗口内收盘价 Z-Score，触发均值回归交易 |

### A 股框架 Alpha 实现

| 类名 | 功能 |
|------|------|
| `AShareBarraCNE5V4AlphaModel` | **V4 唯一正式 IAlphaModel 实现**。15 因子 Barra CNE5 评分模型：市场状态依赖权重切换（低/中/高波动）、IC/IR 因子校准、分层行业选股、评分归一化到 [-1,1]、输出 long-only Insight。支持可配置调仓频率（周/双周/半月/月） |

---

## 第三层：Portfolio Construction Model（组合构建）

**接口**: `IPortfolioConstructionModel`（`Algorithm/Portfolio/`）
**职责**: 将 Alpha 信号转化为具体的目标持仓权重（PortfolioTarget），决定每只股票分配多少资金

### 基础实现（Algorithm/Portfolio/）

| 类名 | 功能 |
|------|------|
| `NullPortfolioConstructionModel` | 空实现，不生成任何目标持仓 |

### 框架实现（Algorithm.Framework/Portfolio/）

| 类名 | 功能 |
|------|------|
| `EqualWeightingPortfolioConstructionModel` | 等权分配，1/N 权重，按 Insight 方向决定多空 |
| `InsightWeightingPortfolioConstructionModel` | 按 Insight.Weight 加权分配（继承等权模型） |
| `ConfidenceWeightedPortfolioConstructionModel` | 按 Insight.Confidence 置信度加权分配（继承 Insight 权重模型） |
| `SectorWeightingPortfolioConstructionModel` | 按行业模板代码加权分配（继承等权模型） |
| `AccumulativeInsightPortfolioConstructionModel` | 累积信号建仓，每个 Insight 分配固定比例（默认 3%），逐步累积仓位 |
| `BlackLittermanOptimizationPortfolioConstructionModel` | Black-Litterman 优化，将市场均衡收益与投资者观点融合，输出优化权重 |
| `FixedBlackLittermanPortfolioConstructionModel` | 修正版 BL 模型，处理收益矩阵与股票列表维度不匹配问题。**V4 策略使用（delta=2.2, tau=0.05）** |
| `MeanVarianceOptimizationPortfolioConstructionModel` | 均值-方差优化，基于 MPT 现代组合理论求解有效前沿 |
| `RiskParityPortfolioConstructionModel` | 风险平价，使每只股票的风险贡献相等 |
| `MeanReversionPortfolioConstructionModel` | OLMAR 均值回归组合构建，在线移动平均反转策略 |
| `AlphaStreamsPortfolioConstructionModel` | Alpha Streams 平台专用组合构建 |

### 优化器

| 类名 | 功能 |
|------|------|
| `MaximumSharpeRatioPortfolioOptimizer` | 最大夏普比优化器 |
| `MinimumVariancePortfolioOptimizer` | 最小方差优化器 |
| `UnconstrainedMeanVariancePortfolioOptimizer` | 无约束均值方差优化器 |
| `RiskParityPortfolioOptimizer` | 风险平价优化器 |

---

## 第四层：Risk Management Model（风控管理）

**接口**: `IRiskManagementModel`（`Algorithm/Risk/`）
**职责**: 审核和调整组合构建层输出的目标持仓，实施止损、止盈、回撤控制、暴露度限制等风控规则

### 基础实现（Algorithm/Risk/）

| 类名 | 功能 |
|------|------|
| `CompositeRiskManagementModel` | 组合多个风控模型，依次应用各模型规则。**V4 策略使用** |
| `NullRiskManagementModel` | 空实现，不做任何风控调整 |

### 框架实现（Algorithm.Framework/Risk/）

| 类名 | 功能 |
|------|------|
| `MaximumDrawdownPercentPerSecurity` | 单股最大回撤控制，当个股回撤超过阈值时清仓。**V4 使用 10%** |
| `MaximumDrawdownPercentPortfolio` | 组合最大回撤控制，当组合回撤超过阈值时整体降仓。**V4 使用 20% trailing** |
| `TrailingStopRiskManagementModel` | 移动止损，从最高未实现盈利回撤超过阈值时平仓。**V4 使用 15%** |
| `MaximumUnrealizedProfitPercentPerSecurity` | 单股最大未实现盈利控制，达到目标后止盈 |
| `MaximumSectorExposureRiskManagementModel` | 行业暴露度限制，控制单一行业持仓占比 |

### A 股专用实现

| 类名 | 功能 |
|------|------|
| `AShareBarraCNE5V4RiskManagementModel` | V4 自定义风控模型（独立实现，V4 实际使用 Composite 组合框架模型）。包含：止损（12%）、盈利激活移动止损（18% 激活，8% 追踪）、冷却期（5 天）、单股最大权重约束（10%）、Sharpe 暴露度缩放、波动目标 |

---

## 第五层：Execution Model（执行下单）

**接口**: `IExecutionModel`（`Algorithm/Execution/`）
**职责**: 将风控调整后的目标持仓转化为实际订单，决定下单时机和方式

### 基础实现（Algorithm/Execution/）

| 类名 | 功能 |
|------|------|
| `ImmediateExecutionModel` | 立即执行，直接提交市价单达到目标持仓 |
| `NullExecutionModel` | 空实现，不下单 |

### 框架实现（Algorithm.Framework/Execution/）

| 类名 | 功能 |
|------|------|
| `SpreadExecutionModel` | 价差执行，仅在买卖价差可接受时下单（需日内数据） |
| `StandardDeviationExecutionModel` | 标准差执行，价格偏离均值 N 个标准差时在有利方向下单 |
| `VolumeWeightedAveragePriceExecutionModel` | VWAP 执行，价格优于成交量加权平均价时下单 |

### A 股专用实现

| 类名 | 功能 |
|------|------|
| `AShareLotSizeExecutionModel` | A 股整手执行模型，将订单数量向下取整到 100 股的整数倍，防止"A 股订单数量必须为 100 的整数倍"报错。**V4 策略使用** |

---

## 各策略版本使用的模型组合

| 策略 | 框架 | Universe | Alpha/Signal | Portfolio | Risk | Execution |
|------|------|----------|-------------|----------|------|-----------|
| V1 | 手动 | 手动 AddEquity | `AShareBarraCNE5SignalModel` (10因子) | SetHoldings | 手动止损 | MarketOrder |
| V2 | 手动 | 手动 AddEquity | `AShareBarraCNE5V2SignalModel` (15因子) | SetHoldings | 手动止损 | MarketOrder |
| V2.1 | 手动 | 手动 AddEquity | `AShareBarraCNE5V2_1SignalModel` (15因子,调权) | SetHoldings | 手动止损 | MarketOrder |
| V3 | 手动 | 手动 AddEquity | `AShareBarraCNE5V3SignalModel` (15因子,状态切换) | SetHoldings | 手动止损 | MarketOrder |
| V3.2 | 手动 | 手动 AddEquity | `AShareBarraCNE5V3_2SignalModel` (15因子) | SetHoldings | 手动止损 | MarketOrder |
| **V4** | **五层框架** | **ManualUniverseSelectionModel** | **AShareBarraCNE5V4AlphaModel** (15因子) | **FixedBlackLitterman** (δ=2.2,τ=0.05) | **Composite(TrailingStop15%+DrawdownPerSec10%+DrawdownPort20%)** | **AShareLotSize** |
