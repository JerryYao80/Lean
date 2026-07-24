# SoloQuant Pipeline 启停管理 — 问题修复记录

## 原始问题

`./sqctl.sh stop` 执行后仍有量化策略在执行 live paper 或回测，没有安全关闭所有 SoloQuant pipeline 进程。

## 根因分析

经过进程表全面扫描，发现 3 类进程未被 `sqctl.sh` 纳管：

### 1. Legacy LEAN 进程（完全不可见）

手动启动的 LEAN 进程使用 `Launcher/config/config-*live-paper*.json` 配置，不在 SoloQuant PID 文件系统 `Results/soloquant/live-paper-processes/` 中。

发现时实际运行着 3 个 legacy 进程：

```
PID 1935759  config-barra-cne5v2-live-paper.json   (运行 3天+)
PID 2002874  config-barra-cne5v2-live-paper.json   (运行 3天+)
PID 2267931  config-barra-cne5v3-2-live-paper.json (运行 3天+)
```

原版 `sqctl.sh` 只从 PID 文件发现进程，这些进程完全不可见、无法停止。

### 2. Tushare supervisord 自动重启

`incremental_scheduler.py` 由 supervisord 管理，配置了 `autorestart=true`：

```ini
# /etc/supervisor/conf.d/tushare_worker.conf
[program:tushare_worker]
command=/root/miniconda3/envs/ohmyquant/bin/python3 -u incremental_scheduler.py ...
autostart=true
autorestart=true
```

直接 `kill` 进程后，supervisord 立即自动重启，导致 `stop all` 后 tushare 仍在运行。

### 3. Bash wrapper 误匹配

Claude Code 之前启动 LEAN 时留下的 `/bin/bash -c ...` wrapper 进程，其 cmdline 中包含 LEAN 命令行参数，`pgrep -f` 会误匹配：

```
PID 2002873  /bin/bash -c ... dotnet QuantConnect.Lean.Launcher.dll --config config-barra-cne5v2-live-paper.json
PID 2267930  /bin/bash -c ... dotnet QuantConnect.Lean.Launcher.dll --config config-barra-cne5v3-2-live-paper.json
```

### 4. Zombie 进程处理

LLM 后台任务的子进程可能变为 zombie（`State: Z`），`kill -0` 返回成功但进程已死，导致 stop 报错：

```
PID 3967247  [python3] <defunct>  State: Z (zombie)
ERROR: Failed to stop llm-analyze_finance_intelligence (PID 3967247)
```

## 修复方案

### 修复一：进程发现基于进程表扫描

**核心原则改变**：从「PID 文件发现」改为「进程表扫描为真实来源，PID 文件仅用于元数据」。

```bash
# 新增：扫描所有 dotnet LEAN 进程，按 config 路径分类
discover_lean_processes() {
    for pid in $(pgrep -f "dotnet.*${LEAN_DLL}"); do
        # 关键过滤：只匹配 /proc/$pid/comm 为 "dotnet" 的进程
        local comm=$(cat /proc/$pid/comm 2>/dev/null)
        [[ "$comm" != "dotnet" ]] && continue  # 排除 bash wrapper

        local config=$(lean_config_of_pid "$pid")
        local cls=$(classify_lean_config "$config")
        # cls = "soloquant" | "legacy" | "unknown"
        echo "${pid}|${config}|${cls}"
    done
}
```

分类规则：
- `*/soloquant/strategies/*` → `soloquant`
- `*/Launcher/config/*` → `legacy`
- 其他 → `unknown`（归入 stray 分组）

### 修复二：新增 `legacy` 分组

发现并管理 `Launcher/config/` 下的 LEAN 进程：

```bash
# 发现
discover_legacy_lean()       # 扫描进程表中 cls=legacy 的进程

# 停止（先保存状态）
stop_legacy() {
    # 保存当前运行的 legacy 配置到 .legacy-state.json
    python3 -c "json.dump({'running_configs': [...]}, open(state_file, 'w'))"
    # 然后停止所有 legacy 进程
}

# 启动（恢复状态）
start_legacy() {
    # 从 .legacy-state.json 读取之前运行的配置
    # 只启动保存的配置，不启动全部 20+ 配置
    # 也可用 --all 强制启动所有
}
```

状态文件：`Results/soloquant/.legacy-state.json`

