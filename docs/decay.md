# LEAN 原生性能变化/衰减指标

LEAN 没有专门的"alpha衰减"或"性能退化"指标，但有几个原生机制可以间接衡量。

## 1. Rolling Performances（滚动窗口统计）— 最核心

`StatisticsBuilder` 自动计算 **1/3/6/12个月** 四个滚动窗口的完整统计，包括 Sharpe、Alpha、Sortino、WinRate、Drawdown 等全部指标。比较不同窗口的 Sharpe/Alpha 变化即可发现衰减趋势。

- 代码位置：`Common/Statistics/StatisticsBuilder.cs` → `GetRollingPerformances()`
- **注意**：`LiveTradingResultHandler` 默认只发送 `TotalPerformance`，不发送 `RollingPerformances`（只有 `BacktestingResultHandler` 在最终结果中包含）。Live 模式下需要自行从 `StatisticsResults.RollingPerformances` 提取。

## 2. 持续采样的时间序列图表

`BaseResultsHandler.Sample()` 每日采样以下指标，形成连续时间序列：

| 图表 | 追踪内容 |
|---|---|
| Drawdown | 当前回撤随时间变化 |
| Portfolio Turnover | 换手率随时间变化（可揭示regime变化） |
| Exposure | 多空暴露度随时间变化 |
| Strategy Equity | 权益曲线 + 日收益率 |

## 3. Capacity Estimate（策略容量估算）— 仅回测

`Common/CapacityEstimate.cs` 用指数平滑追踪策略可承受的最大资金量。容量下降 = 市场冲击增大 = 策略退化信号。Live 模式不追踪此项。

## 4. Alpha Framework Insight Scoring

如果使用 Alpha Framework，每个 Insight 会持续更新 **Direction**（方向准确度 0-1）和 **Magnitude**（幅度准确度 0-1）分数，实时反映 alpha 质量。

## 5. Monte Carlo 压力测试 — 仅回测

`BacktestMonteCarloAnalysis` 模拟 fee stress / shock stress / combined stress 下的损失概率和 P95 回撤。

## 缺失的关键指标

LEAN 原生不提供以下功能：

- 无显式 "alpha decay" 指标
- 无 live vs backtest 性能对比/漂移检测
- 无运行时 rolling Sharpe / rolling returns（仅在 Report 后处理模块中计算，不在引擎运行时可用）
- 无 VaR 输出到统计摘要（`ValueAtRisk95/99` 计算了但未包含在 summary 输出中）

## 实际建议

对于 live paper / 实盘监控性能衰减，最实用的方案是：

1. **利用 Rolling Performances**：在 live bridge 脚本中提取 1/3/6/12 月滚动 Sharpe 和 Alpha，写入 InfluxDB，在 Grafana 中对比趋势
2. **自定义统计**：通过 `IStatisticsService.SetSummaryStatistic()` 注入自定义衰减指标（如 rolling Sharpe 变化率、live vs backtest Sharpe 差值）
3. **Insight Scoring**：如果用 Alpha Framework，Direction/Magnitude 分数的持续下降就是最直接的 alpha 衰减信号
