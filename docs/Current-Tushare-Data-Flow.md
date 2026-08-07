# 当前 Tushare 历史/实时数据处理流程梳理

## 1. 说明与范围

当前仓库里，这两个问题对应的是 **两条不同但共享底层数据源的链路**：

1. **历史交易数据链路**：主要指 `tushare_data` 本地 Parquet 数据，经 Python 数据层读取后，进入 ETF T+0 特征构建、横截面打分、回测，再进入蒙特卡洛压力测试。
2. **实时交易数据链路**：主要指 live-paper 模式下，LEAN 通过 `TushareDataQueue`/`TushareHistoryProvider` 从本地 `tushare_data` 读取最新可用数据，驱动 T+1 策略生成 advisory 信号，并由 TUI 展示。

也就是说：

- **历史 + 蒙特卡洛** 当前主要是 **Python 研究/分析链路**。
- **实时 + live-paper 信号** 当前主要是 **LEAN C# 引擎链路**。
- 两条链路都依赖同一份本地 `tushare_data`，而不是直接连线上 Tushare 实时 API。

---

## 2. 共享基础层：`tushare_data` + dataset catalog

### 2.1 本地数据根目录

默认数据根目录是：

- `/home/project/tushare-downloader/tushare_data`

### 2.2 数据集目录定义

数据集映射定义在：

- `Launcher/config/config-ashare-dataset-catalog.json`

当前实际会用到的主要表包括：

- `fund_daily`：ETF 日线
- `daily`：个股日线
- `fund_nav`：ETF 净值
- `etf_share_size`：ETF 份额/规模
- `index_daily`：指数日线
- `etf_basic`：ETF 基础信息
- `stock_basic`：股票基础信息
- `stk_limit`：涨跌停价
- `daily_basic`：个股日度基本面
- `trade_cal`：交易日历

### 2.3 Python 数据访问入口

Python 侧统一入口是：

- `Scripts/tushare_data_layer.py`

其职责是：

1. 读取 dataset catalog。
2. 根据 `dataset + symbol + date range` 定位 Parquet 文件。
3. 支持按 `{symbol}` 分区路径和 `year=*` 这类分区目录。
4. 用 `pandas.read_parquet()` 读入数据。
5. 在内存中做缓存、字段筛选、日期过滤、排序。
6. 输出给上层脚本使用。

这层本质上是 **研究脚本 / 离线分析脚本的数据入口**。

### 2.4 LEAN 引擎数据访问入口

LEAN C# 引擎侧对应入口是：

- `Common/Data/TushareDataConverter.cs`
- `Engine/HistoricalData/TushareHistoryProvider.cs`
- `Engine/DataFeeds/Queues/TushareDataQueue.cs`

职责分工：

- `TushareDataConverter`：把本地 Parquet 日线读成 `TradeBar`
- `TushareHistoryProvider`：给 `History<TradeBar>()` 提供历史数据
- `TushareDataQueue`：给 live-paper 的订阅流提供“最新一根可用 bar”

---

## 3. 历史交易数据链路：从 `tushare_data` 到蒙特卡洛测试

> 当前真正完整跑到“蒙特卡洛测试”的，是 **ETF T+0 特征策略链路**。

### 3.1 启动入口

主要配置和脚本：

- 配置：`Launcher/config/config-ashare-etf-t0-monte-carlo.json`
- 脚本：`Scripts/ashare_etf_t0_monte_carlo.py`

这个脚本不是直接从 LEAN 回测结果二次采样，而是 **自己重新从 `tushare_data` 读取历史数据、构建基础回测、然后做蒙特卡洛压力测试**。

### 3.2 历史链路总览

```text
本地 tushare_data
  -> dataset catalog
  -> TushareDataLayer
  -> ETF universe 装载
  -> 单标的特征构建
  -> 跨标的横截面打分
  -> 日度回测净值序列
  -> block bootstrap / shock / slippage / regime stress
  -> 蒙特卡洛报告输出
```

### 3.3 第一步：装载 ETF 标的池

入口代码主要在：

- `Scripts/ashare_etf_t0_monte_carlo.py`
- `Scripts/tushare_lean_export.py`
- `Common/Securities/Equity/AShareETFMetadata.cs`

处理逻辑：

1. 从 `registry-file` 读取 ETF 注册表。
2. 可按配置剔除货币 ETF。
3. 得到回测 universe。

### 3.4 第二步：从 `tushare_data` 读取 ETF/净值/份额/指数数据

ETF T+0 基础回测核心会调用：

- `Scripts/ashare_etf_t0_feature_backtest.py`
- 关键函数：`build_symbol_feature_frame()`

对每只 ETF，会从 `TushareDataLayer` 读取：

