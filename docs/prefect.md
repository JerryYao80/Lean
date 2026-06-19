# SoloQuant Prefect Pipeline 可视化任务管理

## 概述

SoloQuant pipeline 已从原始 daemon 模式迁移到 Prefect 编排，实现：

- **Phase 0**: Prefect server + worker 安装部署 ✅
- **Phase 1**: Thin wrapper — Prefect flow 包裹现有 tick ✅
- **Phase 2**: 将 tick 分解为 14 个独立 Prefect @task ✅
- **Phase 3**: Prefect 状态 → InfluxDB + Grafana 仪表盘 ✅
- **Phase 4**: sqctl.sh Prefect 集成 ✅

**关键原则**：现有回测结果、live paper 时序数据、bridge 仪表盘完全不受影响。新的 `soloquant_prefect_*` measurement 是纯增量，不会覆盖或修改任何已有数据。

---

## 架构

### 执行路径

系统有两条并行执行路径，共享相同的 stage 逻辑：

1. **Daemon 模式** — `soloquant_pipeline_runner.py --daemon`，Python `while True` + `time.sleep(poll_seconds)` 循环
2. **Prefect 模式** — `soloquant_prefect_flow.py --deploy`，Prefect 3.x `flow.serve()` 定时调度

两条路径最终调用相同的 `DefaultPipelineServices` stage 方法、相同的 PID 文件管理、相同的 PipelineState。

### 文件结构

```
Scripts/
├── soloquant_prefect_config.py          # 集中配置：Prefect API、路径、14个 stage 名
├── soloquant_prefect_flow.py            # Prefect @flow：分解式 pipeline tick
├── soloquant_prefect_tasks.py           # 14 个独立 @task（含 retries/timeouts）
├── soloquant_prefect_influx_export.py   # Prefect → InfluxDB 导出
├── soloquant_pipeline_runner.py         # 原始 daemon 逻辑 + DefaultPipelineServices
└── soloquant_orchestrator.py            # 共享后端（6782 行）
```

### 14 个 Pipeline Stages

| # | Stage 名 | 类别 | Prefect Task 特性 |
|---|---------|------|------------------|
| 1 | `ingest_local_strategies` | 同步 | retries=1, timeout=300s, interval=1h |
| 2 | `data_driven_crawl` | 后台哨兵 | retries=0, timeout=60s, interval=8h |
| 3 | `crawl_research` | 后台哨兵 | retries=0, timeout=60s, interval=8h |
| 4 | `prepare_reproduction` | 后台哨兵 | retries=0, timeout=60s |
| 5 | `prepare_iv_data` | 同步 | retries=1, timeout=900s |
| 6 | `build_event_graph` | 同步 | retries=1, timeout=600s |
| 7 | `build_event_signals` | 同步 | retries=1, timeout=600s |
| 8 | `reproduce_one` | 后台哨兵 | retries=0, timeout=60s, interval=30m |
| 9 | `materialize_variants` | 同步 | retries=1, timeout=900s |
| 10 | `optimize_backtests` | 后台哨兵 | retries=0, timeout=60s, interval=30m |
| 11 | `prepare_live_market_data` | 同步 | retries=2, timeout=300s |
| 12 | `update_lifecycle` | 同步 | retries=1, timeout=120s |
| 13 | `run_live_paper` | 哨兵 | retries=0, timeout=120s |
| 14 | `export_influx` | 同步 | retries=1, timeout=600s |

**后台哨兵模式**：launch subprocess → return immediately → PID file 跟踪，不阻塞 flow。

### STAGE_DISPATCH 映射

`soloquant_prefect_flow.py` 中的 `STAGE_DISPATCH` 字典将每个 stage 名映射到对应的 `@task` 函数和参数签名：

```python
STAGE_DISPATCH = {
    "ingest_local_strategies":    (ingest_local_strategies,    needs_run_date=True,  needs_now=False),
    "data_driven_crawl":          (data_driven_crawl,          needs_run_date=True,  needs_now=False),
    ...
    "prepare_live_market_data":   (prepare_live_market_data,   needs_run_date=False, needs_now=True),
    "update_lifecycle":           (update_lifecycle,           needs_run_date=False, needs_now=False),
    "run_live_paper":             (run_live_paper,             needs_run_date=False, needs_now=False),
    ...
}
```

### 错误处理

与原始 `run_pipeline_tick()` 一致：

