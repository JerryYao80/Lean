# SoloQuant Pipeline 操作手册（Prefect 版）

## 1. 概述

SoloQuant pipeline 通过 Prefect 3.x 实现可视化任务编排。每个 pipeline stage 作为独立 Prefect @task 运行，具备：

- **Prefect UI** 可视化：每个 stage 独立卡片，状态一目了然
- **自动重试 / 超时**：每个 @task 有独立的 retries 和 timeout_seconds
- **Grafana 仪表盘**：tick 健康、stage 耗时、任务历史实时展示
- **sqctl.sh 一键管理**：`prefect` 组统一启停 server / worker / flow

**关键原则**：回测结果、live paper 时序数据、bridge 仪表盘完全不受影响。新增的 `soloquant_prefect_*` measurement 是纯增量。

---

## 2. 架构

### 2.1 两种运行模式

| 模式 | 启动方式 | 调度机制 | 适用场景 |
|------|---------|---------|---------|
| **Prefect** | `./sqctl.sh start prefect` | Prefect 3.x `flow.serve()` 定时调度 | 日常运行，需要可视化 |
| **Daemon** | `./sqctl.sh start core` | Python `while True` + `time.sleep()` | 无 Prefect 依赖的轻量运行 |

两种模式共享相同的 `DefaultPipelineServices` stage 逻辑、PID 文件管理、`soloquant-pipeline-state.json` 状态文件。**不应同时运行**（会重复执行 tick）。

### 2.2 Prefect 三组件

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐
│ Prefect      │     │ Prefect      │     │ Prefect Flow    │
│ Server       │────▶│ Worker       │────▶│ (scheduled)     │
│ :4200        │     │ soloquant-   │     │ soloquant_      │
│ (API + UI)   │     │ pool         │     │ pipeline_tick   │
└─────────────┘     └─────────────┘     └─────────────────┘
     ▲                    ▲                     │
     │                    │                     ▼
     │                    │           ┌─────────────────┐
     │                    │           │ 14 个 @task      │
     │                    │           │ ingest / crawl / │
     │                    │           │ reproduce / ...  │
     └────────────────────┴───────────┴─────────────────┘
         InfluxDB + PID files + PipelineState
```

| 组件 | 作用 | 端口/路径 |
|------|------|----------|
| Prefect Server | API + UI，存储 flow run 历史 | `0.0.0.0:4200` |
| Prefect Worker | 从 work pool 领取并执行 flow run | — |
| Prefect Flow | `soloquant_pipeline_tick.serve()` 定时触发 | 300s 间隔 |

### 2.3 文件结构

```
Scripts/
├── soloquant_prefect_config.py          # 集中配置（API URL、路径、14 个 stage 名）
├── soloquant_prefect_flow.py            # @flow：分解式 pipeline tick
├── soloquant_prefect_tasks.py           # 14 个独立 @task（retries/timeouts）
├── soloquant_prefect_influx_export.py   # Prefect → InfluxDB 导出
├── soloquant_pipeline_runner.py         # 原始 daemon 逻辑 + DefaultPipelineServices
└── soloquant_orchestrator.py            # 共享后端

Results/soloquant/
├── prefect-task-index.json              # 最近一次 tick 的 stage 状态快照
├── prefect-tick-history.json            # 最近 50 次 tick 历史
├── prefect-server.log                   # Server 日志
├── prefect-worker.log                   # Worker 日志
├── prefect-flow.log                     # Flow 部署日志
├── soloquant-pipeline-state.json        # PipelineState（daemon/Prefect 共享）
├── live-paper-processes/                # LEAN 进程 PID 文件
└── llm-background/                      # LLM 后台任务 PID 文件

