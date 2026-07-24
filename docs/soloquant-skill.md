# SoloQuant Skill 文档

## 位置

文件路径：`/root/.claude/skills/soloquant/SKILL.md`

这是 Claude Code 的 skill 定义目录（`/root/.claude/skills/`），与现有的 `serenity-skill` 同级。Skill 文件会被 Claude Code 自动加载，当你输入 `/soloquant` 时触发。

## 功能

SoloQuant skill 是一个 **自动量化策略管线运维手册**，覆盖从研究发现到实盘模拟的全生命周期：

### 7 类请求路由

| 类别 | 典型操作 |
|------|---------|
| **Pipeline 操作** | `./sqctl.sh start/stop/restart/status` 管理所有 daemon |
| **策略生命周期** | 查看策略状态（候选/服役中/已除役），晋升或退役 |
| **故障排查** | Pipeline 卡住、LLM 超时、编译失败、InfluxDB 问题 |
| **代码生成** | 触发 DeepSeek 生成策略代码，修复 broken 策略 |
| **监控** | 查看 Grafana 仪表板、InfluxDB funnel 数据 |
| **数据管线** | Tushare 数据同步、字段映射、数据覆盖率检查 |
| **配置** | 更新 config-soloquant.json 参数 |

### 4 条硬规则

1. **不修改已有功能** — 新功能必须用新的 InfluxDB measurement、新仪表板面板、新文件
2. **不修改 LEAN 原生代码** — 只修输入数据，不改 LEAN 核心
3. **Pipeline 必须常驻** — 编辑脚本后必须重启 daemon（`./sqctl.sh restart core`）
4. **只用 LEAN 原生指标** — 不自己算 backtest/portfolio/stats

### 核心数据流

```
Tushare下载 → SearxNG搜索 → crawl4ai提取 → DeepSeek筛选
→ DeepSeek复现 → 数据解析 → 代码生成 → 编译验证 → 烟雾测试
→ 3版本变体 → 回测优化 → Live Paper实盘 → 生命周期管理 → InfluxDB/Grafana
```

---

## SKILL.md 原文

以下为 `/root/.claude/skills/soloquant/SKILL.md` 的完整内容：

```yaml
---
name: soloquant
description: SoloQuant automated quant strategy pipeline. Manage the full lifecycle - research crawl, LLM screening, code generation, backtest optimization, live-paper deployment, and monitoring.
license: MIT
metadata:
  author: yzj19870824
  version: "1.0.0"
  short-description: SoloQuant automated quant strategy pipeline manager
---
```

# SoloQuant Skill

Automated quant strategy discovery, reproduction, and live-paper deployment pipeline for A-share markets, built on LEAN.

## Core Promise

Given a pipeline operation request (start/stop/status, strategy lifecycle, troubleshooting, monitoring), execute the correct operational sequence and return a clear status report.

## Critical Rules

### NEVER modify existing features
Mature features must never be modified. New features must use new InfluxDB measurements, new dashboard panels, new files. Never write to existing measurements like soloquant_pipeline_funnel.

### NEVER modify LEAN native code
Never modify LEAN core. Fix input data, not LEAN code. Derive from LEAN interfaces.

### Pipeline must always be alive
SoloQuant daemons must always be running. After editing any pipeline scripts, always restart both daemons.

### LEAN native only
All backtest/portfolio/stats must use LEAN native output. Never self-compute metrics that LEAN provides.

## Request Router

- **Pipeline Operations**: Start, stop, restart, status. Use sqctl.sh.
- **Strategy Lifecycle**: Check status (candidate/serving/retired), promote or retire.
- **Troubleshooting**: Debug pipeline failures, compilation errors, LLM timeouts.
- **Code Generation**: Trigger LLM code generation, fix broken strategies.
- **Monitoring**: Check Grafana dashboards, InfluxDB data, pipeline funnel.
- **Data Pipeline**: Tushare data sync, field mapping, data coverage.
- **Configuration**: Update config-soloquant.json, adjust settings.

## Pipeline Architecture - 14 Stages

The pipeline runs as a tick every 300 seconds (5 minutes):

| # | Stage | Category | Interval |
|---|-------|----------|----------|
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

DEGRADED_ON_FAIL stages continue after failure. Background sentinels never block.
Strategy Lifecycle: candidate - serving (服役中) - retired (已除役, when >25% degradation).

## sqctl.sh - Command Center

