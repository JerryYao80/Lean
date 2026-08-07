# LEAN Live Paper 不接入券商接口的影响

本文档分析在 LEAN Live Paper 模式下不接入任何券商接口时的影响。

---

## 结论

**不会影响输出内容**。

在 Live Paper 模式下，LEAN 使用内置的 `PaperBrokerage`（模拟券商）代替真实券商接口，所有输出内容与接入真实券商时完全一致。

---

## Live Paper 模式架构

### 配置文件示例

```json
{
  "environment": "live-paper",
  "live-mode": true,
  "live-mode-brokerage": "PaperBrokerage",
  "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
  "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
  "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler"
}
```

### 关键组件

| 组件 | 说明 |
|------|------|
| `PaperBrokerage` | 模拟券商，不连接真实券商 API |
| `LiveTradingResultHandler` | 实时结果处理器（与真实交易共用） |
| `LiveTradingDataFeed` | 实时数据源 |
| `BacktestingTransactionHandler` | 使用回测模型处理订单 |

---

## PaperBrokerage 工作原理

`PaperBrokerage` 继承自 `BacktestingBrokerage`，其核心特点：

### 1. 订单执行模拟

```csharp
// PaperBrokerage.cs
public class PaperBrokerage : BacktestingBrokerage
{
    // 继承自 BacktestingBrokerage
    // 使用 FillModel 进行订单成交模拟
}
```

- 调用算法配置的 **FillModel**（如 `ImmediateFillModel`、`VolumeFillModel`）
- 根据实时市场数据进行模拟成交
- 生成订单事件（OrderEvents）

### 2. 资金和持仓

```csharp
// BacktestingBrokerage.cs
public override List<CashAmount> GetCashBalance()
{
    return Algorithm.Portfolio.CashBook.Select(...);
}

public override List<Holding> GetAccountHoldings()
{
    return Algorithm.Portfolio.Securities.Where(...);
}
```

- 资金余额从算法 Portfolio 获取
- 持仓从算法组合中计算

### 3. 分红处理

```csharp
// PaperBrokerage.cs - 支持模拟分红
foreach (var dividend in Algorithm.CurrentSlice.Dividends.Values)
{
    var distribution = security.Holdings.Quantity * dividend.Distribution;
    security.QuoteCurrency.AddAmount(distribution);
}
```

---

## 输出内容对比

### 相同点

| 输出类型 | Live Paper (PaperBrokerage) | Live Trading (真实券商) |
|----------|-----------------------------|-------------------------|
| 状态文件 (.json) | ✅ 完全一致 | ✅ 完全一致 |
| 图表数据 | ✅ 完全一致 | ✅ 完全一致 |
| 订单事件 | ✅ 完全一致 | ✅ 完全一致 |
| 日志 | ✅ 完全一致 | ✅ 完全一致 |
| 持仓数据 | ✅ 完全一致 | ✅ 完全一致 |

### 差异点

| 项目 | Live Paper | Live Trading |
|------|------------|--------------|
| **成交价格** | 使用 FillModel 模拟 | 真实市场成交价 |
| **成交时间** | 基于数据频率 | 真实订单执行延迟 |
| **手续费** | 配置的固定费率 | 真实券商费率 |
| **滑点** | 可配置 | 真实滑点 |

---

## 为什么输出相同？

### 共用同一 ResultHandler

```csharp
// 配置
"result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler"
```

**LivePaper 和 LiveTrading 共用同一个 `LiveTradingResultHandler`**，因此：

1. **输出文件格式相同** - 都保存 `{AlgorithmId}.json`、`{Date}_minute.json` 等
2. **更新频率相同** - 都是实时推送
3. **数据结构相同** - charts、orders、holdings 等字段完全一致

### 订单处理流程

```
算法下单 → TransactionHandler → Brokerage → FillModel → OrderEvent
                              ↓
                      LiveTradingResultHandler  ← 共用
                              ↓
                        输出文件
```

无论使用 `PaperBrokerage` 还是真实券商接口，订单事件都会发送到同一个 `LiveTradingResultHandler`，生成相同的输出。

---

## 配置选择

### 场景 1：仅模拟交易（不接入券商）

```json
{
  "environment": "live-paper",
  "live-mode-brokerage": "PaperBrokerage"
}
```

### 场景 2：接入真实券商

```json
{
  "environment": "live-interactive",
  "live-mode-brokerage": "InteractiveBrokersBrokerage"  // 或其他券商
}
```

---

## 注意事项

### 1. 数据源独立

- Live Paper 使用 `LiveTradingDataFeed` 订阅实时市场数据
- 不依赖券商 API 获取行情

### 2. 模拟 vs 实盘差异

由于使用 FillModel 模拟成交，存在以下差异：

| 差异项 | Live Paper | 真实交易 |
|--------|------------|----------|
| 订单执行 | 确定性（基于模型） | 不确定性（市场波动） |
| 部分成交 | 按模型规则 | 取决于市场深度 |
| 成交价格 | 可能是当前价 | 滑点影响 |

### 3. 适合场景

- **PaperBrokerage 适合**：策略验证、参数调优、历史重现
- **真实券商适合**：实盘交易前验证、真实市场环境测试

---

## 总结

| 问题 | 答案 |
|------|------|
| 不接入券商接口会影响输出内容吗？ | **不会** |
| 输出文件格式相同吗？ | **完全相同** |
| 可以用于验证策略逻辑吗？ | **可以** |
| 与真实交易结果会一致吗？ | **成交细节可能不同，但输出格式一致** |

Live Paper 模式的本质是：
- **使用模拟券商（PaperBrokerage）+ 实时数据源**
- **输出组件与真实交易完全共用**
- **因此输出内容不受券商接口影响**