monitoring/grafana/dashboards/lean/
└── soloquant-prefect-pipeline.json      # Grafana 仪表盘定义
```

### 2.4 14 个 Pipeline Stages

| # | Stage 名 | 类别 | retries | timeout | interval |
|---|---------|------|---------|---------|----------|
| 1 | `ingest_local_strategies` | 同步 | 1 | 300s | 1h |
| 2 | `data_driven_crawl` | 后台哨兵 | 0 | 60s | 8h |
| 3 | `crawl_research` | 后台哨兵 | 0 | 60s | 8h |
| 4 | `prepare_reproduction` | 后台哨兵 | 0 | 60s | — |
| 5 | `prepare_iv_data` | 同步 | 1 | 900s | — |
| 6 | `build_event_graph` | 同步 | 1 | 600s | — |
| 7 | `build_event_signals` | 同步 | 1 | 600s | — |
| 8 | `reproduce_one` | 后台哨兵 | 0 | 60s | 30m |
| 9 | `materialize_variants` | 同步 | 1 | 900s | — |
| 10 | `optimize_backtests` | 后台哨兵 | 0 | 60s | 30m |
| 11 | `prepare_live_market_data` | 同步 | 2 | 300s | — |
| 12 | `update_lifecycle` | 同步 | 1 | 120s | — |
| 13 | `run_live_paper` | 哨兵 | 0 | 120s | — |
| 14 | `export_influx` | 同步 | 1 | 600s | — |

**后台哨兵模式**：launch subprocess → return immediately → PID file 跟踪。不阻塞 flow，由 `monitor_background_jobs` task 统一检查状态。

### 2.5 错误处理

| 情况 | 行为 |
|------|------|
| `DEGRADED_ON_FAIL` 中的 stage 失败 | 标记 `degraded`，继续下一个 stage |
| 非 `DEGRADED_ON_FAIL`、非后台 stage 失败 | 标记 `error`，中断 tick |
| 后台哨兵 stage 失败 | 继续执行（哨兵只负责 launch，不等待结果） |
| 所有 stage 成功 | 标记 `ok` |
| 顶层 interval 未到 | 标记 `skipped`，跳过整个 tick |

`DEGRADED_ON_FAIL` 包含：`ingest_local_strategies`、`crawl_research`、`prepare_reproduction`、`prepare_iv_data`、`build_event_graph`、`build_event_signals`、`reproduce_one`、`optimize_backtests`、`export_influx`。

---

## 3. 日常操作

### 3.1 启动 Pipeline

```bash
cd /home/project/hope/Lean

# 启动 Prefect 全套（server + worker + flow）
./sqctl.sh start prefect

# 如需同时启动 live paper 和 bridge
./sqctl.sh start all
```

`start prefect` 按顺序启动：
1. **Prefect Server** — 等待 `:4200` 端口健康后才继续
2. **Prefect Worker** — 注册到 `soloquant-pool`
3. **Prefect Flow** — `soloquant_pipeline_tick.serve()` 部署定时调度

### 3.2 停止 Pipeline

```bash
# 停止 Prefect 全套
./sqctl.sh stop prefect

# 停止所有 SoloQuant 组件
./sqctl.sh stop all
```

`stop prefect` 按顺序停止：flow → worker → server，每步等待优雅退出。

### 3.3 查看状态

```bash
# Prefect 组件状态 + 上次 tick 结果
./sqctl.sh status prefect

# 所有组件状态
./sqctl.sh status

# 一行概览
./sqctl.sh summary
```

`status prefect` 输出示例：

```
═══ Prefect Orchestration ═══
  ● prefect-server (PID 479099, up 16:20:10)
  ● prefect-worker (PID 479313, up 16:21:05)
  ● prefect-flow (PID 479500, up 15:30:00)
  ▸ last-tick: ok (2026-06-12T10:05:30+00:00)
```

### 3.4 查看日志

```bash
# 所有 Prefect 日志
./sqctl.sh logs prefect

# 细分日志
./sqctl.sh logs prefect-server    # Server 日志
./sqctl.sh logs prefect-worker    # Worker 日志
./sqctl.sh logs prefect-flow      # Flow 部署日志（含 tick 输出）

# 指定行数
./sqctl.sh logs prefect-flow 100
```

### 3.5 重启

```bash
./sqctl.sh restart prefect
```

---

## 4. 手动触发

### 4.1 单次执行（忽略 interval）

```bash
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_flow.py \
  --config Launcher/config/config-soloquant.json \
  --run-once --force
```

参数说明：

| 参数 | 说明 |
|------|------|
| `--config` | 配置文件路径，默认 `Launcher/config/config-soloquant.json` |
| `--run-once` | 执行一次后退出 |
| `--force` | 忽略顶层和各 stage 的 interval 限制 |
| `--mode debug` | 所有 stage 同步执行（不走后台哨兵模式） |
| `--run-date YYYY-MM-DD` | 指定运行日期 |

### 4.2 仅执行某个 stage

直接调用 `soloquant_orchestrator.py` 的对应 flag：

```bash
# 示例：手动触发一次策略代码生成 + 冒烟测试
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --generate-strategy-implementations --max-items 1 --smoke-test-strategies

# 示例：手动触发一次 InfluxDB 导出
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --export-strategy-results-influx
```

### 4.3 手动导出 Prefect 指标到 InfluxDB

当 Prefect flow 未运行但需要补写 Grafana 数据时：

```bash
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_influx_export.py \
  --config Launcher/config/config-soloquant.json --from-index
