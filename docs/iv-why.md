# ETF 波动率择时策略回测问题诊断

**日期**: 2026-07-01
**策略**: AShareETFVolatilityTimingStrategy
**标的**: 510050 SSE 50ETF
**回测窗口**: 2024-02-08 ~ 2024-06-28

---

## 一、原日志为什么是"别的策略"

原始 `docs/etfvol.log` 显示的结果（SPY、2013年、Sharpe 8.3）**根本不是这个策略的回测**：

```
ERROR:: Configuration file config/config-ashare-etf-vol-timing-backtest.json does not exist, using config.json
Loaded BasicTemplateFrameworkAlgorithm
Dates: Start: 10/07/2013 End: 10/11/2013
```

**根因**: 命令行 `--config config/config-...json` 是相对路径，从执行目录解析不到，LEAN 回退到默认 `config.json`，跑了 SPY 5 天回测模板。

所谓"261% 年化 / Sharpe 8.3"是 SPY 默认模板的数，跟 ETF 波动率择时策略毫无关系。

---

## 二、用的是什么数据？数据量多少

修好路径后策略真正运行，读取两类数据：

| 数据类型 | 文件路径 | 数据量 |
|---------|---------|--------|
| 510050 ETF 日线 | `Data/equity/sse/daily/510050.csv` | 全量 2018-01-02 ~ 2025-12-31（1941 行）；回测窗口内约 **95 个交易日** |
| 预算 IV 曲线 | `Data/alternative/ashare-implied-volatility/sse/daily/510050.csv` | **只有 49 行**，2024-02-08 ~ 2024-06-18 |

**关键问题**:
- 回测窗口本身就只有 **~5 个月**
- IV 信号只有 **49 个点**
- 95 个交易日里有 **17 天没有 IV 数据**，代码回退成硬编码 `atmIv=0.2`（垃圾信号）
- 日志里 "165 data points" 是 LEAN 切片总数，不是行情天数

---

## 三、效果为什么这么差

修完 5 个 bug 后策略才真正交易，结果（`docs/etfvol-v2.log`）：

```
Total Orders 93 | Win Rate 58% | Profit-Loss Ratio 0.91 | Expectancy 0.097
Net Profit +0.057% | Sharpe -0.317 | Drawdown 5.1% | Total Fees ¥614.53
```

**5 个月只赚 0.057%，还跑输 510050 买入持有约 2.2 个点**（同期 ETF 从 2.385→2.441，+2.3%）。

### 差的原因分析

#### 1. 窗口太短 + IV 从未触发强信号

策略 `STRONG BUY` 要求 `atmIv>25%`，`REDUCE` 要求 `atmIv<15%`。

但这段 IV 实际只在 10%-16% 徘徊，**强买信号一次都没触发**，只在 15% 附近来回抖，造成 93 笔交易几乎一天一换手的 churn。

#### 2. IV-RV 价差对 ETF 方向择时本身是弱信号

IV>RV 的"波动率溢价"是期权卖方的 edge，不是 ETF 择时的 edge。

2024 H1 上证 50 是慢磨行情没有恐慌，这个信号基本是噪声。

#### 3. 手续费吃掉利润

- 手续费: ¥614
- 净盈利: ¥574

频繁调仓的手续费比赚的还多。

#### 4. IV 缺失用硬编码值兜底

17 天 IV 缺失用 `atmIv=0.2` 兜底，进一步污染信号。

---

## 四、修了哪 5 个 bug（系统性根因）

| # | 根因 | 现象 | 修复 |
|---|------|------|------|
| 1 | 配置路径解析不到 | 回退默认 algo | 用正确相对路径 `../../config/...` 从 `Launcher/bin/Debug` 执行 |
| 2 | config 里 handler 类名错误 | 启动崩溃 | `Result.BacktestingResultHandler` → `Results.BacktestingResultHandler`<br>`RealTime.ScheduledRealTimeHandler` → `RealTime.BacktestingRealTimeHandler` |
| 3 | 账户默认 USD，510050 是 CNY | "requires CNY non-zero conversion" 全部拒单 | 加 `SetAccountCurrency("CNY")` + `CashBook.Add("CNY",...)` |
| 4 | 默认 IB 费率模型不认 SSE | "unexpected equity Market sse" 全部拒单 | `equity.FeeModel = new AShareETFFeeModel()` |
| 5 | AShareETFFillModel 校验盘中时段 | 日线 bar 时间戳在盘外 → 订单静默 Invalid | 日线策略改用默认 `EquityFillModel`，去掉 FillModel/BuyingPowerModel 覆盖 |

### Bug 5 详解：日线策略的 FillModel 陷阱

`AShareETFFillModel` 继承自 `EquityFillModel`，添加了 A 股特有的校验：
- **盘中时段检查**: 9:30-11:30 / 13:00-15:00

问题在于：
- **日线 Resolution 的 bar 时间戳在 00:00（午夜）**
- 这个时间 **不在 9:30-11:30 或 13:00-15:00 范围内**
- FillModel 判定为"盘外时间" → 返回 `OrderStatus.Invalid`

结果：订单静默失败，持仓永远为 0。

**教训**: 日线策略不应使用带盘中时段校验的 FillModel，或需修改校验逻辑兼容日线 bar。

---

## 五、改进建议

要让这个策略有意义地评估，至少需要：

### 1. 扩展 IV 数据

- **扩展到 2-3 年**，覆盖至少一次 IV>30% 的恐慌 spike
- 目前 49 天根本无法验证"买恐慌"逻辑
- 数据源：`Scripts/export_ashare_implied_volatility_data.py`

### 2. 延长回测窗口

- 5 个月对波动率择时毫无统计意义
- 建议至少 2-3 年

### 3. 降低换手率

- `ivRvSpread>5%` 阈值太敏感
- 可改 8-10% 或加冷却期
- 否则手续费永远吃掉微弱 edge

### 4. 加基准对比

- 当前 `SetBenchmark(x=>0)` 把 Alpha/Beta 清零
- 应对比 510050 买入持有

### 5. 考虑 IV 信号的实际含义

IV-RV 价差反映的是期权市场定价偏差，更适合：
- 期权卖方策略（卖高 IV）
- 波动率套利
- 而非单纯的 ETF 方向择时

---

## 六、文件修改记录

| 文件 | 修改内容 |
|------|---------|
| `Launcher/config/config-ashare-etf-vol-timing-backtest.json` | 修复 handler 类名 |
| `Algorithm.CSharp/AShareETFVolatilityTimingStrategy.cs` | 添加 CNY 货币设置 + AShare 费率模型 |

修正后的日志：`docs/etfvol-v2.log`

---

## 七、正确执行命令

```bash
cd /home/project/hope/Lean/Launcher/bin/Debug
/usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll --config ../../config/config-ashare-etf-vol-timing-backtest.json
```

关键点：必须从 `Launcher/bin/Debug` 目录执行，这样相对路径 `../../config/...` 才能正确解析。
