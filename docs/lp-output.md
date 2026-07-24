# LEAN Live Paper 输出指南

本文档详细介绍 LEAN 在 Live Paper（模拟实盘）模式下产生的各类输出文件及其含义。

---

## 概述

Lean Engine 在 Live Paper 模式下会持续输出多种类型的结果文件，用于监控策略运行状态、回顾交易历史、分析策略表现等。

**输出位置**：默认在 `Results/` 目录下

---

## 输出文件类型

### 1. 状态文件 (JSON)

#### 主状态文件
- **文件名格式**：`{AlgorithmId}.json`
- **更新频率**：约每 10 分钟
- **内容**：完整的策略状态快照

```json
{
  "charts": { ... },           // 图表数据
  "orders": { ... },           // 订单字典
  "holdings": { ... },         // 当前持仓
  "cashbook": { ... },         // 现金账本
  "statistics": { ... },       // 统计指标
  "runtimeStatistics": { ... }, // 运行时统计
  "orderEvents": [],           // 订单事件
  "serverStatistics": { ... }, // 服务器统计
  "state": { ... }             // 算法状态
}
```

#### 各字段含义

| 字段 | 类型 | 说明 |
|------|------|------|
| `charts` | Dictionary | 策略图表数据（权益曲线、收益、回撤等） |
| `orders` | Dictionary<int, Order> | 所有订单的字典，Key 为订单 ID |
| `holdings` | Dictionary<string, Holding> | 当前持仓，Key 为标的代码 |
| `cashbook` | CashBook | 多币种现金账本 |
| `statistics` | Dictionary | 策略绩效统计（Sharpe、Sortino 等） |
| `runtimeStatistics` | Dictionary | 运行时动态统计 |
| `orderEvents` | List | 订单事件历史 |
| `serverStatistics` | Dictionary | 服务器端统计（内存、CPU 等） |
| `state` | Dictionary | 算法当前状态（运行中、已停止等） |

---

### 2. 图表数据文件

#### 2.1 分钟级图表
- **文件名格式**：`{AlgorithmId}-{Date}_minute.json`
- **采样频率**：1 分钟
- **内容**：当日完整的分钟级权益曲线和绩效数据

#### 2.2 十分钟级图表
- **文件名格式**：`{AlgorithmId}-{Date}_10minute.json`
- **采样频率**：10 分钟
- **内容**：中等分辨率的绩效数据

#### 2.3 秒级图表（高分辨率）
- **文件名格式**：`{AlgorithmId}-{Date}-{Hour}_second_{ChartName}.json`
- **采样频率**：每秒
- **保存时长**：仅保存最近 1 小时的数据
- **内容**：实时高分辨率图表数据

**常见图表类型**：

| 图表名称 | 说明 |
|----------|------|
| `Strategy Equity` | 策略权益曲线 |
| `Benchmark` | 基准收益曲线 |
| `Drawdown` | 回撤曲线 |
| `Exposure` | 持仓暴露度 |
| `Portfolio Turnover` | 组合换手率 |

---

### 3. 订单事件文件

- **文件名格式**：`{AlgorithmId}-{Date}-order-events.json`
- **保存频率**：每日一个文件
- **内容**：当日所有订单事件（JSON 数组）

**每条订单事件包含**：

```json
{
  "id": "algorithmId-orderId-eventId",
  "algorithmId": "策略ID",
  "orderId": 1,
  "orderEventId": 1,
  "symbol": "512760 3E9",
  "symbolValue": "512760",
  "time": 1581317940.0,
  "status": "filled",           // 订单状态
  "fillPrice": 1.987,           // 成交价格
  "fillQuantity": 89200.0,      // 成交数量
  "direction": "buy",           // 买卖方向
  "orderFeeAmount": 6.784,      // 手续费
  "message": "..."              // 附加信息
}
```

**订单状态**：

| 状态 | 说明 |
|------|------|
| `new` | 新建订单 |
| `submitted` | 已提交至券商 |
| `partiallyFilled` | 部分成交 |
| `filled` | 完全成交 |
| `canceled` | 已取消 |
| `cancelPending` | 取消待确认 |
| `updatePending` | 更新待确认 |
| `invalid` | 无效订单 |

---

### 4. 日志文件

- **文件名格式**：`{AlgorithmId}-log.txt`
- **保存频率**：约每 2 分钟增量保存
- **内容**：策略运行过程中的所有日志输出

**日志内容示例**：

