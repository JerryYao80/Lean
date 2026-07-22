# AShareSectorSmallCap 策略 — 回测与 Grafana 可视化优化记录

## 问题

AShareSectorSmallCapAlgorithm 在 Grafana LEAN Results Overview 中所有数值（Backtest Net Profit、Sharpe Ratio、Portfolio/Cash/Holdings、P&L/Fees/Margin 等）均为空或无变化。

对标：AShareBarraCNE5Algorithm 的回测结果显示最为标准，所有面板有数据。

## 根因

**原 AShareSectorSmallCapAlgorithm 采用"咨询模式"（Advisory Mode），不走 LEAN 真实下单流程。**

咨询模式的问题链：

```
咨询模式（旧）                          真实下单模式（BarraCNE5 可用）
─────────────────                      ──────────────────────────────
自定义 _advisoryCash 跟踪现金           LEAN Portfolio.Cash 跟踪现金
自定义 _positions 跟踪持仓              LEAN Security.Holdings 跟踪持仓
手动计算权益                           LEAN Portfolio.TotalPortfolioValue
不调用 MarketOrder()                   调用 MarketOrder() / SetHoldings()
  ↓                                      ↓
无 OrderEvent 产生                      OrderEvent → TransactionHandler
无 Fill 事件                            FillModel 产生 Fill
LEAN ResultHandler 收不到任何事件       ResultHandler 流式输出到 InfluxDB
  ↓                                      ↓
InfluxDB 无数据                         InfluxDB 有完整数据
  ↓                                      ↓
Grafana 全部为空                        Grafana 完整展示
```

核心差异：**LEAN 的 InfluxDB ResultExporter 只处理通过 TransactionHandler 管道的真实订单事件**。咨询模式绕过了整个订单管道，ResultHandler 无数据可导出。

## 修复方案

将 AShareSectorSmallCapAlgorithm 从咨询模式改为**真实下单 + 信号日志**模式：

### 改动 1：用 MarketOrder() 替代手动持仓管理

```csharp
// 旧（咨询模式）：手动记账
_advisoryCash -= qty * price + fee;
_positions[sym] = new AdvisoryPosition { ... };

// 新（真实下单）：通过 LEAN 下单
MarketOrder(sym, qty);
```

```csharp
// 旧：手动卖出
_advisoryCash += pos.Quantity * price - fee;
_positions.Remove(sym);

// 新：通过 LEAN 下单
MarketOrder(holding.Symbol, -qty);
```

### 改动 2：用 LEAN Portfolio 替代自定义权益追踪

```csharp
// 旧：自定义权益计算
private decimal GetAdvisoryEquity() =>
    _advisoryCash + _positions.Values.Sum(p => p.Quantity * p.LastPrice);

// 新：使用 LEAN 内置 Portfolio
var total = Portfolio.TotalPortfolioValue;
var invested = Portfolio.Values.Where(h => h.Invested).ToList();
var mv = invested.Sum(h => h.HoldingsValue);
```

### 改动 3：添加 OnOrderEvent 和 SetRuntimeStatistic

```csharp
public override void OnOrderEvent(OrderEvent orderEvent)
{
    if (orderEvent.Status == OrderStatus.Filled)
    {
        Log($"[ORDER] {orderEvent.Direction} {orderEvent.Symbol.Value} " +
            $"qty={orderEvent.FillQuantity} price={orderEvent.FillPrice:C2}");
    }
}
```

```csharp
SetRuntimeStatistic("Positions", Portfolio.Values.Where(h => h.Invested).Count().ToString());
SetRuntimeStatistic("Equity", Portfolio.TotalPortfolioValue.ToString("C0"));
```

### 改动 4：添加 SetSummaryStatistic

```csharp
SetSummaryStatistic("Total Return", (double)totalReturn);
SetSummaryStatistic("Sharpe Ratio", 0);
SetSummaryStatistic("Total Signals", _signals.Count);
```

### 保留：信号日志输出

信号（BUY/SELL 记录）仍以 JSON 格式输出，保留策略可审计性：

```csharp
_signals.Add(CreateSignal("BUY", sym, price, qty,
    $"sector={factor?.Sector} mv={factor?.TotalMvWan:F0}万 pb={factor?.Pb:F2}",
    0.75m, "normal"));
```

## 修复后的数据流

