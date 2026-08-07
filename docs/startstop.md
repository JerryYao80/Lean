# SoloQuant Pipeline 启停管理

使用 `sqctl.sh` 一键管理所有 SoloQuant 后台进程。

**核心原则**：
- 以**进程表扫描**为真实来源，PID 文件仅用于元数据
- 即使进程不在 PID 文件中注册，也能被发现和停止
- Tushare 由 **supervisord** 管理，使用 `supervisorctl` 控制启停（防止自动重启）
- Legacy 进程停止前**保存状态**，启动时**恢复**，避免意外启动全部 20+ 配置

## 快速参考

```bash
./sqctl.sh status              # 扫描所有组件状态（含进程表扫描）
./sqctl.sh summary             # 一行概览运行数量
./sqctl.sh stop                # 优雅停止所有（含 supervisord tushare + legacy 状态保存）
./sqctl.sh start               # 启动所有（含 legacy 状态恢复）
./sqctl.sh killall             # 核弹级：强制杀死所有量化进程
```

## 命令格式

```bash
./sqctl.sh <command> [group] [options]
```

### 命令

| 命令 | 说明 |
|------|------|
| `status [group]` | 显示所有或指定分组的状态（基于进程表扫描） |
| `start [group]` | 启动所有或指定分组 |
| `stop [group]` | 优雅停止（SIGTERM → 超时后 SIGKILL） |
| `restart [group]` | 先停后启 |
| `killall` | 核弹级：强制终止所有量化进程（dotnet LEAN + 项目 Python） |
| `logs [group] [N]` | 查看最近 N 行日志并持续跟踪（默认 50 行） |
| `summary` | 一行显示运行进程数 |

### 分组

| 分组 | 包含组件 | 发现方式 |
|------|----------|----------|
| `all` | 全部（含 stray 清理） | 综合 |
| `core` | pipeline-runner、crawl-scheduler | 进程表匹配 |
| `livepaper` | SoloQuant live-paper LEAN 进程 | PID 文件 + 进程表扫描 |
| `legacy` | Launcher/config/\*live-paper\*.json 的 LEAN 进程 | 进程表扫描 |
| `bridge` | barra-cne5v2、barra-cne5v3-2 等 bridge 守护进程 | 进程表匹配 |
| `tushare` | incremental-scheduler（supervisord 管理） | supervisorctl |
| `llm` | LLM 后台任务 | PID 文件 |
| `stray` | 未归类的 LEAN 进程、export 脚本、残留 pipeline 脚本 | 进程表扫描 |

### 日志分组

| 分组 | 日志来源 |
|------|----------|
| `pipeline-runner` | SoloQuant pipeline 主循环 |
| `crawl-scheduler` | 研究爬虫调度器 |
| `tushare` | Tushare 增量调度器 |
| `bridge` | 所有 live bridge 日志 |
| `livepaper` | 所有 soloquant live-paper LEAN 进程日志 |
| `legacy` | 所有 legacy LEAN 日志（/tmp/） |
| `llm` | 所有 LLM 后台任务日志 |

## 使用示例

```bash
# 查看全量状态（含进程表扫描，发现所有未注册进程）
./sqctl.sh status

# 只查看核心守护进程
./sqctl.sh status core

# 查看未归类的残留进程
./sqctl.sh status stray

# 停止所有组件（包括 legacy 状态保存 + supervisord tushare）
./sqctl.sh stop

# 只停止 legacy LEAN 进程（自动保存运行状态）
./sqctl.sh stop legacy

# 只停止 live-paper 进程
./sqctl.sh stop livepaper

# 启动所有（core→bridge→livepaper→legacy→tushare 顺序）
./sqctl.sh start

# 启动核心守护进程
./sqctl.sh start core

# 启动所有 bridge（包括 ashare-* 系列）
./sqctl.sh start bridge --all

# 启动所有 legacy 配置（不限于保存的状态）
./sqctl.sh start legacy --all

# 重启 live-paper
./sqctl.sh restart livepaper

# 核弹级：强制杀死所有量化进程
./sqctl.sh killall

# 查看管道运行日志最近 100 行
./sqctl.sh logs pipeline-runner 100

# 实时跟踪 bridge 日志
./sqctl.sh logs bridge

# 一行概览
./sqctl.sh summary
# ● SoloQuant: 14 processes running  (core:2/2  livepaper:7  legacy:2  bridge:2  tushare:1  llm:0)
```

## 分组详解

### core — 核心守护进程

