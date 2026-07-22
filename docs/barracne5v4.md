# Barra CNE5 V4 — LEAN原生五层架构策略

## 概述

V4是Barra CNE5多因子策略的LEAN原生框架实现。与V3.2的自定义逻辑不同，V4严格遵循LEAN的五层框架架构：Universe Selection → Alpha → Portfolio Construction → Risk Management → Execution。

**核心原则**：所有量化策略必须使用LEAN原生框架接口，不自定义选择、评分、权重、风控和执行逻辑。

## 五层架构

### 第一层：Universe Selection — ManualUniverseSelectionModel

```
实现方式：ManualUniverseSelectionModel
输入：因子CSV目录扫描得到的eligible symbols
输出：Symbol集合
```

- 从因子CSV目录（`alternative/barra-cne5v2-factors/{market}/daily/`）扫描所有有效ticker
- 每个ticker通过 `Symbol.Create(ticker, SecurityType.Equity, market)` 创建
- market根据股票代码首位数字推断：6/9→SSE，0/3→SZSE
- 所有eligible symbols传入 `ManualUniverseSelectionModel(eligibleSymbols)`
- **为什么不用FundamentalUniverseSelectionModel**：Market.China在LEAN中没有注册交易所小时数据，会抛出"Unable to locate exchange hours"异常

### 第二层：Alpha Model — AShareBarraCNE5V4AlphaModel

```
实现方式：IAlphaModel
输入：Slice数据（因子数据 + 价格数据）
输出：Insight集合（每只选中股票一个Insight）
```

Alpha Model是V4的核心，承担了因子评分、市场状态检测、IC/IR调整、分层选股四大功能。

#### 2.1 因子评分

- 15个Barra CNE5因子：beta, momentum, size, earnyld, resvol, growth, btop, leverage, liquidity, nlsize, moneyflow, quality, northbound, margin, chipcost
- 默认因子权重：
  ```csharp
  beta=0.06, momentum=0.10, size=0.06, earnyld=0.10, resvol=0.06,
  growth=0.08, btop=0.10, leverage=0.04, liquidity=0.04, nlsize=0.04,
  moneyflow=0.08, quality=0.08, northbound=0.06, margin=0.05, chipcost=0.05
  ```
- 每个因子值先做截面z-score标准化，再按权重加权求和得到复合得分

#### 2.2 市场状态检测（Regime Switching）

- 计算20日年化波动率
- 三状态划分：
  - `low_vol`：波动率 < 15% → 进攻权重（beta/momentum/size权重 +50%）
  - `mid_vol`：15% ≤ 波动率 < 25% → 默认权重
  - `high_vol`：波动率 ≥ 25% → 防守权重（btop/quality/leverage权重 +50%）
- 每个rebalance周期检测一次

#### 2.3 IC/IR权重调整

- 计算Spearman Rank IC：因子值排名与下期收益排名的相关系数
- 滚动计算IC的均值（IC）和标准差，得到信息比率 IR = IC_mean / IC_std
- IR权重调整因子 = clip(IR / 2.0, 0.2, 2.0)
- 最终权重 = 基础权重 × regime调整 × IR调整

#### 2.4 分层选股（Stratified Selection）

- 使用申万31行业分类进行分层
- 每个行业按因子得分排序，选前N只
- 保证行业分散化，避免集中在少数行业
- 最终选出Top-N股票（默认30只）

#### 2.5 Insight生成

```csharp
Insight.Price(symbol, period, direction, magnitude, confidence, sourceModel, weight: weight)
```

| 字段 | 含义 | 计算方式 |
|------|------|----------|
| Symbol | 股票标识 | 选中的股票 |
| Period | Insight有效期 | 30天（月度rebalance） |
| Direction | 方向 | Up（score > 0）或 Down（score < 0） |
| Magnitude | 预期收益幅度 | abs(composite_score) |
| Confidence | 置信度 | IC-based，0-1 |
| SourceModel | 来源标识 | "BarraCNE5V4" |
| Weight | 权重 | magnitude × confidence |

- SourceModel统一为"BarraCNE5V4"→ 单一BL视图
- 只对Top-N股票生成Insight，无Insight的股票自动清仓（框架行为）