1. `fund_daily`：`pre_close/open/high/low/close/pct_chg/amount/vol`
2. `fund_nav`：`unit_nav/adj_nav`
3. `etf_share_size`：`total_share/total_size`
4. `index_daily`：ETF 对应指数的 `pre_close/open/close`

这些表按 `trade_date` 对齐后，进入统一特征构造。

### 3.5 第三步：构建特征字段

同样在：

- `Scripts/ashare_etf_t0_feature_backtest.py`
- 关键函数：`prepare_symbol_frame()`

当前会构造的核心字段包括：

- 收益类：`trade_return`、`gap_return`
- 位置类：`close_location`
- 波动/流动性：`volatility_10`、`liquidity_5`
- 动量：`momentum_5`、`momentum_20`
- 净值偏离：`nav_premium_1`、`nav_premium_z20`
- 份额/规模变化：`share_change_5`、`size_change_5`
- 指数相对表现：`excess_gap`、`excess_intraday`、`tracking_error_10`、`index_momentum_5`

随后这些特征整体会 **滞后 1 天**，生成用于交易决策的 `signal_*` 字段，避免未来函数。

### 3.6 第四步：横截面打分

入口：

- `Scripts/ashare_etf_t0_feature_backtest.py`
- 关键函数：`compute_cross_section_scores()`

处理逻辑：

1. 每个交易日把所有 ETF 放在一起做横截面比较。
2. 对每个特征做标准化（z-score）。
3. 根据预设权重线性组合成 `score_base`。
4. 再叠加条件信号（如 `nav_premium_z20` overlay）。
5. 得到每个 ETF 当天最终 `score`。

### 3.7 第五步：基础回测

入口：

- `Scripts/ashare_etf_t0_feature_backtest.py`
- 关键函数：`backtest_from_scores()`

处理逻辑：

1. 按日分组处理 `scored` 数据。
2. 根据 `top-n`、`min-score-spread`、`max-average-gap-abs` 过滤掉低质量交易日。
3. 根据市场动量 / 波动进入 `normal / medium / high` 风险状态。
4. 动态调整：
   - 仓位暴露倍数
   - 实际选股数
   - 最低分差阈值
   - 流动性分位过滤
5. 再叠加组合级风控：
   - 波动率目标缩放
   - quarter Kelly 缩放
6. 计算当日组合 `gross_return / net_return / equity`。

输出是一个按日的回测轨迹 `daily`，以及聚合的 `summary`。

### 3.8 第六步：进入蒙特卡洛测试

入口：

- `Scripts/ashare_etf_t0_monte_carlo.py`
- 关键函数：`build_base_backtest()` + 后续 stress 函数

蒙特卡洛流程是：

1. 先通过上述步骤得到基础回测 `daily/net_return`。
2. 对 `net_return` 序列做 `block bootstrap`。
3. 在 bootstrap 路径上叠加不同压力：
   - `apply_fee_stress()`：额外手续费
   - `apply_market_shock_stress()`：市场冲击
   - `apply_execution_slippage_stress()`：成交滑点
   - `regime_bootstrap_returns()`：按风险 regime 重采样
4. 对每条路径计算：
   - `final_equity`
   - `total_return`
   - `annualized_return`
   - `max_drawdown`
   - `sharpe`
5. 汇总成各场景的统计分布。

### 3.9 历史链路输出

当前主要输出包括：

- 特征导出报告：`Results/ashare-etf-t0-feature-export-report.json`
- 特征文件：`Data/alternative/ashare-etf-t0-features/<market>/daily/<ticker>.csv`
- 蒙特卡洛报告：通常写到 `Launcher/bin/Debug/AShareEtfT0FeatureIntradayAlgorithm-monte-carlo.log`
- 基础回测汇总：由脚本打印或写入报告文件

### 3.10 一个重要补充：历史链路有两种消费方式

当前历史数据实际有两种消费方式：

#### A. 直接 Python 消费

用于：

- 特征导出
- ETF T+0 特征回测
- 诊断脚本
- 蒙特卡洛测试
- T+1 Python 回测脚本

特点：

- 直接读 Parquet
- 速度快
- 方便做分析/研究/参数试验

#### B. 通过 LEAN HistoryProvider 消费

用于：

- T+1 C# 回测/LEAN live-paper 中的 `History<TradeBar>()`

特点：

- 通过 `TushareHistoryProvider -> TushareDataConverter`
- 主要服务 LEAN 引擎侧算法
- 当前只支持 `Daily` 分辨率

---

## 4. 实时交易数据链路：从 Tushare 接入到 live-paper 生成交易信号

> 当前“实时”链路本质上是 **基于本地 `tushare_data` 的 latest snapshot/polling 仿真**，不是直连 Tushare 在线 streaming 行情。

### 4.1 启动入口

主要入口：