| 进程 | 脚本 | 说明 |
|------|------|------|
| pipeline-runner | `Scripts/soloquant_pipeline_runner.py --daemon --poll-seconds 300` | 主流水线：14 个阶段循环执行（爬取→复现→编译→回测→上线） |
| crawl-scheduler | `Scripts/soloquant_crawl_scheduler.py --daemon` | 研究爬虫：定期从 arXiv/SSRN/Quantpedia 等源爬取策略研究 |

日志路径：
- `Results/soloquant/soloquant-pipeline.log`
- `Results/soloquant/soloquant-crawl-scheduler.log`

### livepaper — SoloQuant Live-Paper LEAN 进程

由 pipeline-runner 的 `run_live_paper` 阶段自动创建，PID 文件存放在 `Results/soloquant/live-paper-processes/*.pid.json`。

**发现方式**：同时扫描 PID 文件和进程表，确保即使 PID 文件过期也能发现正在运行的进程。

每个 PID 文件记录：
- `strategy_id` — 策略标识
- `pid` — 进程号
- `live-paper-config` — LEAN 配置路径
- `started_at_utc` — 启动时间

### legacy — Legacy LEAN 进程

手动启动的 LEAN 进程，使用 `Launcher/config/config-*live-paper*.json` 配置。这些进程不在 SoloQuant PID 文件系统中。

**发现方式**：扫描所有 `dotnet` 进程（仅匹配 `/proc/$pid/comm` 为 `dotnet` 的进程，排除 bash wrapper），按 `--config` 参数路径分类。`Launcher/config/` 下的为 legacy 进程。

**状态保存与恢复**：
- `stop legacy` 时，将当前运行的 legacy 配置路径保存到 `Results/soloquant/.legacy-state.json`
- `start legacy` 时，优先从状态文件恢复；若无状态文件，则只启动与运行中 bridge 匹配的配置
- `start legacy --all` 启动所有 legacy 配置

常见 legacy 进程：
- `config-barra-cne5v2-live-paper.json` — Barra CNE5 V2 live paper
- `config-barra-cne5v3-2-live-paper.json` — Barra CNE5 V3.2 live paper
- `config-ashare-*-live-paper.json` — A-Share 各策略 live paper

> ⚠️ 注意：同一配置可能有多个进程（如重复启动），`stop legacy` 会杀掉所有匹配的进程。

### bridge — Live Bridge 守护进程

桥接脚本将 LEAN 实时交易数据导出到 InfluxDB 供 Grafana 展示。

| 桥接 | 脚本 | 默认启停 |
|------|------|----------|
| barra-cne5v2 | `Scripts/barra_cne5v2_live_bridge.py` | ✅ |
| barra-cne5v3-2 | `Scripts/barra_cne5v3_2_live_bridge.py` | ✅ |
| barra-cne5v4 | `Scripts/barra_cne5v4_live_bridge.py` | 需 `--all` |
| barra-cne5 | `Scripts/barra_cne5_live_bridge.py` | 需 `--all` |
| ashare-etf-t0 | `Scripts/ashare_etf_t0_feature_live_bridge.py` | 需 `--all` |
| ashare-multi-family | `Scripts/ashare_multi_family_live_bridge.py` | 需 `--all` |
| ashare-llm-quant | `Scripts/ashare_llm_quant_live_bridge.py` | 需 `--all` |
| ashare-industry-rotation | `Scripts/ashare_industry_rotation_live_bridge.py` | 需 `--all` |
| ashare-etf-dual-rotation | `Scripts/ashare_etf_dual_rotation_live_bridge.py` | 需 `--all` |

### tushare — Tushare 数据源（supervisord 管理）

| 进程 | 脚本 | 说明 |
|------|------|------|
| tushare-worker | `/home/project/tushare-downloader/incremental_scheduler.py` | 每个交易日 16:00 后自动触发增量数据更新 |

**重要**：Tushare 由 **supervisord** 管理，配置了 `autorestart=true`。直接 kill 进程会被 supervisord 自动重启。`sqctl.sh` 使用 `supervisorctl start/stop tushare_worker` 控制启停。

supervisord 配置：`/etc/supervisor/conf.d/tushare_worker.conf`

日志路径：`/home/project/tushare-downloader/logs/incremental_scheduler_*.log`

### llm — LLM 后台任务

LLM 任务由 pipeline-runner 自动派生，PID 跟踪在 `Results/soloquant/llm-background/*.pid.json`。

