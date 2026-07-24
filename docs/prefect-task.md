# SoloQuant Prefect Pipeline 任务清单

## 概述

SoloQuant pipeline 通过 Prefect 编排 14 个顺序执行的阶段 + 1 个监控任务，每次 tick（默认 5 分钟）依次执行。整体 flow 超时 600 秒，0 次重试。

**源文件**：
- `Scripts/soloquant_prefect_tasks.py` — 任务定义
- `Scripts/soloquant_prefect_config.py` — 阶段排序、配置、间隔
- `Scripts/soloquant_prefect_flow.py` — Flow 编排
- `Scripts/soloquant_orchestrator.py` — 核心功能逻辑

**任务分类**：
- **A 类（同步有限）**：在 tick 内同步完成，有明确超时
- **B 类（LLM 后台哨兵）**：启动后台子进程后立即返回，实际工作在进程外进行
- **C 类（Live Paper 管理）**：市场数据准备、生命周期管理、live paper 启动

---

## A 类：同步有限阶段

### 1. ingest-local-strategies — 本地策略导入

| 属性 | 值 |
|------|-----|
| 间隔 | 1 小时（`local-ingest-interval-seconds`，默认 3600） |
| 超时 | 300 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 是 |

**功能**：扫描 `local-strategies/` 目录中的新文件（.pdf、.md、.txt、.json）。通过文件名+mtime 哈希在 `.ingested.json` 中跟踪已导入文件，避免重复处理。PDF 自动提取文本；含 `core_idea` 或 `signals` 字段的 JSON 直接路由到复现摘要；其他内容进入正常爬取流水线。过短或过大的内容被跳过。

**输出**：
- `artifacts/crawled/strategy/` — 新的爬取条目 JSON
- `artifacts/reproduction/strategy/` — 新的复现摘要 JSON
- `.ingested.json` — 更新的跟踪文件

**依赖**：无（流水线第一阶段）

---

### 5. prepare-iv-data — 隐含波动率数据准备

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 900 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 是 |

**功能**：运行 `export_ashare_implied_volatility_data.py` 子进程，从 Tushare 拉取隐含波动率数据并写入 LEAN 数据目录。接受 `--start-date` 参数（来自 run_date）。

**输出**：
- `Data/alternative/ashare-implied-volatility/` — IV 数据文件

**依赖**：无直接依赖，逻辑上为下游策略提供 IV 数据

---

### 6. build-event-graph — 金融事件图谱构建

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 600 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 是 |

**功能**：读取 `artifacts/intelligence-analysis/finance_intelligence/` 中的金融情报分析文件。对每个质量达标的分析，构建知识图谱，包含三类节点（event、entity、source）和四种边（SOURCE_REPORTED_EVENT、EVENT_IMPACTS_ENTITY、EVENT_HAS_THEME、EVENT_RELATED_TO_EVENT）。共享实体或主题的事件通过 EVENT_RELATED_TO_EVENT 边连接，带有关系强度评分。实体通过 `detect_finance_entities()` 检测，主题通过 `detect_finance_themes()` 识别。

**输出**：
- `artifacts/event-graph/finance_intelligence/graph.json` — 包含节点、边和元数据的图谱

**依赖**：消费 `analyze_finance_intelligence`（prepare-reproduction 的子阶段）的输出

---

### 7. build-event-signals — 事件信号生成

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 600 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 是 |

**功能**：将金融事件图谱转换为策略可消费的信号上下文。读取 build-event-graph 构建的图谱，按事件类型分组信号，计算复合风险水平，识别受影响行业。按日期持久化结果。

**输出**：
- `artifacts/event-signals/YYYYMMDD/signal-context.json` — 信号上下文文件

**依赖**：依赖 build-event-graph 的 graph.json 输出

---

### 9. materialize-variants — 策略变体实例化

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 900 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 否（硬错误，中断 tick） |

**功能**：运行 `--materialize-generated-strategies`。对每个生成的策略清单文件（`Algorithm.CSharp/SoloQuantGenerated/*/manifest.json` 或 `Algorithm.Python/SoloQuantGenerated/*/manifest.json`），创建参数变体目录（baseline、learned-v1、learned-v2），每个变体包含完整的 LEAN 回测和 live-paper 配置文件。变体调整 lookback-period 和 max-positions。回测配置包含蒙特卡洛设置（500 次试验、63 天窗口、块大小 5）。Live-paper 配置包含共享市场数据快照路径。

**输出**：
- 策略变体目录，每个包含 LEAN 回测和 live-paper 配置 JSON 文件

**依赖**：依赖 reproduce-one 生成的策略实现

---

### 14. export-influx — InfluxDB 数据导出

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 600 秒 |
| 重试 | 1 次，延迟 60 秒 |
| 失败降级 | 是 |