```
2025-01-02 00:00:00 Launching analysis for AShareBarraCNE5Algorithm with LEAN Engine v2.5.0.0
2025-01-02 00:00:00 Changing account currency from USD to CNY...
2025-01-02 00:00:00 AShareBarraCNE5Algorithm initialized with 5181 factor subscriptions
2025-01-02 02:00:00 [signal ranking] trade_date=20250102 eligible=0 preview=none
2025-01-02 02:00:00 [kelly] trade_date=20250102 closed_trades=0 win_rate=0.0 %
2025-01-02 02:00:00 [daily] trade_date=20250103 equity=999799.99 cash=479246.99 invested=520553.00
2025-01-02 02:00:00 [portfolio] trade_date=20250103 holdings=26 top=300673.SZ=9.86 %
```

**常见日志标签**：

| 标签 | 说明 |
|------|------|
| `[signal ranking]` | 信号排名结果 |
| `[kelly]` | Kelly 仓位计算 |
| `[daily]` | 每日摘要 |
| `[portfolio]` | 组合状态 |
| `[risk exits]` | 风险退出事件 |
| `rebalance` | 调仓事件 |

---

### 5. 实时推送数据（WebSocket）

LEAN 还会通过 WebSocket 实时推送以下数据到前端界面：

| 数据类型 | 更新频率 | 说明 |
|----------|----------|------|
| Delta Charts | 实时 | 图表增量更新 |
| Holdings | 实时 | 当前持仓 |
| Orders | 实时 | 新订单和订单更新 |
| OrderEvents | 实时 | 订单事件 |
| RuntimeStatistics | 实时 | 运行时统计 |
| ServerStatistics | ~1分钟 | 服务器状态 |

---

## 输出文件命名规范

```
{AlgorithmId}-{Date}[-{Hour}]_{Resolution}[_{ChartName}].json
```

**参数说明**：
- `{AlgorithmId}`：算法唯一标识
- `{Date}`：日期，格式 `yyyy-MM-dd`
- `{Hour}`：小时（仅秒级文件），格式 `HH`
- `{Resolution}`：分辨率 `minute`、`10minute`、`second`
- `{ChartName}`：图表名称（如 `Strategy Equity`、`Benchmark`）

**示例**：
- `MyAlgorithm-2025-01-15_minute.json` - 分钟级数据
- `MyAlgorithm-2025-01-15_10minute.json` - 十分钟级数据
- `MyAlgorithm-2025-01-15-09_second_Strategy Equity.json` - 当日 9 点权益曲线

---

## 数据流处理

### 实时处理流程

```
Algorithm Run
    ↓
[每 2 秒] 采样权益/图表 → 推送 Delta Charts
    ↓
[每 3 秒] 推送 Holdings、Orders、RuntimeStatistics
    ↓
[每 10 分钟] 保存完整状态到 {AlgorithmId}.json
    ↓
[每日] 保存订单事件到 {Date}-order-events.json
    ↓
[每 2 分钟] 增量保存日志到 -log.txt
```

### 数据采样策略

| 时间范围 | 采样分辨率 |
|----------|------------|
| 当日 | 1 分钟、10 分钟、秒级 |
| 历史（>1天） | 降采样至 12 小时 |
| 图表修剪 | 删除 2 天前的数据 |

---

## 使用场景

### 1. 实时监控
- 查看 `{AlgorithmId}.json` 获取最新状态
- 监控 WebSocket 推送的实时数据

### 2. 历史回溯
- 查看 `{Date}_minute.json` 重现当日表现
- 分析 `{Date}-order-events.json` 复盘交易

### 3. 问题诊断
- 查看 `-log.txt` 定位异常
- 分析订单事件排查交易问题

### 4. 绩效分析
- 提取 `charts` 数据绘制权益曲线
- 计算各项风险指标

---

## 总结

| 文件类型 | 用途 | 频率 |
|----------|------|------|
| `{AlgorithmId}.json` | 完整状态快照 | ~10分钟 |
| `{Date}_minute.json` | 分钟级绩效 | 实时 |
| `{Date}_10minute.json` | 十分钟级绩效 | 实时 |
| `{Hour}_second_*.json` | 秒级高分辨率 | 实时 |
| `{Date}-order-events.json` | 订单事件 | 每日 |
| `-log.txt` | 运行日志 | 增量 |

理解这些输出文件有助于：
- 实时监控策略运行状态
- 回溯分析交易历史
- 诊断问题根因
- 评估策略绩效