```json
{
  "running_configs": [
    "/home/project/hope/Lean/Launcher/config/config-barra-cne5v2-live-paper.json",
    "/home/project/hope/Lean/Launcher/config/config-barra-cne5v3-2-live-paper.json"
  ]
}
```

### 修复三：Tushare supervisord 集成

将 tushare 的启停从直接 kill 改为 `supervisorctl`：

```bash
stop_tushare() {
    local info=$(supervisorctl status tushare_worker 2>/dev/null || true)
    local status=$(echo "$info" | awk '{print $2}')
    if [[ "$status" == "RUNNING" ]]; then
        supervisorctl stop tushare_worker
        # supervisord 不会自动重启
    fi
}

start_tushare() {
    local info=$(supervisorctl status tushare_worker 2>/dev/null || true)
    local status=$(echo "$info" | awk '{print $2}')
    if [[ "$status" != "RUNNING" ]]; then
        supervisorctl start tushare_worker
    fi
}
```

**关键细节**：`supervisorctl status` 在进程 STOPPED 时返回 exit code 3，不能用于条件判断。改为解析输出文本中的状态字段。

### 修复四：Zombie 进程处理

```bash
pid_alive() {
    local pid="$1"
    # Zombies 存在但不是"活着的"——等待父进程 reap
    local state=$(cat /proc/$pid/status 2>/dev/null | grep '^State:' | awk '{print $2}')
    [[ "$state" == "Z" ]] && return 1
    kill -0 "$pid" 2>/dev/null
}
```

### 修复五：新增 `stray` 分组和 `killall` 命令

```bash
# 发现未归类进程
status_stray() {
    # 1. 未知 config 路径的 LEAN 进程
    # 2. 仍在运行的 export 脚本
    # 3. 残留的 pipeline 脚本
    # 4. 残留的 live_paper 脚本
}

# 核弹级停止
cmd_killall() {
    cmd_stop all           # 先优雅停止
    # 扫描所有 dotnet LEAN 进程，SIGKILL 残留
    # 扫描所有项目 Python 进程，SIGKILL 残留
    # supervisorctl stop tushare_worker
    # 最终验证
}
```

### 修复六：启动顺序调整

原顺序：`core → livepaper → legacy → bridge → tushare`
问题：legacy 启动时检查 matching bridge，但 bridge 还没启动

新顺序：`core → bridge → livepaper → legacy → tushare`

## 修复前后对比

### 修复前

```
$ ./sqctl.sh stop all
# 杀了 PID 文件中的进程，但：

$ ps aux | grep dotnet | grep -v grep | wc -l
3  # ← 3 个 legacy LEAN 进程仍在运行！

$ supervisorctl status
tushare_worker  RUNNING  # ← tushare 自动重启了！

$ ./sqctl.sh summary
● SoloQuant: 4 processes running  # ← 以为全停了，实际还有 4 个
```

### 修复后

```
$ ./sqctl.sh stop all
[INFO] Saved 2 running legacy configs to state file
[OK]   legacy:barra-cne5v2 (LP) stopped
[OK]   legacy:barra-cne5v3-2 (LP) stopped
[OK]   tushare-worker stopped (supervisord)

$ ps aux | grep dotnet | grep -v grep | wc -l
0  # ← 全部停止

$ supervisorctl status
tushare_worker  STOPPED  # ← 不会自动重启

$ ./sqctl.sh summary
○ SoloQuant: 0 processes running  # ← 真正全部停止

$ ./sqctl.sh start all
[INFO] Restoring from saved state
[OK]   barra-cne5v2 (LP) started
[OK]   barra-cne5v3-2 (LP) started
[OK]   tushare-worker started (supervisord)

$ ./sqctl.sh summary
● SoloQuant: 14 processes running
```

## 验证结果

完整 `stop all` → `start all` 循环：

| 指标 | stop 前 | stop 后 | start 后 |
|------|---------|---------|----------|
| core | 2/2 | 0/2 | 2/2 |
| livepaper | 7 | 0 | 7 |
| legacy | 2 | 0 | 2（从状态恢复） |
| bridge | 2 | 0 | 2 |
| tushare | 1 | 0（supervisord STOPPED） | 1（supervisorctl start） |
| llm | 0 | 0 | 0（pipeline-runner 自动管理） |
| stray | 0 | 0 | 0 |
| **总进程数** | **14** | **0** | **14** |
| **dotnet LEAN 进程** | **9** | **0** | **9** |
| **supervisord 状态** | RUNNING | STOPPED | RUNNING |