**功能**：依次运行 5 个 InfluxDB 导出子操作：
1. `--export-crawled-ideas-influx` — 导出爬取的策略想法
2. `--export-research-influx` — 导出研究数据
3. `--export-finance-event-graph-influx` — 导出金融事件图谱
4. `--export-strategy-results-influx` — 导出策略回测结果
5. `--export-strategy-pipeline` — 导出 pipeline 运行指标

全部 5 个子操作必须成功，阶段才报告 "ok"。

**输出**：
- 写入 InfluxDB 的时序指标数据（供 Grafana 可视化）

**依赖**：逻辑上依赖所有前置阶段产出的数据

---

## B 类：LLM 后台哨兵阶段

所有 B 类任务遵循相同的哨兵模式：通过 PID 文件检查是否已在运行 → 清理已完成进程 → 启动后台子进程 → 立即返回 PID。**不等待子进程完成**。

### 2. data-driven-crawl — 数据驱动爬取

| 属性 | 值 |
|------|-----|
| 间隔 | 8 小时（`data-driven-crawl-interval-seconds`，默认 28800） |
| 超时 | 60 秒 |
| 重试 | 0 次 |
| 失败处理 | 后台阶段，不中断 tick |

**功能**：启动 `soloquant_crawl_scheduler.py --task data_driven_strategy --force --once`。执行数据感知的 Web 搜索，使用 LLM 筛选找到基于当前市场数据和 IV 曲面的策略想法。可配置 `crawl-max-queries-per-task`（默认 4）和 `crawl-max-results-per-query`（默认 2）。

**输出**：
- `llm-background/data_driven_crawl.pid.json` — PID 跟踪文件
- 后台进程写入爬取条目到 artifact 存储

**依赖**：无

---

### 3. crawl-research — 学术研究爬取

| 属性 | 值 |
|------|-----|
| 间隔 | 8 小时（`crawl-interval-seconds`，默认 28800） |
| 超时 | 60 秒 |
| 重试 | 0 次 |
| 失败处理 | 后台阶段，不中断 tick |

**功能**：启动 `soloquant_crawl_scheduler.py --task strategy --task finance_intelligence --force --once`。执行两类爬取任务：
1. **strategy** — Web 搜索和爬取学术/商业化量化策略内容
2. **finance_intelligence** — Web 搜索金融新闻、监管公告、市场情报

**输出**：
- `llm-background/crawl_research.pid.json` — PID 跟踪文件
- 后台进程写入爬取条目和金融情报条目到 artifact 存储

**依赖**：无

---

### 4. prepare-reproduction — 策略复现准备

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 60 秒 |
| 重试 | 0 次 |
| 失败处理 | 后台阶段，不中断 tick |

**功能**：启动**两个**后台子进程：
1. **`--prepare-reproduction`** — 读取爬取的策略条目，下载/提取 PDF，将内容发送给 LLM（GLM）生成结构化复现摘要（核心思想、信号、参数、风险管理），写入 `artifacts/reproduction/strategy/`
2. **`--analyze-finance-intelligence`** — 读取爬取的金融情报条目，评估质量，将达标内容发送给 LLM 分析（事件类型、要点、影响、风险水平），写入 `artifacts/intelligence-analysis/finance_intelligence/`

每个子阶段每 tick 最多处理 1 条（`--max-items 1`），各自有独立的 PID 文件。

**输出**：
- `llm-background/prepare_reproduction.pid.json`
- `llm-background/analyze_finance_intelligence.pid.json`
- 复现摘要 JSON 文件和情报分析 JSON 文件

**依赖**：消费 crawl-research 和 data-driven-crawl 产出的爬取条目

---

### 8. reproduce-one — 策略实现（代码生成）

| 属性 | 值 |
|------|-----|
| 间隔 | 30 分钟（`reproduce-interval-seconds`，默认 1800） |
| 超时 | 60 秒 |
| 重试 | 0 次 |
| 失败处理 | 后台阶段，不中断 tick |

**功能**：启动 `soloquant_orchestrator.py --generate-strategy-implementations --max-items 1 --smoke-test-strategies`。编排器读取复现摘要，发送给 LLM（GLM）生成算法代码（CSharp 或 Python），写入 `Algorithm.CSharp/SoloQuantGenerated/` 或 `Algorithm.Python/SoloQuantGenerated/`，编译项目（CSharp 用 dotnet build，Python 用语法检查），然后运行冒烟测试（最小配置的 LEAN 回测）。损坏的 .cs 文件被重命名为 .cs.broken。每 tick 只处理 1 条策略。

**这是资源消耗最大的阶段**：LLM 代码生成 + dotnet 编译 + LEAN 冒烟回测，总计可能需要 10-30 分钟。

