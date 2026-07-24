# SoloQuant Prefect Pipeline — Task 详解

## 概述

SoloQuant Pipeline 使用 Prefect 3.x 进行任务编排，每个 pipeline 阶段对应一个 Prefect `@task`。整个 pipeline 由一个 `@flow`（`soloquant-pipeline-tick`）统一调度，每 300 秒（5 分钟）执行一次 tick。

**核心文件：**
- `Scripts/soloquant_prefect_flow.py` — Flow 定义与调度
- `Scripts/soloquant_prefect_tasks.py` — 所有 @task 定义
- `Scripts/soloquant_prefect_config.py` — Prefect 配置常量
- `Scripts/soloquant_prefect_influx_export.py` — InfluxDB 指标导出

**Prefect 基础设施：**
- Server: `http://localhost:4200`
- Work Pool: `soloquant-pool`
- UI: `http://84.8.248.198:4200`

---

## Flow

### `soloquant-pipeline-tick`

| 属性 | 值 |
|------|-----|
| 类型 | `@flow` |
| 超时 | 600 秒 |
| 重试 | 0（flow 内部自行处理错误） |
| 调度 | 每 300 秒由 `serve()` 自动触发 |

Flow 按顺序执行 14 个 stage task，结束后执行 `monitor_background_jobs` task。非 ok 状态的 stage 根据 `DEGRADED_ON_FAIL` 集合决定是降级继续还是中断 tick。

---

## Task 列表（共 15 个）

### Category A：有限同步任务

这类 task 在 Prefect worker 进程内同步执行，有明确的超时和重试策略。

---

#### 1. `ingest_local_strategies`

| 属性 | 值 |
|------|-----|
| Task 名 | `ingest-local-strategies` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 300 秒 |
| 间隔 | 1 小时 |

**功能：** 扫描 `Results/soloquant/local-strategies/` 目录，将用户手动放入的策略文件（`.cs` / `.py`）注册到 strategy-registry。是策略进入 pipeline 的入口。

**执行逻辑：**
1. 检查 1 小时间隔门控，未到时间则跳过
2. 调用 `DefaultPipelineServices.ingest_local_strategies()` 扫描本地策略
3. 将新发现的策略写入 `strategy-registry.json`
4. 记录完成时间到 `soloquant-pipeline-state.json`

---

#### 2. `prepare_iv_data`

| 属性 | 值 |
|------|-----|
| Task 名 | `prepare-iv-data` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 900 秒 |

**功能：** 从 Tushare 数据准备隐含波动率（Implied Volatility）数据，供后续事件信号构建使用。

**执行逻辑：**
1. 调用 `DefaultPipelineServices.prepare_iv_data()`
2. 读取 Tushare 期权数据，计算 IV 指标
3. 缓存到 `local_data/soloquant-field-cache/`

---

#### 3. `build_event_graph`

| 属性 | 值 |
|------|-----|
| Task 名 | `build-event-graph` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 600 秒 |

**功能：** 从已爬取的财经情报（finance intelligence）构建金融事件图（Event Graph）。事件图描述事件之间的因果关系和时序依赖。

**执行逻辑：**
1. 调用 `run_orchestrator_command(["--build-finance-event-graph"])`
2. 解析已爬取的财经新闻/公告
3. 提取事件节点和因果关系边
4. 存储为事件图数据结构

---

#### 4. `build_event_signals`

| 属性 | 值 |
|------|-----|
| Task 名 | `build-event-signals` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 600 秒 |

**功能：** 基于事件图构建事件信号上下文（Event Signal Context）。将事件图转化为可用于策略决策的交易信号。

**执行逻辑：**
1. 调用 `run_orchestrator_command(["--build-event-signal-context"])`
2. 读取事件图，计算事件对市场的预期影响
3. 生成信号上下文供策略使用

---

#### 5. `materialize_variants`

| 属性 | 值 |
|------|-----|
| Task 名 | `materialize-variants` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 900 秒 |

**功能：** 将 LLM 生成的策略代码具体化为可编译的变体（variants）。为每个策略生成多个参数/逻辑变体版本，用于后续回测对比。

**执行逻辑：**
1. 调用 `run_orchestrator_command(["--materialize-generated-strategies"])`
2. 读取 `generated-code/` 中的策略代码
3. 为每个策略创建 baseline + N 个 learned 变体
4. 生成 LEAN 兼容的 `.cs` / `.py` 文件和 `config-*.json`

---

#### 6. `export_influx`

| 属性 | 值 |
|------|-----|
| Task 名 | `export-influx` |
| 重试 | 1 次 |
| 重试延迟 | 60 秒 |
| 超时 | 600 秒 |

**功能：** 将 pipeline 所有数据导出到 InfluxDB（5 个子导出），供 Grafana 可视化。

**执行逻辑：**
1. 调用 `DefaultPipelineServices.export_influx()`
2. 依次导出：
   - pipeline funnel（漏斗数据）
   - strategy progress（策略进度）
   - strategy lifecycle（策略生命周期）
   - backtest results（回测结果）
   - live-paper stats（模拟盘统计）
3. 写入 InfluxDB `quant` bucket