- `DEGRADED_ON_FAIL` 中的 stage 非	ok → 标记 `degraded`，继续下一个 stage
- 其他非后台 stage 非 ok → 标记 `error`，中断 tick
- 后台 stage 非_ok → 继续执行（因为哨兵模式只负责 launch，不等待结果）

---

## InfluxDB Measurements

### soloquant_prefect_tick

每次 tick 的汇总指标：

| Field | 类型 | 说明 |
|-------|------|------|
| `status_code` | int | 0=skipped, 1=ok, 2=degraded, 3=error |
| `status_text` | string | 状态文字 |
| `duration_seconds` | float | tick 总耗时 |
| `total_stages` | int | 总 stage 数 |
| `ok_stages` | int | 成功 stage 数 |
| `error_stages` | int | 失败 stage 数 |
| `skipped_stages` | int | 跳过 stage 数 |
| `degraded_stages` | int | 降级 stage 数 |

### soloquant_prefect_stage

每个 stage 的详细指标：

| Tag | 说明 |
|-----|------|
| `stage` | stage 名称 |
| `mode` | sync / background |

| Field | 类型 | 说明 |
|-------|------|------|
| `status_code` | int | 0=skipped, 1=ok, 3=error |
| `status_text` | string | 状态文字 |
| `duration_seconds` | float | stage 耗时 |
| `mode` | string | sync / background |
| `pid` | int | 后台进程 PID（仅 background mode） |

---

## Grafana 仪表盘

**Dashboard**: SoloQuant Prefect Pipeline (`uid: soloquant-prefect-pipeline`)

**文件**: `monitoring/grafana/dashboards/lean/soloquant-prefect-pipeline.json`

9 个面板：

| # | 面板 | 类型 | 说明 |
|---|------|------|------|
| 1 | Pipeline Tick Status | stat | 色彩映射状态指示器（OK/跳过/降级/错误） |
| 2 | Last Tick Duration | stat | tick 耗时（秒），超 300s 黄色，超 480s 红色 |
| 3 | Stage Summary | stat | ok/error/skipped/degraded stage 计数 |
| 4 | Last Tick Time | stat | 最后执行时间戳 |
| 5 | Tick History | timeseries (bars) | tick 耗时时间序列柱状图 |
| 6 | Tick Status Over Time | timeseries (points) | tick 状态散点图 |
| 7 | Stage Duration Breakdown | barchart (horizontal) | 各 stage 耗时横向柱状图 |
| 8 | Stage Status Table | table | stage 状态表格（颜色编码、耗时、模式、PID） |
| 9 | Stage Duration Time Series | timeseries (line) | 各 stage 耗时多线图 |

---

## sqctl.sh Prefect 集成

### 新增 `prefect` 组

```bash
./sqctl.sh status prefect    # 查看 server/worker/flow 状态 + 上次 tick 结果
./sqctl.sh start prefect     # 启动 server → worker → flow
./sqctl.sh stop prefect      # 停止 flow → worker → server
./sqctl.sh restart prefect   # 重启
./sqctl.sh logs prefect      # 查看所有 Prefect 日志
```

### 细分日志组

```bash
./sqctl.sh logs prefect-server    # Server 日志
./sqctl.sh logs prefect-worker    # Worker 日志
./sqctl.sh logs prefect-flow      # Flow 部署日志
```

### Summary 输出

```bash
./sqctl.sh summary
# ● SoloQuant: 9 processes running  (prefect:2/3  core:0/2  livepaper:7  ...)
```

`prefect:N/3` 表示 3 个组件（server/worker/flow）中有 N 个运行中。

---

## 功能验证步骤

### 第一步：验证 Prefect 分解流程（Phase 2）

```bash
cd /home/project/hope/Lean
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_flow.py \
  --config Launcher/config/config-soloquant.json \
  --run-once --force
```

**预期结果：**
- 输出 JSON report，`"status": "ok"` 或 `"degraded"`
- `"stages"` 列表包含 14 个条目，每个都有 `started_at_utc` / `finished_at_utc`
- Prefect UI（`http://localhost:4200`）能看到这次 flow run
- 点击 flow run 进入详情，能看到 `ingest-local-strategies`、`data-driven-crawl` 等独立 task 卡片
- 如果某 stage 被 interval 跳过，它的状态显示 `skipped` 而不是报错

### 第二步：验证 InfluxDB 写入 + Grafana 仪表盘（Phase 3）