**输出**：
- `llm-background/reproduce_one.pid.json`
- 算法源文件、manifest.json、编译/冒烟测试结果

**依赖**：消费 prepare-reproduction 产出的复现摘要

---

### 10. optimize-backtests — 回测优化

| 属性 | 值 |
|------|-----|
| 间隔 | 30 分钟（`optimize-interval-seconds`，默认 1800） |
| 超时 | 60 秒 |
| 重试 | 0 次 |
| 失败处理 | 后台阶段，不中断 tick |

**功能**：启动 `soloquant_orchestrator.py --optimize-strategies --max-items 1`。读取生成的策略清单，运行 LEAN 回测并进行参数优化，寻找更优的参数组合。每 tick 只优化 1 条策略。

**这是资源消耗第二大的阶段**：多轮 LEAN 回测，每轮可能需要数分钟。

**输出**：
- `llm-background/optimize_backtests.pid.json`
- 优化结果文件

**依赖**：依赖 reproduce-one 生成的策略实现

---

## C 类：Live Paper 与生命周期管理

### 11. prepare-live-market-data — 实时市场数据准备

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 300 秒 |
| 重试 | 2 次，延迟 30 秒 |
| 失败降级 | 否（硬错误，中断 tick） |

**功能**：调用 `ashare_live_market_cache` 库为所有 live-paper LEAN 进程准备共享市场数据快照。检测市场状态（开/闭盘、交易时段），选择适当的数据源模式（开盘时用 tushare-realtime，其他时间用模拟/缓存数据）。刷新间隔 60 秒，批量大小 200，最大 50 请求/分钟，1 个工作线程。每日行情归档到 `Data/archive/ashare-live-market-daily-quotes/`。

**这是执行最慢的同步阶段**：最新 tick 耗时约 4.5 分钟。

**输出**：
- `Results/shared-live-market/ashare-live-price-snapshot.json` — 共享价格数据
- `Results/shared-live-market/ashare-live-market-report.json` — 元数据

**依赖**：无直接依赖，输出被 run-live-paper 和 update-lifecycle 消费

---

### 12. update-lifecycle — 策略生命周期更新

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 120 秒 |
| 重试 | 1 次，延迟 30 秒 |
| 失败降级 | 否（硬错误，中断 tick） |

**功能**：两个操作：
1. 调用 `prepare_registered_live_configs_for_market_data()` — 更新所有已注册 live-paper 策略配置中的共享市场数据快照路径
2. 调用 `update_strategy_lifecycle()` — 根据回测/live 表现与阈值（`serving-score-threshold` 默认 0.0，`degradation-threshold` 默认 0.25）评估每条策略，在生命周期状态间转换：**candidate → serving → retired**。同时导出生命周期数据到 InfluxDB。

**输出**：
- 更新的策略注册表文件（新的生命周期状态）
- InfluxDB 生命周期指标

**依赖**：依赖 prepare-live-market-data 产出的快照文件；依赖 optimize-backtests 和 materialize-variants 的回测结果

---

### 13. run-live-paper — Live Paper 交易启动

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick |
| 超时 | 120 秒 |
| 重试 | 0 次 |
| 失败降级 | 否（硬错误，中断 tick） |
| 哨兵模式 | 是 |

**功能**：哨兵任务。调用 `start_registered_live_paper_strategies()` 读取策略注册表，确保所有注册的 live-paper 策略都有运行中的 LEAN dotnet 进程。未运行的策略被启动为独立进程（使用对应配置文件）。已运行的策略被跳过。LEAN 进程无限期运行（模拟盘交易）。InfluxDB token 通过环境变量传递。

**输出**：
- 运行中的 LEAN dotnet 进程（每个策略一个）
- 每个策略的 PID 跟踪

**依赖**：依赖 prepare-live-market-data（快照文件）和 update-lifecycle（注册表状态）；依赖 materialize-variants 创建的 live-paper 配置文件

---

## 监控任务

### 15. monitor-background-jobs — 后台任务监控

| 属性 | 值 |
|------|-----|
| 间隔 | 每次 tick 结束后（仅当有后台阶段被调度时） |
| 超时 | 30 秒 |
| 重试 | 0 次 |
| 失败处理 | 失败被捕获并记录，不影响 tick |

**功能**：扫描 `llm-background/` 目录中所有 `*.pid.json` 文件。对每个 PID 文件，读取 PID 并检查进程是否存活（`process_alive()`）。记录每个后台任务的阶段名、PID 和存活状态。返回汇总字典。

**输出**：
- 汇总报告（`jobs` 映射每个阶段名到 `{pid, alive}`），写入 tick 报告的 `monitor` 键下
- 不写文件

**依赖**：仅在有后台阶段被调度时调用

---

## 汇总表

