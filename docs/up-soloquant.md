# SoloQuant Pipeline 启动手记

**日期**：2026-06-24
**操作**：一键启动 SoloQuant pipeline（`./sqctl.sh start core` + `start tushare` + `start prefect`）

---

## 启动结果

| 组件 | 状态 | 备注 |
|---|---|---|
| **pipeline-runner** | ✅ RUNNING（PID 1834123） | 主调度，每 300s tick |
| **crawl-scheduler** | ✅ RUNNING（PID 1834161） | 8h 爬虫调度 |
| **strategy-api** | ✅ RUNNING（:5000 healthy） | 策略 API |
| **tushare_worker** | ✅ RUNNING（supervisord） | 数据源，常驻 |
| **prefect-server** | ✅ RUNNING（:4200） | Prefect 编排服务 |
| **prefect-worker** | ✅ RUNNING | Prefect worker |
| **prefect-flow** | ❌ 启动失败 | 可选编排路径，不影响主 pipeline |
| **live-paper** | 0/13 running | pipeline tick 的 `run_live_paper` 阶段会自动拉起 |

---

## 说明

- **主 pipeline 已活**：`pipeline-runner` 是核心，按 14 阶段运行
  （ingest → crawl → reproduce → codegen → compile → smoke → backtest → live-paper → lifecycle → InfluxDB）。
  `DEGRADED_ON_FAIL` 阶段失败后继续；background sentinel 不阻塞。
- **last-tick 显示 2026-06-19 degraded**：是上次旧记录；runner 起来后下一个 300s 周期会刷新。
- **prefect-flow 失败**：Prefect 是另一套编排入口，主链路用 `pipeline-runner`，故不阻塞。
  如需 Prefect 编排，单独排查 `Scripts/soloquant_prefect_flow.py` 的部署/调度配置。
- **live-paper 0/13**：正常——这些是上次崩溃残留的死 PID 记录；pipeline tick 到
  `run_live_paper` 阶段会按 `strategy-registry.json` 里 `服役中` 的策略重新拉起。
- **tushare 数据已迁移到 `tushare_data_v2/`**（2026-06-23 分区规范化），`config.DATA_DIR` 已指向新目录。

---

## 14 阶段流水线

| # | Stage | 类别 | 间隔 |
|---|-------|------|------|
| 1 | ingest_local_strategies | Sync | 1h |
| 2 | data_driven_crawl | Background sentinel | 8h |
| 3 | crawl_research | Background sentinel | 8h |
| 4 | prepare_reproduction | Background sentinel | On demand |
| 5 | prepare_iv_data | Sync | On demand |
| 6 | build_event_graph | Sync | On demand |
| 7 | build_event_signals | Sync | On demand |
| 8 | reproduce_one | Background sentinel | 30m |
| 9 | materialize_variants | Sync | On demand |
| 10 | optimize_backtests | Background sentinel | 30m |
| 11 | prepare_live_market_data | Sync | On demand |
| 12 | update_lifecycle | Sync | On demand |
| 13 | run_live_paper | Sentinel | On demand |
| 14 | export_influx | Sync | On demand |

策略生命周期：candidate → serving（服役中）→ retired（已除役，>25% 退化时）。

---

## 常用命令

```bash
# 一键启动核心 daemon
./sqctl.sh start core
./sqctl.sh start tushare      # tushare_worker（supervisord）
./sqctl.sh start prefect      # Prefect 编排层

# 状态 / 日志 / 汇总
./sqctl.sh status
./sqctl.sh summary
./sqctl.sh logs pipeline-runner

# 停止 / 重启（编辑脚本后必须重启）
./sqctl.sh stop core
./sqctl.sh restart core
./sqctl.sh killall

# 手动触发一次 tick / 单阶段
python3 Scripts/soloquant_pipeline_runner.py --config config-soloquant.json --once
python3 Scripts/soloquant_orchestrator.py --config config-soloquant.json --stage --max-items 1
```

---

## 外部基础设施

- SearxNG：`localhost:11236`（baidu/sogou/360search/arxiv/semantic_scholar/bing 可用）
- crawl4ai：`localhost:11235`
- InfluxDB：`localhost:8086`（org=lean, bucket=quant, Token admin-token-leansystem）
- Grafana：`localhost:3000`（需 InfluxQL 模式，非 Flux）
- Prefect：`localhost:4200`
- DeepSeek LLM：`api.deepseek.com`（DeepSeek-v4-pro，timeout 1200s，env `ZHIPU_API_KEY`）

---

## 注意事项（来自项目约定）

- **成熟功能永不修改**：新功能用新 InfluxDB measurement / 新 dashboard panel / 新文件，不写已有 measurement（如 `soloquant_pipeline_funnel`）。
- **永不改 LEAN 原生代码**：数据问题改输入数据，从 LEAN 接口派生。
- **pipeline 必须常驻**：编辑任何 pipeline 脚本后，`./sqctl.sh restart core`。
- **LEAN native only**：回测/组合/统计全部用 LEAN 原生输出，不自算 LEAN 已提供的指标。
- **A 股 ticker**：用纯代码（`600519`），不是 `600519.SSE`；6xx=SSE，0xx/3xx=SZSE。
