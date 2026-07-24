# tushare_data 分区命名规范化设计

**日期**：2026-06-23
**状态**：设计待评审
**涉及代码**：`/home/project/tushare-downloader/`、`/home/project/hope/Lean/data-source/tushare/`、`/home/project/hope/Lean/Scripts/tushare_lean_export.py`
**涉及数据**：`/home/project/tushare-downloader/tushare_data/`（约 18G，73 张表）

---

## 1. 背景与问题

`tushare_data/` 的分区命名当前存在两类问题：

1. **历史残留分区**：STOCK 类表（`daily`/`daily_basic`/`adj_factor`/`stk_limit`/`moneyflow`/`income`/`balancesheet`/`fund_daily` 等）磁盘上同时存在 `ts_code=`、`date=`、`year=` 三套分区外加根 `data.parquet`。其中 `date=<symbol>`（如 `date=920992.BJ`）并非日期，而是早期调用方式 `download_api_by_date(date=<股票代码>)` 的产物；当前已改用 `download_api_by_stock(ts_code=…)` 写 `ts_code=<symbol>/`。残留分区是死数据，但会污染 `convert_to_lean.py` 的 `get_stock_list()` 目录枚举（把 `date=` 目录当股票列出）。
2. **键名歧义**：DATE 类表用 `date=<YYYYMMDD>` 作真日期分区，与残留的 `date=<symbol>` 同名不同义，导致 `date=920992.BJ` 无法仅凭键名判断是日期还是股票。

此外，部分二级表存在缺口（停在旧日期未更新到最新交易日 20260623），见第 6 节。

---

## 2. 目标与约束

**目标**
- 统一 `tushare_data` 的分区命名规范，每张表只允许一种分区策略，彻底消除残留与歧义。
- 便于 LEAN 因子计算（`convert_to_lean.py` / `tushare_lean_export.py` 能可靠读取逐股票全历史）。
- 纳入缺口补数，使数据更新到最新交易日。

**硬约束**
1. **不重新下载存量数据**：已下载的 18G parquet 全部本地复用，迁移过程零 tushare API 调用、零限流、零重传。
2. **复用现有健壮下载层**：tushare 限流 / 超时重传 / 重试逻辑原样保留，不重写下载核心。下载层只做最小改动（分区键名收敛 + 前缀校验）。
3. **下载活动由 supervisor 守护，不在 Claude Code CLI 执行**：持续下载由 supervisor 管理的 `tushare_worker` 常驻进程承担（见第 7 节）。本设计的迁移脚本是一次性离线数据变换，与常驻下载进程职责分离。
4. **保留旧目录只读作回滚**：迁移到新目录 `tushare_data_v2/`，旧目录切换后设为只读保留，验证稳定后再决定删除或归档。

---

## 3. 现状（分区方案）

分区由 `downloader.py:107 _get_file_path()` 手工拼目录实现（4 种模板：`ts_code=`/`year=`/`quarter=`/`date=` + 根文件），每张表的策略在 `api_registry.py` 的 `chunk_strategy` 枚举声明（NONE/STOCK/DATE/YEAR/QUARTER）。**未使用** pyarrow `partition_cols`。

LEAN 转换层硬编码读 `ts_code=<symbol>/data.parquet`：
- `Lean/data-source/tushare/convert_to_lean.py:44-61`
- `Lean/Scripts/tushare_lean_export.py:132-133`

两份脚本核心下载逻辑字节级相同；`tushare-downloader` 是 daemon 实跑的裁剪版（转换钩子被 `/dev/null` 关闭），`Lean/data-source/tushare` 是带转换钩子的完整版。两份 `config.py` 都把 `DATA_DIR` 指向同一份物理数据。

73 张表按现有 `chunk_strategy` 归类见第 4 节映射表。

---

## 4. 设计：分区命名规范

保持两层架构职责分离：
- **Raw 层** `tushare_data_v2/`：规范化分区后的 tushare 原始数据。
- **LEAN 层** `Lean/Data/`：转换产物（`equity/{sse|szse}/daily/{ticker}.csv` 等），由转换层从 Raw 层生成。

### 4.1 五类分区规范

每张表在 `api_registry.py` 声明**唯一** `chunk_strategy`，对应固定布局：