| # | 任务名 | 分类 | 间隔 | 超时 | 重试 | 失败降级 | 哨兵模式 | 资源消耗 |
|---|--------|------|------|------|------|----------|----------|----------|
| 1 | ingest-local-strategies | A: 同步 | 1h | 300s | 1 | 是 | 否 | 低 |
| 2 | data-driven-crawl | B: 后台 | 8h | 60s | 0 | — | 是 | 中（LLM+网络） |
| 3 | crawl-research | B: 后台 | 8h | 60s | 0 | — | 是 | 中（LLM+网络） |
| 4 | prepare-reproduction | B: 后台 | — | 60s | 0 | — | 是 | 中（LLM） |
| 5 | prepare-iv-data | A: 同步 | — | 900s | 1 | 是 | 否 | 中（Tushare API） |
| 6 | build-event-graph | A: 同步 | — | 600s | 1 | 是 | 否 | 低 |
| 7 | build-event-signals | A: 同步 | — | 600s | 1 | 是 | 否 | 低 |
| 8 | reproduce-one | B: 后台 | 30m | 60s | 0 | — | 是 | **极高**（LLM+编译+回测） |
| 9 | materialize-variants | A: 同步 | — | 900s | 1 | 否 | 否 | 中（文件生成） |
| 10 | optimize-backtests | B: 后台 | 30m | 60s | 0 | — | 是 | **高**（多轮回测） |
| 11 | prepare-live-market-data | C: LP | — | 300s | 2 | 否 | 否 | **高**（~4.5min/tick） |
| 12 | update-lifecycle | C: LP | — | 120s | 1 | 否 | 否 | 低 |
| 13 | run-live-paper | C: LP | — | 120s | 0 | 否 | 是 | 中（启动进程） |
| 14 | export-influx | A: 同步 | — | 600s | 1 | 是 | 否 | 低（网络写入） |
| 15 | monitor-background-jobs | 监控 | 后置 | 30s | 0 | — | 否 | 极低 |

## 执行流程图

```
每次 tick（默认 5 分钟间隔）
│
├── 1. ingest-local-strategies ──── 每小时
├── 2. data-driven-crawl ───────── 每 8 小时 [后台哨兵]
├── 3. crawl-research ──────────── 每 8 小时 [后台哨兵]
├── 4. prepare-reproduction ────── 每次 tick [后台哨兵，双进程]
├── 5. prepare-iv-data ─────────── 每次 tick
├── 6. build-event-graph ───────── 每次 tick
├── 7. build-event-signals ─────── 每次 tick
├── 8. reproduce-one ───────────── 每 30 分钟 [后台哨兵] ⚡ 资源最高
├── 9. materialize-variants ─────── 每次 tick [硬错误]
├── 10. optimize-backtests ─────── 每 30 分钟 [后台哨兵] ⚡ 资源次高
├── 11. prepare-live-market-data ─ 每次 tick [硬错误，~4.5min]
├── 12. update-lifecycle ───────── 每次 tick [硬错误]
├── 13. run-live-paper ─────────── 每次 tick [哨兵]
├── 14. export-influx ──────────── 每次 tick
│
└── 15. monitor-background-jobs ── 后置（仅当有后台任务时）
```

## 错误传播规则

- **DEGRADED_ON_FAIL（9 个阶段）**：失败后 tick 继续执行，状态标记为 "degraded"
  - ingest-local-strategies, data-driven-crawl, crawl-research, prepare-reproduction, prepare-iv-data, build-event-graph, build-event-signals, reproduce-one, optimize-backtests, export-influx
- **硬错误（4 个阶段）**：失败后 tick 中断
  - materialize-variants, prepare-live-market-data, update-lifecycle, run-live-paper
- **后台阶段**：哨兵任务本身几乎不会失败（只启动进程），实际工作在进程外进行

## 关键架构说明

**哨兵模式**：B 类任务和 run-live-paper 使用哨兵模式——启动子进程后立即返回。Prefect 任务本身在几秒内完成，实际工作运行数分钟到数小时。通过 `llm-background/*.pid.json` 中的 PID 文件跨 tick 跟踪进程。每次启动前检查进程是否已在运行。

**间隔门控**：5 个阶段有专用间隔门控，检查 `PipelineState`（存储在 `soloquant-pipeline-state.json`）：ingest-local-strategies（1h）、data-driven-crawl（8h）、crawl-research（8h）、reproduce-one（30m）、optimize-backtests（30m）。可通过 `--force` 覆盖。

**Tick 后操作**：14 个阶段完成后，flow 还会：
1. 调用 `monitor_background_jobs`（如有后台阶段被调度）
2. 在 PipelineState 中记录 tick
3. 写入 `prefect-task-index.json` 和 `prefect-tick-history.json`（最近 50 次 tick）供 sqctl.sh/Grafana 使用
4. 导出 tick 指标到 InfluxDB