```

---

## 5. Prefect UI

### 5.1 访问

浏览器打开：

```
http://84.8.248.198:4200
```

> **注意**：首次访问如显示 "Can't connect to Server API at http://localhost:4200/api"，这是浏览器缓存了旧的 localhost 配置。按 `Ctrl+Shift+R` 硬刷新，或清除站点数据后重试。

### 5.2 关键页面

| 页面 | 路径 | 说明 |
|------|------|------|
| Flow Runs | `/runs` | 所有 tick 执行历史 |
| Deployments | `/deployments` | `soloquant-pipeline-scheduled` 部署详情 |
| Work Pools | `/work-pools` | `soloquant-pool` worker 状态 |
| Flow Run Detail | `/runs/{id}` | 单次 tick 的 14 个 task 状态、日志 |

### 5.3 在 UI 中手动触发

1. 进入 Deployments 页面
2. 点击 `soloquant-pipeline-scheduled`
3. 点击右上角 **Custom Run**
4. 可选修改参数（如 `force: true`）
5. 点击 **Run**

---

## 6. Grafana 仪表盘

### 6.1 访问

浏览器打开：

```
http://84.8.248.198:3000
```

进入 Dashboards → **SoloQuant Prefect Pipeline**

### 6.2 面板说明

| 面板 | 类型 | 说明 |
|------|------|------|
| Pipeline Tick Status | stat | 色彩映射状态指示器（🟢 OK / 🔵 Skipped / 🟡 Degraded / 🔴 Error） |
| Last Tick Duration | stat | tick 耗时（秒），>300s 黄色，>480s 红色 |
| Stage Summary | stat | ok/error/skipped/degraded stage 计数 |
| Last Tick Time | stat | 最后执行时间戳 |
| Tick History | timeseries | tick 耗时时间序列柱状图 |
| Tick Status Over Time | timeseries | tick 状态散点图（颜色=状态） |
| Stage Duration Breakdown | barchart | 各 stage 耗时横向柱状图 |
| Stage Status Table | table | stage 状态表格（颜色编码、耗时、模式、PID） |
| Stage Duration Time Series | timeseries | 各 stage 耗时多线图 |

### 6.3 InfluxDB Measurements

仪表盘数据来源于两个新 measurement（不影响已有 measurement）：

**`soloquant_prefect_tick`** — 每次 tick 汇总：

| Field | 说明 |
|-------|------|
| `status_code` | 0=skipped, 1=ok, 2=degraded, 3=error |
| `duration_seconds` | tick 总耗时 |
| `total_stages` / `ok_stages` / `error_stages` / `skipped_stages` / `degraded_stages` | 各状态 stage 计数 |

**`soloquant_prefect_stage`** — 每个 stage 详情：

| Tag | Field | 说明 |
|-----|-------|------|
| `stage` | — | stage 名称 |
| `mode` | — | sync / background |
| — | `status_code` | 0=skipped, 1=ok, 3=error |
| — | `duration_seconds` | stage 耗时 |
| — | `pid` | 后台进程 PID（仅 background） |

---

## 7. 模式切换

Daemon 模式和 Prefect 模式共享 `soloquant-pipeline-state.json`，可随时切换，不会重复执行 tick。

### 7.1 Daemon → Prefect

```bash
./sqctl.sh stop core
./sqctl.sh start prefect
```

### 7.2 Prefect → Daemon

```bash
./sqctl.sh stop prefect
./sqctl.sh start core
```

### 7.3 对比

| 特性 | Daemon | Prefect |
|------|--------|---------|
| 启动 | `./sqctl.sh start core` | `./sqctl.sh start prefect` |
| 调度 | `time.sleep(poll_seconds)` | Prefect `flow.serve(interval=...)` |
| Stage 可见性 | 日志文本 | Prefect UI 独立 task 卡片 |
| 重试 | 无（需手动） | 每个 @task 独立 retries |
| 超时 | 无 | 每个 @task 独立 timeout |
| Grafana | 仅原有 measurement | 额外 `soloquant_prefect_*` |
| 依赖 | 无 | Prefect server + worker |
| 资源占用 | ~50MB | ~300MB（server + worker + flow） |

---

## 8. 故障排查

### 8.1 Prefect UI 显示 "Can't connect to Server API"

**原因**：浏览器缓存了 `localhost:4200` 配置。

**解决**：
1. 硬刷新：`Ctrl+Shift+R`
2. 清除站点数据：`F12` → Application → Storage → Clear site data
3. 如果 UI 有 API URL 输入框，改为 `http://84.8.248.198:4200/api`

### 8.2 Server 无法启动（database is locked）

**原因**：旧 server 进程未完全退出，SQLite 数据库被锁定。

**解决**：
```bash
pkill -9 -f "prefect server"
sleep 3
./sqctl.sh start prefect
```

### 8.3 Worker 无法连接 Server

**原因**：`PREFECT_API_URL` 环境变量不正确。

**解决**：
```bash
# 检查 server 健康
curl -sf http://localhost:4200/api/health && echo "OK"

# 如果 server 未运行，先启动
./sqctl.sh start prefect
```

### 8.4 Flow 部署后无 tick 执行

**原因**：Worker 未运行或 work pool 名称不匹配。