### 第三层：Portfolio Construction — InsightWeightingPortfolioConstructionModel

```
实现方式：InsightWeightingPortfolioConstructionModel
输入：Insight集合
输出：IPortfolioTarget[]（目标持仓权重）
```

- **为什么不用BlackLittermanOptimizationPortfolioConstructionModel**：在运行时，BL优化器返回的权重数量与symbol数量不匹配，导致IndexOutOfRangeException。这是LEAN框架BL实现的已知问题。
- InsightWeightingPortfolioConstructionModel直接使用Insight.Weight作为目标权重
- 参数：`rebalance = 30天`，`PortfolioBias = Long`
- 权重归一化：所有Insight.Weight归一化到100%总权重

### 第四层：Risk Management — AShareBarraCNE5V4RiskManagementModel

```
实现方式：IRiskManagementModel
输入：当前持仓 + 目标权重
输出：调整后的IPortfolioTarget[]
```

6个风控子模块：

#### 4.1 止损（Stop Loss）

- 价格 ≤ 均价 × (1 - 12%) → 清仓
- 防止单只股票大幅亏损

#### 4.2 移动止损（Trailing Stop）

- 激活条件：盈利 ≥ 18%
- 止损线：从最高价回撤 ≥ 8% → 清仓
- 跟踪每只股票的最高价

#### 4.3 冷却期（Cooldown）

- 清仓后N天内不再买入同一股票（默认5天）
- 防止反复交易同一股票

#### 4.4 单股最大权重（Max Single Weight）

- 单只股票权重 > 10% → 降权至10%
- 保证持仓分散化

#### 4.5 Sharpe暴露度缩放（Sharpe-based Exposure Scaling）

- 滚动计算近期Sharpe比率
- Sharpe > 1.0 → 放大暴露度（最高1.5x）
- Sharpe < 0.5 → 缩小暴露度（最低0.3x）
- 在ManageRisk()中直接缩放所有portfolio targets

#### 4.6 波动率目标（Vol Targeting）

- 计算近期组合波动率
- 如果波动率 > 目标波动率（默认15%）→ 按比例缩减持仓
- 如果波动率 < 目标波动率 → 可适度放大

### 第五层：Execution — ImmediateExecutionModel

```
实现方式：ImmediateExecutionModel
输入：IPortfolioTarget[]
输出：MarketOrder
```

- 收到目标权重后立即执行市价单
- 无滑点模型或延迟执行
- 简单直接，适合A股T+1环境

## 数据流

```
因子CSV文件
    ↓ (AShareBarraCNE5V2FactorData自定义数据类型)
Slice数据
    ↓
AlphaModel.Update()
    ├── 读取因子数据
    ├── 计算截面z-score
    ├── 应用Regime权重
    ├── 应用IC/IR调整
    ├── 计算复合得分
    ├── 分层选股（申万31行业）
    └── 生成Insight集合
    ↓
InsightWeightingPortfolioConstructionModel
    ├── 接收Insight集合
    ├── 按Weight归一化
    └── 生成IPortfolioTarget[]
    ↓
RiskManagementModel.ManageRisk()
    ├── 止损检查
    ├── 移动止损检查
    ├── 冷却期检查
    ├── 单股最大权重检查
    ├── Sharpe暴露度缩放
    ├── 波动率目标调整
    └── 返回调整后的IPortfolioTarget[]
    ↓
ImmediateExecutionModel
    └── 执行市价单
```

## A股特殊处理

### T+1约束

- `AShareT1PortfolioModel`：当日买入的股票次日才能卖出
- `AShareT1Holding`：持仓模型遵守T+1规则
- 每个Security都设置这两个模型

### 无风险利率

- `ChinaInterestRateProvider`：使用SHIBOR 1Y作为无风险利率
- 从 `Data/alternative/interest-rate/usa/interest-rate.csv` 读取

### 交易费用

- A股佣金模型：买入0.03%，卖出0.03% + 印花税0.1%
- 最低佣金5元

## 文件清单