```bash
INFLUX_TOKEN=$(python3 -c "import json; print(json.load(open('Launcher/config/config-soloquant.json')).get('influxdb',{}).get('token-default',''))")

# 查看 tick 数据
curl -s "http://localhost:8086/query?db=quant" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  --data-urlencode 'q=SELECT * FROM soloquant_prefect_tick ORDER BY time DESC LIMIT 3' | python3 -m json.tool

# 查看 stage 数据
curl -s "http://localhost:8086/query?db=quant" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  --data-urlencode 'q=SELECT * FROM soloquant_prefect_stage ORDER BY time DESC LIMIT 5' | python3 -m json.tool
```

也可以用 `--from-index` 方式导出（sqctl.sh cron 调用路径）：

```bash
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_influx_export.py \
  --config Launcher/config/config-soloquant.json --from-index
```

打开 Grafana → Dashboards → **SoloQuant Prefect Pipeline**：
- 顶部 stat 面板应显示 Tick Status（绿/黄/红）、Duration、Stage Summary
- 中间应有 Tick History 条形图、Stage Duration Breakdown 横向柱状图
- 底部 Stage Status Table 应列出每个 stage 的状态、耗时、模式

### 第三步：验证 sqctl.sh Prefect 管理（Phase 4）

```bash
# 查看 Prefect 组件状态
./sqctl.sh status prefect

# 查看 Prefect 日志
./sqctl.sh logs prefect 20

# 一行概览（应显示 prefect:N/3）
./sqctl.sh summary
```

### 完整生命周期测试

```bash
# 1. 停掉 Prefect
./sqctl.sh stop prefect

# 2. 确认已停
./sqctl.sh status prefect

# 3. 重新启动
./sqctl.sh start prefect

# 4. 等待 5 秒确认健康
sleep 5
./sqctl.sh status prefect

# 5. 等 5~6 分钟后看是否有自动 tick（prefect-flow --deploy 默认 300s 间隔）
sleep 320
./sqctl.sh logs prefect-flow 30
```

### 验证检查清单

| 检查项 | 命令 | 预期 |
|--------|------|------|
| 单次 tick 执行 | `--run-once --force` | 14 stage 各有状态 |
| Prefect UI 可视化 | 浏览器 `:4200` | 每个 stage 是独立 task |
| InfluxDB tick 数据 | InfluxQL 查询 | `soloquant_prefect_tick` 有行 |
| InfluxDB stage 数据 | InfluxQL 查询 | `soloquant_prefect_stage` 有行 |
| Grafana 仪表盘 | 浏览器 Grafana | 9 面板有数据 |
| sqctl Prefect 状态 | `./sqctl.sh status prefect` | server/worker/flow 三个 `●` |
| sqctl 启停 | `start/stop prefect` | 进程正确启停 |
| 自动调度 | 等 5 分钟看日志 | 有 tick 自动执行 |
| 现有功能不受影响 | `./sqctl.sh status all` | livepaper/bridge 等一切如常 |

---

## 与现有系统的兼容性

### 不受影响的部分

- **回测时序数据**：`lean_chart`、`lean_portfolio`、`lean_metric` 等 measurement 不变
- **Live paper 时序数据**：bridge 写入的 `barra_cne5_*`、`lean_holding` 等 measurement 不变
- **策略生命周期**：`strategy_lifecycle` measurement 不变，`update_lifecycle` stage 逻辑完全复用
- **Pipeline funnel**：`soloquant_pipeline_funnel`、`soloquant_pipeline_funnel_stages` 不变
- **PID 文件**：`live-paper-processes/*.pid.json`、`llm-background/*.pid.json` 不变
- **PipelineState**：`soloquant-pipeline-state.json` 不变，daemon 和 Prefect 两种模式共享
- **sqctl.sh**：`core`、`livepaper`、`legacy`、`bridge`、`tushare`、`llm` 组不变

### 新增的部分

- `soloquant_prefect_tick` / `soloquant_prefect_stage` — 两个新 InfluxDB measurement
- `prefect-task-index.json` / `prefect-tick-history.json` — 两个新状态文件
- `soloquant-prefect-pipeline` — 一个新 Grafana 仪表盘
- `prefect` sqctl.sh 组 — 新的进程管理组

### 两种模式可并存

Daemon 模式和 Prefect 模式**不应该同时运行**（会重复执行 tick），但可以随时切换：

```bash
# 从 daemon 切换到 Prefect
./sqctl.sh stop core
./sqctl.sh start prefect

# 从 Prefect 切回 daemon
./sqctl.sh stop prefect
./sqctl.sh start core
```

两者共享同一个 `soloquant-pipeline-state.json`，interval-based skip 逻辑一致，不会重复执行。