**解决**：
```bash
# 检查 worker 状态
./sqctl.sh status prefect

# 检查 work pool
curl -sf http://localhost:4200/api/work_pools/soloquant-pool | python3 -m json.tool

# 重启 worker
./sqctl.sh stop prefect
./sqctl.sh start prefect
```

### 8.5 Tick 全部 skipped

**原因**：interval 未到。

**解决**：
```bash
# 强制执行一次
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_flow.py \
  --run-once --force
```

### 8.6 Grafana 仪表盘无数据

**原因**：Prefect tick 未执行过，或 InfluxDB 导出失败。

**解决**：
```bash
# 1. 确认 tick 已执行
cat Results/soloquant/prefect-task-index.json | python3 -m json.tool

# 2. 手动导出到 InfluxDB
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_prefect_influx_export.py \
  --config Launcher/config/config-soloquant.json --from-index

# 3. 查询 InfluxDB 确认数据
INFLUX_TOKEN=$(python3 -c "import json; print(json.load(open('Launcher/config/config-soloquant.json')).get('influxdb',{}).get('token-default',''))")
curl -s "http://localhost:8086/query?db=quant" \
  -H "Authorization: Token $INFLUX_TOKEN" \
  --data-urlencode 'q=SELECT * FROM soloquant_prefect_tick ORDER BY time DESC LIMIT 3'
```

### 8.7 Live paper 进程未启动

**原因**：Prefect flow 的 `run_live_paper` task 是哨兵模式，只启动注册表中 status≠retired 的策略。

**解决**：
```bash
# 检查策略注册表
cat Results/soloquant/strategy-registry.json | python3 -c "
import json, sys
r = json.load(sys.stdin)
for s in r.get('strategies', []):
    print(f\"{s.get('strategy_id','?'):40s} status={s.get('status','?')}\")

# 手动启动 live paper
./sqctl.sh start livepaper
```

---

## 9. 配置参考

### 9.1 Prefect 相关配置项

位于 `Launcher/config/config-soloquant.json`：

```json
{
  "prefect": {
    "api-url": "http://localhost:4200/api",
    "work-pool": "soloquant-pool"
  },
  "pipeline": {
    "poll-seconds": 300,
    "crawl-interval-seconds": 28800,
    "data-driven-crawl-interval-seconds": 28800,
    "reproduce-interval-seconds": 1800,
    "optimize-interval-seconds": 1800,
    "local-ingest-interval-seconds": 3600
  }
}
```

### 9.2 sqctl.sh 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LEAN_DIR` | 脚本所在目录 | LEAN 仓库根目录 |
| `CONDA_ENV` | `/root/miniconda3/envs/quant311/bin/python3` | SoloQuant Python 路径 |
| `BRIDGE_INTERVAL` | `60` | Bridge 轮询间隔（秒） |

### 9.3 Prefect 进程环境变量

`sqctl.sh start prefect` 自动设置：

| 变量 | Server | Worker | Flow |
|------|--------|--------|------|
| `PREFECT_API_URL` | `http://84.8.248.198:4200/api` | `http://localhost:4200/api` | `http://localhost:4200/api` |
| `PREFECT_UI_API_URL` | `http://84.8.248.198:4200/api` | — | — |

> `PREFECT_UI_API_URL` 用于 Prefect Server 告知浏览器 UI 的 API 地址，必须设为外部可访问的 IP。

---

## 10. 完整操作速查

```bash
# ── 日常操作 ──────────────────────────────────────
./sqctl.sh start prefect          # 启动 Prefect pipeline
./sqctl.sh stop prefect           # 停止 Prefect pipeline
./sqctl.sh status prefect         # 查看状态
./sqctl.sh logs prefect-flow 50   # 查看最近 50 行 flow 日志
./sqctl.sh summary                # 一行概览

# ── 手动触发 ──────────────────────────────────────
python3 Scripts/soloquant_prefect_flow.py --run-once --force   # 强制执行一次
python3 Scripts/soloquant_prefect_flow.py --run-once           # 遵循 interval 执行一次

# ── 模式切换 ──────────────────────────────────────
./sqctl.sh stop core && ./sqctl.sh start prefect   # Daemon → Prefect
./sqctl.sh stop prefect && ./sqctl.sh start core   # Prefect → Daemon

# ── 故障排查 ──────────────────────────────────────
curl -sf http://localhost:4200/api/health           # 检查 server 健康
cat Results/soloquant/prefect-task-index.json        # 查看最近 tick 结果
python3 Scripts/soloquant_prefect_influx_export.py --from-index   # 补写 InfluxDB

# ── 全量操作 ──────────────────────────────────────
./sqctl.sh start all              # 启动所有组件
./sqctl.sh stop all               # 停止所有组件
./sqctl.sh killall                # 强制杀所有量化进程
```