| 阶段 | 说明 |
|------|------|
| crawl_research | 爬取研究论文 |
| data_driven_crawl | 数据驱动爬取 |
| prepare_reproduction | 准备复现摘要 |
| generate_strategy_code | GLM 生成 C# 策略代码 |
| reproduce_one | 编译+冒烟测试 |
| optimize_backtests | 多版本回测优化 |
| analyze_finance_intelligence | 金融情报分析 |

这些任务由 pipeline-runner 生命周期管理，`sqctl.sh` 可查看状态和强制停止，但不能手动启动。如需触发一次性执行，直接调用编排器：

```bash
/root/miniconda3/envs/quant311/bin/python3 Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json --<stage>
```

### stray — 未归类的残留进程

扫描并管理不属于以上任何分组的进程：

- 配置路径未知的 LEAN dotnet 进程
- 仍在运行的 export 脚本（`export_*_to_influx.py` 等）
- 残留的 pipeline 脚本（非 soloquant_pipeline_runner）
- 残留的 live_paper 脚本

`stop all` 会自动清理这些残留进程。

## killall — 核弹级停止

```bash
./sqctl.sh killall
```

按以下顺序执行：
1. 先执行正常的 `stop all`（优雅停止）
2. 扫描所有 `dotnet.*Lean` 进程，SIGKILL 残留
3. 扫描所有项目相关 Python 进程，SIGKILL 残留
4. 通过 `supervisorctl stop tushare_worker` 停止 supervisord 管理的进程
5. 最终验证是否全部清除

> ⚠️ 这是核弹级操作，会杀死所有量化相关进程，不可恢复。仅在确认需要完全清理时使用。

## 停止机制

`sqctl.sh stop` 采用优雅关闭策略：

1. 发送 `SIGTERM`，等待进程自行退出
2. 超时后（core 15s、livepaper/legacy 20s、其他 10s）发送 `SIGKILL` 强制终止
3. Zombie 进程自动跳过（等待父进程 reap）
4. 确认进程已退出

## 进程发现原理

sqctl.sh 采用**两层发现**机制：

1. **PID 文件**（`Results/soloquant/` 下的 `*.pid.json`）— 提供策略名称等元数据
2. **进程表扫描**（`/proc/` + `pgrep`）— 发现所有实际运行的进程

关键过滤：
- 只匹配 `/proc/$pid/comm` 为 `dotnet` 的进程，排除 bash wrapper 进程
- Zombie 进程（State: Z）不视为"存活"

## 启停顺序

### stop all
```
llm → livepaper → legacy (保存状态) → bridge → stray → core → tushare (supervisorctl)
```

### start all
```
core → bridge → livepaper → legacy (恢复状态) → tushare (supervisorctl) → llm (提示)
```

Bridge 在 legacy 之前启动，确保 legacy 的 bridge 匹配检查能正确工作。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LEAN_DIR` | 自动检测（脚本所在目录） | LEAN 仓库根目录 |
| `CONDA_ENV` | `/root/miniconda3/envs/quant311/bin/python3` | SoloQuant Python 环境 |
| `TUSHARE_ENV` | `/root/miniconda3/envs/ohmyquant/bin/python3` | Tushare Python 环境 |
| `BRIDGE_INTERVAL` | `60` | Bridge 轮询间隔（秒） |

## 运维注意事项

- **Tushare 自动重启**：supervisord 配置了 `autorestart=true`，直接 kill 进程会被自动重启。必须使用 `supervisorctl stop tushare_worker` 或 `./sqctl.sh stop tushare`
- **Legacy 状态保存**：`stop legacy` 自动保存运行中的配置到 `.legacy-state.json`，`start legacy` 自动恢复。若需清除状态，删除 `Results/soloquant/.legacy-state.json`
- **Live-Paper 自动恢复**：pipeline-runner 会检测已注册但未运行的 live-paper 进程并自动重启，手动 `stop livepaper` 后如需保持停止状态，需同时停止 core
- **PID 文件**：livepaper 和 llm 的 PID 文件由 pipeline-runner 维护，手动修改可能导致状态不一致
- **日志轮转**：当前日志为追加模式，长期运行需定期清理或配置 logrotate
- **重复 Legacy 进程**：同一配置可能存在多个进程（重复启动），`stop legacy` 会全部清除
- **Zombie 进程**：LLM 后台任务可能产生 zombie 子进程，sqctl.sh 自动跳过，等待父进程（pipeline-runner）reap