- live-paper 配置：`Launcher/config/config-ashare-t1-live-paper.json`
- momentum live-paper 配置：`Launcher/config/config-ashare-t1-momentum-live-paper.json`
- 启动脚本：`Scripts/ashare_t1_live_paper.py`
- TUI：`Scripts/ashare_t1_tui.py`

### 4.2 实时链路总览

```text
本地 tushare_data
  -> TushareDataQueue (每 60 秒轮询最新 bar)
  -> LiveTradingDataFeed
  -> LEAN live-paper 算法
  -> TushareHistoryProvider 提供 lookback history
  -> 计算信号 / 更新 advisory 持仓
  -> 写 signal / portfolio / daily 输出文件
  -> TUI 读取输出并展示
```

### 4.3 第一步：LEAN live-paper 初始化

配置里关键组件是：

- `data-feed-handler = LiveTradingDataFeed`
- `data-queue-handler = ["TushareDataQueue"]`
- `history-provider = ["TushareHistoryProvider"]`
- `transaction-handler = BacktestingTransactionHandler`
- `live-mode-brokerage = PaperBrokerage`

这意味着：

1. LEAN 进入 live-paper 模式。
2. 实时数据由 `TushareDataQueue` 提供。
3. 历史 lookback 由 `TushareHistoryProvider` 提供。
4. 账户是 paper brokerage。
5. 当前 T+1 算法采取 **advisory / synthetic** 模式，不直接发真实券商订单。

### 4.4 第二步：TushareDataQueue 轮询“最新 bar”

实时数据队列入口：

- `Engine/DataFeeds/Queues/TushareDataQueue.cs`

处理逻辑：

1. `SetJob()` 从作业参数中读取 `tushare-data-path`。
2. 内部创建 `TushareDataConverter`。
3. 每个订阅 `Subscribe()` 后，进入无限循环：
   - 把 LEAN `Symbol` 转成 Tushare `ts_code`
   - 调用 `_converter.GetLatestData(tsCode)`
   - yield 返回最新一根 `TradeBar`
   - `Sleep(60s)` 后继续轮询

因此当前实时 feed 的语义是：

- **不是逐笔/逐分钟线上流式行情**
- 而是 **每 60 秒从本地 Parquet 中取最后一根可用日线 bar 作为“实时”快照**

### 4.5 第三步：TushareHistoryProvider 提供历史窗口

历史服务入口：

- `Engine/HistoricalData/TushareHistoryProvider.cs`

处理逻辑：

1. 初始化时读取 `tushare-data-path`。
2. 当算法调用 `History<TradeBar>()` 时：
   - 把 LEAN `Symbol` 转为 `ts_code`
   - 校验当前只支持 `Daily`
   - 调用 `TushareDataConverter.GetDailyData()`
3. `TushareDataConverter` 从 Parquet 读取并缓存全量日线，再按请求窗口截取。

这一步保证 live-paper 算法即使在实时运行，也能拿到过去 N 日的 lookback 数据做信号计算。

### 4.6 第四步：live-paper 算法初始化

当前主要算法：

- `Algorithm.CSharp/AShareT1MeanReversionAlgorithm.cs`
- `Algorithm.CSharp/AShareT1MomentumAlgorithm.cs`

初始化阶段会做：

1. 从配置参数读取：
   - `universe`
   - `lookback-period`
   - `entry-threshold`
   - `exit-threshold`
   - `position-size`
   - `initial-capital`
   - `fee-rate`
   - 输出文件路径
2. `AddEquity()` 添加股票订阅。
3. 注入 A 股 T+1 模型：
   - `AShareStockFeeModel`
   - `AShareStockFillModel`
   - `AShareStockBuyingPowerModel`
   - `DelayedSettlementModel(1, 09:00)`
4. 注册定时任务：开盘后 5 分钟做一次 `EvaluateSignals()`。
5. 初始化时先落一次空的/初始的信号与资产快照文件。

### 4.7 第五步：OnData 驱动实时评估与快照落盘

以 `AShareT1MeanReversionAlgorithm` 为例：

1. `OnData()` 只在 `LiveMode` 下工作。
2. 如果收到数据：
   - 先更新持仓的标记价格
   - 判断是否需要做一次“开盘后补算”（`ShouldEvaluateLiveCatchUp()`）
   - 判断是否需要做周期性快照落盘（`ShouldPersistLiveSnapshot()`）
3. 当前默认行为：
   - 开盘后若当天还未算过，在 `09:35` 之后触发评估
   - 每隔约 1 分钟刷新一次输出快照

### 4.8 第六步：生成交易信号

信号生成的核心在 `EvaluateSignals()`：

#### Mean Reversion 版本

1. 对每个标的调用 `History<TradeBar>(symbol, lookback, Daily)`。
2. 计算最近窗口的 z-score。
3. 若已有持仓且满足：
   - `holding_days >= 1`
   - `zscore >= exit-threshold`
   则生成卖出信号。