| 文件 | 用途 |
|------|------|
| `Algorithm.CSharp/AShareBarraCNE5V4Algorithm.cs` | 主算法类，组装五层框架 |
| `Algorithm.CSharp/AShareBarraCNE5V4AlphaModel.cs` | Alpha模型：因子评分、Regime、IC/IR、分层选股 |
| `Algorithm.CSharp/AShareBarraCNE5V4RiskManagementModel.cs` | 风控模型：6个子模块 |
| `Algorithm.CSharp/AShareBarraCNE5V4UniverseSelectionModel.cs` | Universe选择模型（备用，当前使用ManualUniverseSelectionModel） |
| `Launcher/config/config-barra-cne5v4-backtest.json` | 回测配置 |
| `Launcher/config/config-barra-cne5v4-live-paper.json` | 实盘模拟配置 |
| `Scripts/barra_cne5v4_live_bridge.py` | InfluxDB桥接脚本，写入barra_cne5v4_*指标 |
| `Scripts/generate_simulated_cne5_factors.py` | 模拟因子数据生成器 |
| `monitoring/grafana/dashboards/lean/barra-cne5v4-backtest.json` | V4回测Grafana看板 |
| `monitoring/grafana/dashboards/lean/barra-cne5v4-live-paper.json` | V4实盘模拟Grafana看板 |

## 因子数据准备

### 使用真实tushare数据

```bash
python3 Scripts/barra_cne5v2_factor_bridge.py \
  --output Data/alternative/barra-cne5v2-factors \
  --start-date 20200101 \
  --end-date 20251231
```

### 使用模拟数据（测试用）

```bash
python3 Scripts/generate_simulated_cne5_factors.py \
  --output Data/alternative/barra-cne5v2-factors \
  --stocks 300 \
  --seed 42 \
  --sse-tickers Data/equity/sse/daily/ticker_list.txt \
  --szse-tickers Data/equity/szse/daily/ticker_list.txt
```

## 运行命令

### 回测

```bash
cd /home/project/hope/Lean
dotnet build QuantConnect.Lean.sln
cd Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll \
  --config ../../../Launcher/config/config-barra-cne5v4-backtest.json
```

### 实盘模拟

```bash
cd /home/project/hope/Lean/Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll \
  --config ../../../Launcher/config/config-barra-cne5v4-live-paper.json
```

### 启动桥接脚本

```bash
python3 Scripts/barra_cne5v4_live_bridge.py --once
# 或持续运行
python3 Scripts/barra_cne5v4_live_bridge.py --interval 300
```

## InfluxDB指标

V4桥接脚本写入以下measurement：

| Measurement | 内容 |
|-------------|------|
| `barra_cne5v4_daily` | 净值、回撤、收益率、暴露度、持仓数 |
| `barra_cne5v4_factor_exposure` | 15个因子的当前暴露度 |
| `barra_cne5v4_holding_detail` | 每只持仓股票的详情 |
| `barra_cne5v4_trades` | 最近100笔成交 |
| `barra_cne5v4_decay` | Sharpe衰减、收益衰减、回撤监控 |

## 与V3.2的对比

| 维度 | V3.2 | V4 |
|------|------|-----|
| 架构 | 自定义逻辑 | LEAN五层框架 |
| 选股 | SelectPortfolioStratified() | AlphaModel.Update() → Insight |
| 评分 | ComputeScores() | AlphaModel内部 |
| 组合构建 | BuildBlackLittermanKellyWeights()（标量贝叶斯收缩） | InsightWeightingPortfolioConstructionModel |
| 风控 | ApplyRiskManagementExits() + cooldown | RiskManagementModel.ManageRisk() |
| 暴露度 | ComputeSharpeBasedExposureScale() + ComputeVolTargetScale() | RiskManagementModel内部 |
| 执行 | ExecuteRebalance() → SetHoldings() | ImmediateExecutionModel |
| BL模型 | 伪BL（无协方差矩阵） | 真BL（因框架bug暂用InsightWeighting） |

## 独立性

V4完全独立于V2/V3/V3.2：
- 独立的C#算法文件（V4后缀）
- 独立的配置文件
- 独立的InfluxDB measurement（barra_cne5v4_*）
- 独立的Grafana看板
- 独立的桥接脚本
- 不修改任何现有功能或看板
