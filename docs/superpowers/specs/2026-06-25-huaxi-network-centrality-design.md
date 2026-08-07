# 华西证券·股票网络与网络中心度因子策略 Design

**Source**: 华西证券金工研报《股票网络与网络中心度因子研究》(2021-03-14)
**Strategy ID**: `huaxi-network-centrality-20210314`
**Date**: 2026-06-25

---

## 1. 研报核心思想

将股票市场视为一个网络：每只股票是一个节点，股票间日收益率的强相关性构成连边。在该网络上计算各类**网络中心度**指标，作为横截面选股因子。

**核心假设**: 网络中心度低的股票 → 与市场联动弱 → idiosyncratic 成分高 → 未来预期收益更高。

**original_market**: A 股（原研报即针对 A 股，无需市场映射）

## 2. 确定的参数（用户确认）

| 参数 | 值 |
|------|-----|
| 时间窗口 (lookback) | 252 个交易日 |
| 相关系数类型 | Pearson |
| 阈值方法 | 动态分位数，保留每月相关系数 top 30% 的边 |
| 网络类型 | 无向加权（权重 = 相关系数） |
| 股票池 | CSI300（沪深300成分股） |
| 因子 | 4 个独立信号：Degree / Closeness / Betweenness / Eigenvector |
| 因子合成方式 | 各自独立横截面排序，EqualWeighting PCM 合成 |
| 组合方向 | 纯多头 Q1（做多最低中心度 top 20%） |
| 调仓频率 | 月度 |
| 回测区间 | 2015-01-01 ～ 2025-12-31 |
| 起始资金 | 1,000,000 CNY |
| Q1 持仓数 | top 20%（约 60 只），等权 |

## 3. 网络中心度因子定义

设网络节点集 V = CSI300 成分股，|V| = N。

- **Degree Centrality**: `deg(i) = (Σ_j A_ij) / (N-1)`，其中 A 为邻接矩阵。衡量股票与多少其他股票强联动。
- **Closeness Centrality**: `clo(i) = (N-1) / Σ_j d(i,j)`，d 为最短路径长度（基于 1/相关系数 的距离）。衡量股票在网络中的"可达性"。
- **Betweenness Centrality**: `bet(i) = Σ_{s≠i≠t} (σ_st(i) / σ_st)`，σ_st 为 s→t 最短路径数，σ_st(i) 为经过 i 的数量。衡量股票作为"桥梁"的重要性。
- **Eigenvector Centrality**: 满足 `A·x = λ_max·x` 的解向量第 i 分量。衡量股票连接到"重要"股票的程度。

每个因子按月横截面排序，**低中心度 → 看多**（InsightDirection.Up，取最低 top 20%）。

## 4. LEAN 5-Step Framework 架构

| 步骤 | 模型 | 实现方式 |
|------|------|----------|
| 1. Universe Selection | `IUniverseSelectionModel` | `ManualUniverseSelectionModel`，月度用 CSI300 成分股更新，剔除 ST/停牌 |
| 2. Alpha | `IAlphaModel` | 派生 `NetworkCentralityAlphaModel`（4 个因子独立 Insight） |
| 3. Portfolio Construction | `IPortfolioConstructionModel` | `EqualWeightingPortfolioConstructionModel`（LEAN 内置） |
| 4. Risk Management | `IRiskManagementModel` | `NullRiskManagementModel`（LEAN 内置） |
| 5. Execution | `IExecutionModel` | `ImmediateExecutionModel`（LEAN 内置） |

算法基类: `QCAlgorithm`，在 `Initialize()` 中通过 `SetUniverseSelection/SetAlpha/SetPortfolioConstruction/SetRiskManagement/SetExecution` 装配。

## 5. 因子数据流（离线 Python 准备 + LEAN 内消费）

**因子准备（Python 脚本）**: `Scripts/compute_huaxi_network_centrality.py`
- 输入: tushare `daily` 数据（pct_chg 日收益率）
- 流程: 月末，对过去 252 天构建相关系数矩阵 → top 30% 阈值建网 → networkx 算 4 个中心度
- 输出: `Data/alternative/huaxi-network-centrality/factors.csv`，列为 `[trade_date, ts_code, degree, closeness, betweenness, eigenvector]`

**LEAN 消费（Alpha Model 内）**: 通过自定义数据或预热历史数据读取，月末读取因子文件，为每只股票按各因子横截面排序生成 Insight。

> 决策（打分/排序/选股）完全在 LEAN 算法内执行，Python 仅负责数值计算（相关系数矩阵、networkx 中心度），符合 LEAN-native 原则。

## 6. A 股合规要素

| 维度 | 设置 |
|------|------|
| 标的 | 沪深 A 股（CSI300），纯 ticker（如 `600519`），从 ticker 首位推断 SSE/SZSE |
| 结算 | T+1 |
| 费用 | `AShareStockFeeModel` |
| 成交 | `AShareStockFillModel`（涨跌停、100 股整手） |
| 基准 | `SetBenchmark(_ => 0m)` |
| 基本面 | 不使用 FineFundamental |
| 无风险利率 | SHIBOR 1Y via `ChinaInterestRateProvider` |
| 交易时段 | 9:30–11:30, 13:00–15:00 Asia/Shanghai |
| 数据源 | tushare daily（收益率），无需外部数据 |

## 7. 效果指标（LEAN 原生输出）

所有指标来自 LEAN `InfluxDbResultExporter` 写入 `lean_metric`（Sharpe / Sortino / Total Return / Max Drawdown / Win Rate 等），禁止自计算。

## 8. 文件清单（新增，不修改已有）

- `Scripts/compute_huaxi_network_centrality.py` — 因子计算
- `Data/alternative/huaxi-network-centrality/factors.csv` — 因子输出
- `Algorithm.Python/NetworkCentralityAlphaModel.py` — Alpha 模型
- `Algorithm.Python/HuaxiNetworkCentralityAlgorithm.py` — 算法主类
- `Launcher/config/config-huaxi-network-centrality.json` — 回测配置
- `Launcher/config/config-huaxi-network-centrality-live-paper.json` — live-paper 配置