---

### Category B：LLM 后台任务（哨兵模式）

这类 task 采用**哨兵模式**：启动子进程后立即返回，不等待子进程完成。子进程独立运行，PID 记录在 `llm-background/*.pid.json` 中。

---

#### 7. `data_driven_crawl`

| 属性 | 值 |
|------|-----|
| Task 名 | `data-driven-crawl` |
| 重试 | 0 次 |
| 超时 | 60 秒 |
| 间隔 | 8 小时 |

**功能：** 启动数据驱动的策略爬取。基于已有数据覆盖情况，自动发现数据中可挖掘的策略因子，然后爬取相关研究论文/文章。

**执行逻辑：**
1. 检查 8 小时间隔门控
2. 检查是否已有同 stage 后台进程在运行
3. 清理已结束的旧后台进程
4. 启动子进程：
   ```
   soloquant_crawl_scheduler.py --task data_driven_strategy --force --once
   ```
5. 记录 PID，立即返回 `{"mode": "background", "action": "launched"}`

---

#### 8. `crawl_research`

| 属性 | 值 |
|------|-----|
| Task 名 | `crawl-research` |
| 重试 | 0 次 |
| 超时 | 60 秒 |
| 间隔 | 8 小时 |

**功能：** 启动研究爬取。从 SearxNG 搜索引擎爬取量化策略研究和财经情报文章，经 DeepSeek LLM 筛选后存入 `llm-background/`。

**执行逻辑：**
1. 检查 8 小时间隔门控
2. 检查后台进程是否已在运行
3. 启动子进程：
   ```
   soloquant_crawl_scheduler.py --task strategy --task finance_intelligence --force --once
   ```
4. 同时爬取 `strategy`（策略研究）和 `finance_intelligence`（财经情报）两类任务
5. 记录 PID，立即返回

---

#### 9. `prepare_reproduction`

| 属性 | 值 |
|------|-----|
| Task 名 | `prepare-reproduction` |
| 重试 | 0 次 |
| 超时 | 60 秒 |

**功能：** 启动策略复现准备和财经情报分析两个后台子进程。将已爬取的研究论文解析为 LLM 可理解的 prompt，并分析财经情报。

**执行逻辑：**
1. 检查两个子阶段的后台进程状态：
   - `prepare_reproduction` — 解析研究论文，生成 LLM prompt
   - `analyze_finance_intelligence` — 分析财经情报
2. 对每个未运行的子阶段，分别启动子进程：
   ```
   soloquant_orchestrator.py --prepare-reproduction
   soloquant_orchestrator.py --analyze-finance-intelligence
   ```
3. 各自记录 PID，立即返回

---

#### 10. `reproduce_one`

| 属性 | 值 |
|------|-----|
| Task 名 | `reproduce-one` |
| 重试 | 0 次 |
| 超时 | 60 秒 |
| 间隔 | 30 分钟 |

**功能：** 启动 LLM 代码生成 + 冒烟测试后台子进程。每次处理 1 个策略，使用 DeepSeek LLM 将研究论文转化为可编译的 C#/Python 代码。

**执行逻辑：**
1. 检查 30 分钟间隔门控
2. 检查后台进程是否已在运行
3. 启动子进程：
   ```
   soloquant_orchestrator.py --generate-strategy-implementations --max-items 1 --smoke-test-strategies
   ```
4. 子进程内部流程：
   - LLM 生成策略代码（最多 2 次重试修复编译错误）
   - 编译验证（`dotnet build`）
   - 冒烟测试（1 天回测）
   - 失败标记 `.cs.broken` / `.smoke-failed`
5. 记录 PID，立即返回

---

#### 11. `optimize_backtests`

| 属性 | 值 |
|------|-----|
| Task 名 | `optimize-backtests` |
| 重试 | 0 次 |
| 超时 | 60 秒 |
| 间隔 | 30 分钟 |

**功能：** 启动策略回测优化后台子进程。对已通过冒烟测试的策略执行完整回测，评分并更新注册表。

**执行逻辑：**
1. 检查 30 分钟间隔门控
2. 检查后台进程是否已在运行
3. 启动子进程：
   ```
   soloquant_orchestrator.py --optimize-strategies --max-items 1
   ```
4. 子进程内部流程：
   - 对策略的 3 个变体执行完整回测
   - 计算评分（Sharpe、收益、回撤等）
   - 更新 `strategy-registry.json` 中的评分和状态
5. 记录 PID，立即返回

---

### Category C：Live-Paper 与生命周期管理（哨兵模式）

---

#### 12. `prepare_live_market_data`

| 属性 | 值 |
|------|-----|
| Task 名 | `prepare-live-market-data` |
| 重试 | 2 次 |
| 重试延迟 | 30 秒 |
| 超时 | 300 秒 |

**功能：** 准备共享市场数据快照，供所有 live-paper LEAN 进程使用。确保 LEAN 进程启动前所需的行情数据已就绪。

**执行逻辑：**
1. 调用 `DefaultPipelineServices.prepare_live_market_data()`
2. 检查/准备 Tushare 实时数据
3. 生成市场数据快照文件

