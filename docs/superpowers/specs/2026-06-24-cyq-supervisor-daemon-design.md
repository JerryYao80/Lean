# cyq 调度器改用 supervisor daemon（替换 cron）

**日期**：2026-06-24
**状态**：设计
**涉及代码**：`/home/project/tushare-downloader/cyq_scheduler.py`（新增）、`/home/project/tushare-downloader/backfill_cyq.py`（复用）、`/etc/supervisor/conf.d/cyq.conf`（新增）、`/home/project/tushare-downloader/install_cyq_supervisor.sh`（新增）
**动机**：cyq 日更 cron（2026-06-23 安装）引入了第二套调度机制（cron.service），而系统既有模式是 supervisor 守护自调度 daemon（`tushare_worker`）。统一为 supervisor 消除不一致。

---

## 1. 现状

- `tushare_worker`（supervisor）：`incremental_scheduler.py` 自调度 daemon，每 60s poll、cutoff 16:00、16 张核心表增量下载 + `/dev/null` 转换
- cyq 初始回补：`backfill_cyq.py`，逐 ts_code sweep（已补完 ~5 个月缺口到 20260623）
- 当前 cyq 日更：root crontab `10 17 * * 1-5 flock ... backfill_cyq.py --workers 8`，fire-and-exit
- `cron.service` 原为 inactive（本系统仅此一条 cron）；装 cyq cron 前 root crontab 为空
- systemd 可用（7 个 `*.timer` 单元），但现有调度未用 timer

---

## 2. 目标

用 supervisor daemon 替换 cron，统一调度机制，保持 cyq 日更与核心 16 表进程隔离、负载错峰。

---

## 3. 设计

### 3.1 架构

```
supervisor
├── tushare_worker          (core_scheduled_apis: 16 表，cutoff=16)
│   └── incremental_scheduler.py  → 写 tushare_data_v2/
└── tushare_cyq_worker      (cyq 日更，cutoff=17)    ← 新增
    └── cyq_scheduler.py     → 调 backfill_cyq.py
```

两个 supervisor 程序独立进程，共享同一 `tushare_data_v2/` 写入区。cyq cutoff 17:00、核心 16:00，天然错峰；非交易日两者都 skip，不叠加请求。

### 3.2 `cyq_scheduler.py`（新增，~60 行）

镜像 `incremental_scheduler.py` 的自调度模式。

**参数（模块常量）**：
- `POLL_SECONDS = 60`
- `CUTOFF_HOUR = 17`
- `WORKERS = 8`
- `STATE_FILE = download_state/cyq_scheduler_state.json`
- `LOG_FILE = logs/cyq_scheduler.log`

**主循环**：
```
while True:
    now = datetime.now(SHANGHAI_TZ)
    target_date = trade_cal 中 is_open=1 且 <= today 的最大 cal_date
    if 非交易日 or now.hour < CUTOFF_HOUR:
        sleep(POLL_SECONDS); continue
    if state.last_finished_target_date == target_date:
        log "already finished today, skip"; sleep(POLL_SECONDS); continue
    → 调 backfill_cyq.main(["--workers", str(WORKERS)])
    → 成功后写 state(last_finished_target_date=target_date, updated_at)
    sleep(POLL_SECONDS)
```

**复用 backfill_cyq**：同目录 import，调 `backfill_cyq.main(["--workers", "8"])`。`--target` 缺省 `"auto"`，由 backfill_cyq 自动取 `trade_cal` 最新交易日。

**可测试性**：把"是否触发"抽成纯函数 `_should_run(now, target_date, last_finished) -> bool`，便于单测（注入 `now`），不依赖系统时钟。

**日志**：单独文件 `logs/cyq_scheduler.log`（不混入 incremental_update.log）。

### 3.3 supervisor 配置 `cyq.conf`

```
[program:tushare_cyq_worker]
command=/root/miniconda3/envs/ohmyquant/bin/python3 -u /home/project/tushare-downloader/cyq_scheduler.py
directory=/home/project/tushare-downloader
user=root
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_out.log
stderr_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_err.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
stopasgroup=true
killasgroup=true
```

### 3.4 迁移脚本 `install_cyq_supervisor.sh`（幂等）

1. 确保 `backfill_cyq.py` 存在
2. 写 `cyq.conf` 到 `/etc/supervisor/conf.d/`
3. `supervisorctl reread && supervisorctl update` → `tushare_cyq_worker` 自启
4. 验证 `supervisorctl status tushare_cyq_worker` = RUNNING
5. 删 cron 条目：`crontab -l | grep -v tushare-cyq-backfill | crontab -`
6. 可选停用 cron.service（如 root crontab 已空）：`systemctl stop cron`

**幂等保障**：`cyq.conf` 覆盖写再 `update`（supervisor 不重建已运行同名程序）；cron 行用 `grep -v tushare-cyq-backfill` 过滤。

### 3.5 回滚

```bash
supervisorctl stop tushare_cyq_worker
rm /etc/supervisor/conf.d/cyq.conf && supervisorctl update
bash install_cyq_cron.sh                            # 恢复 crontab
systemctl start cron 2>/dev/null || true             # 确保 cron 在跑
```

### 3.6 错误处理

- `backfill_cyq.main` 完成（无论退出码 0/1）→ **都写 `last_finished_target_date`**，即每个交易日只跑一次。失败的 symbol 靠 backfill_cyq 的断点续传在**次日**自动补（它们仍落后，resumable）。这避免 2 小时大任务在 60s poll 下整夜重跑。
- `backfill_cyq.main` 抛未捕获异常（进程级崩溃）→ **不写 state**；由 supervisor `autorestart` 拉起后下一周期重试
- `tushare_cyq_worker` 自身异常退出 → `autorestart=true` 重新拉起
- 交易日判定失败（trade_cal 读不到）→ `warn` + sleep，不阻塞
- `flock` **不再需要**：daemon 的 state 机制天然防重入（`last_finished == target → skip`）

---

## 4. 不在范围

- 不修改 `backfill_cyq.py`（它已是"被调度方"的正确接口）
- 不修改 `tushare_worker`（核心 16 表）
- 不引入 systemd timer（本系统未用 timer 调度下载）
- 不移除 cron/crond 功能（仅清空 root crontab 行；无其他条目后可停用 cron.service）

## 5. 测试

1. `cyq_scheduler.py --dry-run`：不实际调 backfill，仅打印"would trigger backfill for target=YYYYMMDD"并退出
2. 写 `cyq.conf` + `supervisorctl update` 验证 `tushare_cyq_worker` RUNNING
3. 验证 17:00 后 `cyq_scheduler_state.json` 的 `last_finished_target_date` 写入正确
4. 验证 `backfill_cyq_cron.log` 不再有新的 cron 写入
5. 验证 `supervisorctl stop/start tushare_cyq_worker` 交互正常
6. 验证 `supervisorctl status` 同时显示 `tushare_worker` 和 `tushare_cyq_worker`