4. 对空仓标的按“最超跌”排序，若：
   - `zscore <= entry-threshold`
   则生成买入信号。

#### Momentum 版本

1. 同样拉取日线历史。
2. 计算窗口动量 `last / first - 1`。
3. 若已有持仓且：
   - `holding_days >= 1`
   - `momentum <= exit-threshold`
   则生成卖出信号。
4. 对候选标的按“动量最高”排序，若：
   - `momentum >= entry-threshold`
   则生成买入信号。

### 4.9 第七步：更新 advisory 持仓与资产快照

生成信号后，算法不会提交券商订单，而是更新内部 advisory 状态：

1. 更新 `_advisoryCash`
2. 更新 `_positions`
3. 更新 `holding_days`
4. 根据 T+1 规则控制 `available_quantity`

这部分写入的文件包括：

- `signal-file`：当前全部信号快照 JSON
- `portfolio-snapshot-file`：当前组合快照 JSON
- `daily-summary-file`：按日汇总 CSV
- `Results/signals/signals_YYYYMMDD.jsonl`：增量信号历史

典型输出文件：

- `Results/ashare-t1-signals.json`
- `Results/ashare-t1-portfolio.json`
- `Results/ashare-t1-live-daily.csv`
- `Results/signals/signals_YYYYMMDD.jsonl`

Momentum live-paper 则使用对应的 `ashare-t1-momentum-*` 输出文件。

### 4.10 第八步：TUI 展示

TUI 入口：

- `Scripts/ashare_t1_tui.py`

处理逻辑：

1. 读取 live-paper launcher 配置。
2. 解析 `parameters` 里的：
   - `signal-file`
   - `portfolio-snapshot-file`
   - `daily-summary-file`
   - `tushare-data-path`
3. 从信号文件和组合文件恢复当前运行状态。
4. 可选调用 `TushareRealtimeDataFeed`：
   - 再次从本地 `tushare_data` 取最新快照
   - 用于刷新持仓展示中的 `last_price`
5. 用 `rich` 渲染：
   - 账户总览
   - 最新信号
   - 持仓明细

注意：

- `Scripts/tushare_realtime_feed.py` 也是 **本地 snapshot 读取器**，并不是在线推流行情接口。

---

## 5. 当前实现里的关键分叉

### 5.1 历史链路分叉

#### 分叉 A：研究脚本直读 Parquet

代表：

- `Scripts/ashare_etf_t0_feature_backtest.py`
- `Scripts/ashare_etf_t0_monte_carlo.py`
- `Scripts/export_ashare_etf_t0_feature_data.py`
- `Scripts/ashare_t1_backtest.py`

特点：

- 快
- 可直接在 Python 中做分析
- 适合研究、诊断、蒙特卡洛

#### 分叉 B：LEAN 引擎消费 Tushare 数据

代表：

- `TushareHistoryProvider`
- `TushareDataQueue`
- `AShareT1MeanReversionAlgorithm`
- `AShareT1MomentumAlgorithm`

特点：

- 适合 live-paper / C# 策略
- 和 LEAN 调度、风控、账户模型集成更自然

### 5.2 实时链路并非真实流式行情

这是当前最需要明确的现状：

1. `TushareDataQueue` 不是直接订阅 Tushare 在线行情。
2. `TushareRealtimeDataFeed` 也不是在线 API 拉流。
3. 当前 live-paper 的“实时”是：
   - 基于本地 `tushare_data`
   - 周期性读取最后一根可用日线/快照
   - 作为仿真 live-paper 数据源

换句话说，**当前更准确的定位是“本地历史数据驱动的 live-paper 仿真”**。

---

## 6. 一句话总结

### 历史数据链路

当前历史链路是：

`本地 tushare_data Parquet -> TushareDataLayer -> 特征构建/横截面打分 -> ETF T+0 基础回测 -> 蒙特卡洛压力测试 -> 报告输出`

### 实时数据链路

当前实时链路是：

`本地 tushare_data -> TushareDataQueue / TushareHistoryProvider -> LEAN live-paper -> T+1 advisory 策略计算 -> signal/portfolio/daily 文件输出 -> TUI 展示`

---

## 7. 目前最重要的结论

1. **历史 + 蒙特卡洛** 当前主链路在 Python 侧。
2. **实时 + live-paper 信号生成** 当前主链路在 LEAN C# 侧。
3. 两条链路共享 `tushare_data`，但消费方式不同。
4. 当前“实时”并不是线上 Tushare streaming，而是本地数据轮询仿真。
5. 如果后续要接真正实时行情，最应该替换/增强的是：
   - `Engine/DataFeeds/Queues/TushareDataQueue.cs`
   - `Scripts/tushare_realtime_feed.py`