**注意：** 此 task 接收 `now` 参数（`datetime`），而非 `run_date`。

---

#### 13. `update_lifecycle`

| 属性 | 值 |
|------|-----|
| Task 名 | `update-lifecycle` |
| 重试 | 1 次 |
| 重试延迟 | 30 秒 |
| 超时 | 120 秒 |

**功能：** 更新策略生命周期状态（candidate → serving → retired），并将生命周期数据导出到 InfluxDB。

**执行逻辑：**
1. 调用 `DefaultPipelineServices.update_lifecycle()`
2. 评估所有策略的近期表现：
   - **candidate**：已通过冒烟测试，等待回测评分
   - **serving（服役中）**：评分达标，正在 live-paper 运行
   - **retired（已除役）**：性能退化超过 25% 阈值
3. 更新 `strategy-registry.json` 中的 lifecycle 字段
4. 导出生命周期变更到 InfluxDB

---

#### 14. `run_live_paper`

| 属性 | 值 |
|------|-----|
| Task 名 | `run-live-paper` |
| 重试 | 0 次 |
| 超时 | 120 秒 |

**功能：** 哨兵模式 — 确保所有注册的 live-paper 策略 LEAN 进程处于运行状态。启动未运行的进程，跳过已运行的进程。

**执行逻辑：**
1. 调用 `DefaultPipelineServices.run_live_paper()`
2. 扫描 `live-paper-processes/*.pid.json`
3. 对每个已注册但未运行的策略：
   - 读取 LEAN 配置路径
   - 启动 `dotnet QuantConnect.Lean.Launcher.dll --config <config>`
   - 更新 PID 文件
4. 已运行的进程跳过
5. LEAN 进程在后台持续运行（不等待完成）

---

### Category D：监控任务

---

#### 15. `monitor_background_jobs`

| 属性 | 值 |
|------|-----|
| Task 名 | `monitor-background-jobs` |
| 重试 | 0 次 |
| 超时 | 30 秒 |

**功能：** 检查所有 LLM 后台任务的运行状态，生成汇总报告。在所有后台 stage dispatch 完成后执行。

**执行逻辑：**
1. 扫描 `llm-background/*.pid.json`
2. 对每个后台任务：
   - 读取 PID
   - 检查进程是否存活
3. 返回汇总：`{"jobs": {"stage_name": {"pid": N, "alive": true/false}, ...}}`

---

## Task 执行顺序

每次 tick 按 `PIPELINE_STAGES` 定义的顺序依次执行：

```
1.  ingest_local_strategies    — 同步，1h 间隔
2.  data_driven_crawl          — 哨兵，8h 间隔
3.  crawl_research             — 哨兵，8h 间隔
4.  prepare_reproduction       — 哨兵，按需
5.  prepare_iv_data            — 同步，按需
6.  build_event_graph          — 同步，按需
7.  build_event_signals        — 同步，按需
8.  reproduce_one              — 哨兵，30m 间隔
9.  materialize_variants       — 同步，按需
10. optimize_backtests         — 哨兵，30m 间隔
11. prepare_live_market_data   — 同步，按需
12. update_lifecycle           — 同步，按需
13. run_live_paper             — 哨兵，按需
14. export_influx              — 同步，按需
15. monitor_background_jobs    — 监控，tick 末尾
```

## 降级与错误处理

- **DEGRADED_ON_FAIL** 集合中的 stage 失败时，tick 状态标记为 `degraded`，继续执行后续 stage：
  `ingest_local_strategies`, `crawl_research`, `prepare_reproduction`, `prepare_iv_data`, `build_event_graph`, `build_event_signals`, `reproduce_one`, `optimize_backtests`, `export_influx`

- **非 DEGRADED_ON_FAIL 且非后台** 的 stage 失败时，tick 状态标记为 `error`，中断后续 stage 执行

- **后台 stage**（LLM_BACKGROUND_STAGES）失败不影响 tick 流程，因为它们是异步子进程

## InfluxDB 指标导出

每次 tick 完成后，Flow 自动调用 `soloquant_prefect_influx_export.py` 导出两个 InfluxDB measurement：

| Measurement | 粒度 | 内容 |
|-------------|------|------|
| `soloquant_prefect_tick` | 每次 tick | 整体状态、耗时、stage 统计（ok/error/skipped/degraded 数量） |
| `soloquant_prefect_stage` | 每个 stage | 单个 stage 状态、耗时、模式（sync/background）、PID |

这些指标与现有的 `soloquant_pipeline_funnel`、`soloquant_strategy_progress` 等测量互补，不会覆盖。

## 运维命令

```bash
# 启动 Prefect 全套（server + worker + flow）
./sqctl.sh start prefect

# 停止 Prefect
./sqctl.sh stop prefect

# 查看状态
./sqctl.sh status prefect

# 手动触发一次 tick
conda run -n quant311 Scripts/soloquant_prefect_flow.py --run-once

# 强制执行（忽略间隔门控）
conda run -n quant311 Scripts/soloquant_prefect_flow.py --run-once --force

# 查看 Prefect 日志
./sqctl.sh logs prefect
./sqctl.sh logs prefect-flow 50
```