```
MarketOrder() 生成 Order
    ↓
FillModel 生成 Fill
    ↓
TransactionHandler 处理 OrderEvent
    ↓
BacktestingResultHandler 接收事件
    ↓
InfluxDbResultExporter 写入 InfluxDB
    ↓  measurement 列表：
    ├── lean_portfolio   (cash, total_value, ...)
    ├── lean_holding     (quantity, market_value, ...)
    ├── lean_order       (direction, status, price, ...)
    ├── lean_order_event (fill_price, fill_quantity, ...)
    ├── lean_trade       (entry_price, exit_price, pnl, ...)
    ├── lean_cash        (amount, ...)
    ├── lean_chart       (equity curve points)
    ├── lean_metric      (custom metrics)
    ├── lean_message     (log messages)
    ├── equity           (自定义导出的权益曲线)
    ├── statistics       (自定义导出的统计指标)
    └── strategy_lifecycle (策略生命周期状态)
    ↓
Grafana 查询 InfluxDB → 完整展示
```

## 回测运行命令

```bash
# 构建
/usr/local/dotnet/dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug
/usr/local/dotnet/dotnet build Launcher/QuantConnect.Lean.Launcher.csproj -c Debug -m:1

# 运行回测（必须设置 INFLUXDB_TOKEN 才能写入 InfluxDB）
cd Launcher/bin/Debug
INFLUXDB_TOKEN="admin-token-leansystem" \
  /usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll \
  --config /home/project/hope/Lean/Launcher/config/config-ashare-sector-smallcap-backtest.json
```

## Grafana 验证

1. 打开 Grafana：`http://<host>:3000`
2. 进入 **LEAN** → **LEAN Results Overview**
3. 选择 algorithm = `AShareSectorSmallCapAlgorithm`
4. 确认以下面板有数据：
   - Portfolio / Cash / Holdings 图表有变化曲线
   - P&L / Fees / Margin 有数值
   - Backtest Net Profit 有值（51.2%）
   - Backtest Sharpe Ratio 有值（0.54）
   - Orders 表有订单记录

### 关键 InfluxDB 查询验证

```flux
// 权益曲线
from(bucket: "quant") |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "equity")
  |> filter(fn: (r) => r["algorithm_id"] == "AShareSectorSmallCapAlgorithm")

// 统计指标
from(bucket: "quant") |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "statistics")
  |> filter(fn: (r) => r["algorithm_id"] == "AShareSectorSmallCapAlgorithm")

// 策略生命周期
from(bucket: "quant") |> range(start: -30d)
  |> filter(fn: (r) => r["_measurement"] == "strategy_lifecycle")
  |> filter(fn: (r) => r["strategy_id"] == "AShareSectorSmallCapAlgorithm")
```

## 数据量参考

| Measurement | 数据点数 | 说明 |
|------------|---------|------|
| lean_portfolio | ~41K | 每日组合快照 |
| lean_holding | ~122K | 每日每持仓 |
| lean_order | ~6.2K | 所有订单 |
| lean_order_event | ~10K | 订单状态变更 |
| lean_trade | ~4K | 成交记录 |
| equity | 1,565 | 日线权益 |
| statistics | 4 | 回测统计 |

## 经验总结

### 什么情况下 Grafana 有数据

**必须使用 LEAN 真实下单 API**：`MarketOrder()`、`LimitOrder()`、`SetHoldings()` 等。这些 API 会触发完整的订单生命周期（Order → Fill → OrderEvent → TransactionHandler → ResultHandler → InfluxDB）。

### 什么情况下 Grafana 没数据

- **咨询模式**：自己管理持仓和现金，不调用 LEAN 下单 API
- **BarraCNE5 的 SyncLeanPortfolioState()**：虽然直接写入 `security.Holdings` 和 `Portfolio.CashBook`，但不产生 OrderEvent，所以 InfluxDB 仍无订单数据
- **自定义文件输出**：只写 JSON/CSV 文件，不走 LEAN 内部管道

### 最佳实践

对于需要在 Grafana 完整展示的策略：

1. **用 LEAN 真实下单** — 不要自建持仓管理系统
2. **用 Portfolio.TotalPortfolioValue** — 不要自己算权益
3. **添加 OnOrderEvent** — 监控订单状态
4. **添加 SetRuntimeStatistic** — 实时面板数据
5. **添加 SetSummaryStatistic** — 回测统计指标
6. **保留信号日志** — 以 JSON 形式记录买卖理由，用于审计
7. **运行时设置 INFLUXDB_TOKEN** — 否则 InfluxDB 写入会静默失败