Commands: status, start, stop, restart, killall, logs, summary
Groups: core, prefect, livepaper, legacy, bridge, tushare, llm, stray

Core start:
  Pipeline runner: nohup python3 Scripts/soloquant_pipeline_runner.py --config config-soloquant.json --daemon --poll-seconds 300
  Crawl scheduler: nohup python3 Scripts/soloquant_crawl_scheduler.py --config config-soloquant.json --daemon

After ANY edit: ./sqctl.sh restart core

## External Infrastructure

SearxNG (localhost:11236), crawl4ai (localhost:11235), InfluxDB (localhost:8086, org=lean, bucket=quant), Grafana (localhost:3000), Prefect (localhost:4200), DeepSeek LLM (api.deepseek.com)

LLM: DeepSeek-v4-pro, timeout 1200s, env var ZHIPU_API_KEY
SearxNG: baidu/sogou/360search/arxiv/semantic_scholar/bing work; google/duckduckgo/startpage blocked

## Strategy Validation

1. Compile-validate-retry: dotnet build, LLM fix up to 2 retries, .cs.broken/.py.broken for failures
2. Smoke test: 1-day backtest, .smoke-failed suffix, degraded stage
3. Backtest optimization: full backtests for 3 variants, score, update registry

Fast rule: proceed unless >80% data missing (coverage < 20% = blocked)

## Data Requirements

Tushare data: /home/project/tushare-downloader/tushare_data
Field mapping: /home/project/tushare-downloader/tushare_field_mapping.json
Risk-free rate: SHIBOR 1Y via ChinaInterestRateProvider
Data grades: A (direct), B (backtest), C (logic only), D (replace before prod)

## Configuration (config-soloquant.json)

system-id: soloquant, workflow-root: Results/soloquant
poll-seconds: 300, crawl-interval: 28800, reproduce-interval: 1800, optimize-interval: 1800
languages: [CSharp, Python], min-versions: 3

## State Directory (Results/soloquant/)

State: soloquant-pipeline-state.json, strategy-registry.json, missing-data.jsonl
Dirs: artifacts/, generated-code/, live-paper-processes/, llm-background/, local-strategies/, strategies/

## A-Share Ticker

plain ticker (600519), NOT 600519.SSE. 6xx=SSE, 0xx/3xx=SZSE

## Common Operations

Check health: ./sqctl.sh status or ./sqctl.sh summary
Strategy registry: python3 -c with strategy-registry.json
One-shot tick: python3 Scripts/soloquant_pipeline_runner.py --config config-soloquant.json --once
Specific stage: python3 Scripts/soloquant_orchestrator.py --config config-soloquant.json --stage --max-items 1
InfluxDB query: curl localhost:8086/query with Token admin-token-leansystem
Local strategies: drop files in Results/soloquant/local-strategies/

## Troubleshooting

Pipeline stuck: ./sqctl.sh status core, check logs, grep errors, ./sqctl.sh restart core
LLM timeout: check 1200s timeout, PID in llm-background/*.pid.json
Compilation: check .cs.broken/.py.broken, read generated-code/*/manifest.json
Data coverage: check missing-data.jsonl, < 20% = auto-blocked
InfluxDB: curl localhost:8086/health, Grafana needs InfluxQL mode (not Flux)
GBM: TradeBar to 4 Ticks, 24h market hours, AShareStockFillModel

## Grafana Dashboards

soloquant-strategy-pipeline.json, soloquant-research-overview.json, soloquant-prefect-pipeline.json
NEVER modify existing panels. New strategies follow BarraCNE5V2 template.

## Key Files

Scripts/soloquant_orchestrator.py (6782 lines), soloquant_pipeline_runner.py, soloquant_crawl_scheduler.py, soloquant_job_runner.py, soloquant_prefect_flow.py
Launcher/config/config-soloquant.json, Results/soloquant/strategy-registry.json, sqctl.sh
Algorithm.CSharp/SoloQuantGenerated/, Algorithm.Python/SoloQuantGenerated/

## Pipeline Data Flow

Tushare -> SearxNG -> crawl4ai -> DeepSeek Screen -> Reproduce -> Data Resolution -> Code Gen -> Compile -> Smoke -> Variants -> Backtest -> Live Paper -> Lifecycle -> InfluxDB/Grafana

## Communication Style

Chinese for Chinese users, lead with status, show exact errors, verify after operations, copy-pasteable commands