| 类别 | 分区布局 | 含义 | 代表表 |
|---|---|---|---|
| **STOCK** | `ts_code=<symbol>/data.parquet` | 一股一文件、全历史 | daily, daily_basic, adj_factor, stk_limit, moneyflow, income, balancesheet, cashflow, forecast, dividend, fina_indicator, index_daily, fund_daily, fund_nav, cyq_perf, margin_detail |
| **DATE** | `trade_date=<YYYYMMDD>/data.parquet` | 按交易日分区 | index_weight, index_dailybasic, opt_daily, cb_daily, top_list, top_inst, stk_auction_o, stk_auction_c, stk_ah_comparison, weekly, monthly, index_weekly, index_monthly |
| **YEAR** | `year=<YYYY>/data.parquet` | 按年分区 | hk_daily, hk_adjfactor, hk_daily_adj, fut_daily, margin, block_trade, limit_list_d, suspend_d, share_float, moneyflow_hsgt, moneyflow_ths, moneyflow_dc, moneyflow_ind_dc, moneyflow_ind_ths, moneyflow_mkt_dc, sw_daily, ci_daily |
| **QUARTER** | `quarter=<YYYYQN>/data.parquet` | 按季度分区 | fund_portfolio, express, top10_holders, top10_floatholders, stk_holdernumber, disclosure_date, report_rc, hk_balancesheet, hk_cashflow |
| **NONE** | `<api>/data.parquet` | 单文件 | stock_basic, trade_cal, index_basic, fund_basic, fut_basic, namechange, stk_managers, repurchase, broker_recommend |

> 实施前需逐表核对 `api_registry.py` 的 `chunk_strategy` 与上表一致；以 `api_registry.py` 实际声明为准，本表为已知归类。

### 4.2 关键命名决策

- DATE 类分区键名 `date=` → **`trade_date=`**：永久消除"`date=920992.BJ` 是日期还是股票"的歧义。
- STOCK 类保持 `ts_code=<symbol>/data.parquet`：这正是 LEAN 转换层期望的逐股票全历史形态，**转换层零改动**即可读取。

---

## 5. 迁移设计（本地，不重新下载）

新增一次性脚本 `migrate_to_v2.py`，**纯本地 parquet 读写，零 API 调用**。

### 5.1 逐表迁移动作

| 类别 | 动作 |
|---|---|
| STOCK | glob `<api>/ts_code=*/data.parquet`，逐文件 copy 到 `tushare_data_v2/<api>/ts_code=<symbol>/data.parquet`。**丢弃** `date=`/`year=` 残留分区。 |
| DATE | glob `<api>/date=*/data.parquet`，**仅接受 8 位日期值**（正则 `^\d{8}$`），copy 到 `tushare_data_v2/<api>/trade_date=<值>/data.parquet`。非 8 位值（残留的股票代码）告警并跳过。 |
| YEAR | glob `<api>/year=*/data.parquet`，直接 copy。 |
| QUARTER | glob `<api>/quarter=*/data.parquet`，直接 copy。 |
| NONE | copy `<api>/data.parquet` 单文件。 |

### 5.2 校验

每张表迁移完成后：
1. **行数比对**：旧目录该表全部分区总行数 == 新目录该表总行数。
2. **抽样日期校验**：STOCK 表抽样若干 symbol，比对 max/min `trade_date` 一致；DATE 表比对全表 max/min 日期一致。
3. 不一致 → 中止该表，回滚该表新目录写入，报告差异，**不进入切换流程**。

### 5.3 磁盘与回滚

- 迁移用**独立 copy**（非 hardlink），保证旧目录与 v2 物理独立。
- 峰值磁盘 2×18G，符合已确认的"保留旧目录只读作回滚"决策。
- 旧 `tushare_data/` 在切换后设为只读（`chmod -R a-w`），保留作回滚源；验证 N 天稳定后再决定删除或永久归档。

---

## 6. 缺口补数设计（纳入本次，使用现有下载层）

迁移并切换 DATA_DIR 后，对停在旧日期的表补数到最新交易日 **20260623**。复用现有 `incremental_update.py` 的 gap-fill 能力，从各表 `incremental_state.json` 的 `last_target_date` 起补。

**已知缺口**（基于 2026-06-23 实测）：

| 表 | 当前停在哪 | 缺口类型 |
|---|---|---|
| index_daily, index_dailybasic, index_weight, index_weekly, index_monthly | 20260612 | 每日轮转未覆盖（停 6/17） |
| fund_daily, fund_adj, fund_share | 20260612 | 同上 |
| hk_daily, hk_adjfactor, hk_tradecal, fut_daily, fut_settle, fut_holding | 20260612 | 同上 |
| moneyflow_dc/ths/ind_*, dc_daily, dc_hot, ths_daily, ths_hot, tdx_daily, sw_daily | 20260612 | 同上 |
| stk_premarket, stk_auction_o/c, bak_basic/daily, kpl_list | 20260612 | 同上 |
| cyq_chips | 20260129 | 停更约 5 个月 |
| **us_daily** | 内容仅到 20200129 | **可选**：美股接口限制大，单独评估，不阻塞主流程 |

> 这些是从未下载的新日期，**不属于重新下载存量**，符合约束 1。补数产物写入 `tushare_data_v2/`（已切换 DATA_DIR），失败沿用现有 state 重试机制。

---

## 7. 切换流程与 supervisor 集成（关键）

**tushare 数据下载是 supervisor 守护的常驻进程，不在 Claude Code CLI 中执行。** 现状：

```
[program:tushare_worker]
command=/root/miniconda3/envs/ohmyquant/bin/python3 -u \
        /home/project/tushare-downloader/incremental_scheduler.py \
        --poll-seconds 60 --retry-interval-minutes 30
directory=/home/project/tushare-downloader
autostart=true
autorestart=true
```

