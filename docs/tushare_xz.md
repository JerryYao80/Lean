# Tushare 数据下载周期任务

> 记录时间：2026-06-17

---

## 当前运行的任务

### 1. Crontab 定时任务（当前活跃）

```cron
0 16 * * 1-5  /bin/bash /home/project/hope/Lean/data-source/tushare/run_incremental_update_and_convert.sh
```

- **触发时间**：每周一至周五 16:00（CST，即收盘后）
- **执行流程**（4 步）：
  1. `incremental_update.py` → 下载增量数据到 tushare_data parquet
  2. `convert_to_lean.py` → parquet 转 LEAN CSV 格式（equity daily + factor + map）
  3. `tushare_lean_export.py` → 导出 ETF 数据到 LEAN 格式
  4. `announcement_downloader.py` → 下载公告数据
- **特点**：一步到位，下载+转换+导出全链路

### 2. Supervisor 常驻进程（已停止）

```ini
[program:tushare_worker]
command = incremental_scheduler.py --poll-seconds 60 --retry-interval-minutes 30
autostart = true
autorestart = true
```

- **状态**：`STOPPED`（6 月 11 日 16:30 停止）
- **设计行为**：每 60 秒轮询一次时间，仅在上海时区交易日 16:00 后触发增量下载，失败后 30 分钟重试
- **与 crontab 的区别**：这个只下载数据（调用 `incremental_update.py`），**不做 LEAN 格式转换**

---

## 两者对比

| | Crontab | Supervisor (tushare_worker) |
|--|---------|---------------------------|
| 运行状态 | ✅ 活跃 | ❌ STOPPED |
| 下载+转换 | 一体化 | 仅下载 |
| 触发方式 | cron 定时 | 常驻轮询 |
| 失败重试 | 无 | 30 分钟后重试 |

当前实际生效的只有 crontab。supervisor 的 `tushare_worker` 已停运近一周，且功能被 crontab 脚本覆盖。

---

## 相关文件

| 文件 | 路径 | 用途 |
|------|------|------|
| crontab 脚本 | `data-source/tushare/run_incremental_update_and_convert.sh` | 下载+转换一体化入口 |
| 增量下载 | `tushare-downloader/incremental_update.py` | 核心：按交易日增量下载 tushare 数据 |
| 增量调度器 | `tushare-downloader/incremental_scheduler.py` | 常驻轮询版调度器（supervisor 用） |
| LEAN 转换 | `data-source/tushare/convert_to_lean.py` | parquet → LEAN CSV |
| ETF 导出 | `Scripts/tushare_lean_export.py` | ETF 数据导出 |
| 公告下载 | `data-source/tushare/announcement_downloader.py` | 公告数据下载 |
| Supervisor 配置 | `/etc/supervisor/conf.d/tushare.conf` | tushare_worker 进程配置 |
| 调度器状态 | `tushare-downloader/download_state/incremental_scheduler_state.json` | 增量调度状态持久化 |
