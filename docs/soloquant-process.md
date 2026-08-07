# SoloQuant Pipeline 进程一键关闭

> 日期：2026-06-23
> 命令中心：`sqctl.sh`（位于 LEAN 仓库根目录）
> 设计方法：以进程表为唯一事实来源，PID 文件仅作元数据；按依赖顺序 graceful 关闭（SIGTERM → SIGKILL）。

---

## 一键关闭命令

```bash
./sqctl.sh stop all
```

该命令按依赖顺序依次关闭所有 SoloQuant 组件：

```
prefect → llm → livepaper → legacy → bridge → stray → core → tushare
```

> 如需强制终止（nuclear 选项，先 graceful stop 再扫杀所有残留 dotnet/quant Python 进程）：
> ```bash
> ./sqctl.sh killall
> ```

---

## 关闭结果（2026-06-23 执行）

| 组件 | 状态 |
|------|------|
| Prefect (server/worker/flow) | ✅ 全部停止 |
| LLM 后台任务 (6个) | ✅ 全部停止 |
| SoloQuant live-paper LEAN (7个) | ✅ 全部停止 |
| Legacy LEAN | ✅ 无运行 |
| Bridge 守护进程 (9个) | ✅ 全部停止 |
| Core 守护进程 (pipeline-runner / crawl-scheduler / strategy-api) | ✅ 全部停止 |
| Stray / untracked 进程 | ✅ 无残留 |
| Tushare worker | ✅ supervisord STOPPED |

最终验证（全部为 0）：

```bash
ps aux | grep -E "dotnet.*Lean|QuantConnect" | grep -v grep | wc -l   # dotnet LEAN = 0
ps aux | grep -E "soloquant|barra_cne5|ashare.*bridge|incremental_scheduler" | grep -v grep | wc -l  # python = 0
ps aux | grep -E "prefect (server|worker)" | grep -v grep | wc -l     # prefect = 0
supervisorctl status tushare_worker                                    # STOPPED
```

`./sqctl.sh summary` 输出：

```
○ SoloQuant: 0 processes running  (prefect:0/3  core:0/2  livepaper:0  legacy:0  bridge:0  tushare:0  llm:0)
```

---

## 注意事项

1. **Tushare worker 由 supervisord 托管**（`autostart=true` / `autorestart=true`），`sqctl.sh stop all` 通过 `supervisorctl stop` 将其置于 `STOPPED` 状态，当前不会自动重启；但若 supervisord 本身重启，则会按配置自动拉起 tushare_worker。

2. **重新启动整套 pipeline：**
   ```bash
   ./sqctl.sh start all
   ```

3. **`stop all` 与 `killall` 的区别：**
   - `stop all`：graceful 关闭（SIGTERM，超时后 SIGKILL），按组顺序停止，记录 PID 文件状态。
   - `killall`：nuclear 选项，先执行 `stop all`，再扫杀所有幸存的 `dotnet` LEAN 进程和项目 Python 进程（`kill -9`），适合彻底清理无法 graceful 退出的残留。

---

## 常用运维命令速查

| 操作 | 命令 |
|------|------|
| 全量状态扫描 | `./sqctl.sh status` |
| 一行运行计数 | `./sqctl.sh summary` |
| 一键停止全部 | `./sqctl.sh stop all` |
| 一键强制终止 | `./sqctl.sh killall` |
| 重启全部 | `./sqctl.sh restart all` |
| 仅停止 core | `./sqctl.sh stop core` |
| 仅停止 live-paper | `./sqctl.sh stop livepaper` |
| 仅停止 prefect | `./sqctl.sh stop prefect` |
| 查看日志 | `./sqctl.sh logs <group> [N]` |

### Groups

- `all` — 全部组件（含 stray 清理）
- `core` — pipeline-runner + crawl-scheduler + strategy-api
- `prefect` — Prefect server + worker + flow deployment
- `livepaper` — SoloQuant live-paper LEAN 进程（PID 文件 + 进程扫描）
- `legacy` — 来自 `Launcher/config/*live-paper*.json` 的 legacy LEAN
- `bridge` — live bridge 守护进程（`start bridge --all` 启动全部）
- `tushare` — Tushare incremental scheduler
- `llm` — LLM 后台任务（由 pipeline-runner 自动管理）
- `stray` — 未跟踪/孤儿进程（unknown LEAN、export 脚本等）