因此所有"停 / 重启 daemon"操作必须用 `supervisorctl`，不能 `kill` 裸进程（会被 autorestart 拉起）。

### 7.1 切换步骤

> 顺序关键：**先停 worker 让旧目录静止，再迁移**，避免迁移时 worker 仍在追加文件导致 torn read。停机窗口为迁移耗时（18G 本地 copy，约十几分钟，且发生在收盘后），缺口由第 6 节补数追平。

1. **停止守护进程**（让旧目录静止）：
   - `supervisorctl stop tushare_worker`
2. **离线迁移**（一次性，跑 `migrate_to_v2.py`，不涉及下载）：
   - 建 `tushare_data_v2/` → 跑迁移 → 5.2 校验通过。
3. **改配置**（两份脚本同步）：
   - `tushare-downloader/config.py` 与 `Lean/data-source/tushare/config.py` 的 `DATA_DIR` → `tushare_data_v2`。
4. **启动守护进程**：
   - `supervisorctl start tushare_worker`（本设计不改 `.conf` 的 command，仅改 `config.py`，无需 `reread/update`；若改了 `.conf` 则先 `supervisorctl reread && update`）。
   - 下载随即常驻写 `tushare_data_v2/`；旧目录设只读保留。
5. **缺口补数**：守护进程恢复后触发 gap-fill 写 v2，追平停机窗口 + 第 6 节历史缺口。
6. **验证 LEAN 转换**：`convert_to_lean.py --tushare-data <v2>` 转几只票，确认因子计算可用。
7. **观察期**：稳定运行 ≥3 个交易日后删除旧目录或永久只读归档。

### 7.2 回滚

任何步骤失败（校验不过 / 转换抽样失败 / 守护进程异常）：
- `DATA_DIR` 改回 `tushare_data`（旧目录只读，先 `chmod -R u+w` 恢复可写）
- `supervisorctl restart tushare_worker`
- 回到旧链路。

---

## 8. 代码改动（最小）

| 文件 | 改动 |
|---|---|
| `tushare-downloader/config.py` | `DATA_DIR` → `tushare_data_v2`（切换时，两份同步） |
| `Lean/data-source/tushare/config.py` | 同上 |
| `downloader.py:107 _get_file_path()` | 加前缀校验：DATE 类强制写 `trade_date=` 键名；拒绝 `date=<非8位日期>` 写入，根除残留再生 |
| `convert_to_lean.py:320 get_stock_list()` | 目录枚举加 `ts_code=` 前缀过滤（修现有 bug，避免 `date=`/`year=` 残留被当股票） |
| `api_registry.py` | 逐表核对 `chunk_strategy` 唯一且与第 4.1 节一致 |
| **新增** `migrate_to_v2.py` | 一次性本地迁移脚本（第 5 节） |

两份脚本（`tushare-downloader` + `Lean/data-source/tushare`）的 `incremental_update.py`/`downloader.py`/`api_registry.py`/`config.py` 必须同步改动。

---

## 9. 错误处理

- **迁移**：逐表事务化，单表失败回滚该表；全局失败保留旧目录，不切 DATA_DIR。
- **校验失败**（行数 / 日期不一致）：中止，报告差异，不切换。
- **切换后 LEAN 转换抽样失败**：按 7.2 回滚 DATA_DIR。
- **守护进程异常**：supervisor `autorestart` 自愈；若 `tushare_worker` 反复崩溃，按 7.2 回滚。
- **补数失败**：沿用现有 state 重试机制（`--retry-interval-minutes 30`），失败的表记入 `incremental_state.json`，次日重试。

---

## 10. 测试

1. **迁移脚本端到端**：挑小表各跑通一类——NONE（`trade_cal`）、DATE（`index_weight`）、QUARTER（`fund_portfolio`）、STOCK（单只 `daily/ts_code=600519.SH`）——行数与日期校验通过。
2. **`_get_file_path` 单测**：各策略路径正确；DATE 类拒绝 `date=<非8位>` 写入并抛错。
3. **`get_stock_list` 单测**：混入 `date=`/`year=` 目录时只返回 `ts_code=` 前缀项。
4. **LEAN 转换比对**：迁移后 `convert_to_lean.py` 转 600519.SH / 000001.SZ，产物与旧目录转换产物逐字段一致（价格 ×10000、vol ×100 规则不变）。
5. **补数验证**：跑一张缺口表（如 `index_daily`），确认 `last_target_date` 推进到 20260623。
6. **守护进程切换演练**：在副本环境验证 `supervisorctl stop/restart tushare_worker` 后增量正确写入 v2。

---

## 11. 不在本次范围

- 重写下载核心（限流 / 超时重传保持不变）。
- `us_daily` 美股全量补数（标注可选，单独评估）。
- 改动 LEAN 转换的价格缩放规则（仅修读取路径与前缀过滤）。
- Hive 多级分区 / 统一日期分区方案（已评估排除，见设计讨论记录）。
