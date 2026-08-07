# tushare_data 数据问题诊断

> 诊断时间：2026-06-16

## 问题一：daily_basic / moneyflow 存在两套分区，读取方读的是旧分区

### 现象

- `tushare_analysis.py` 和分析代码读取 `daily_basic` 时，PE/PB 数据只到 **2026-01-27**
- 但增量调度器的 state 文件显示 `daily_basic` 最后更新到 **2026-06-12**
- `moneyflow` 同样存在此问题

### 根因

tushare_data 里 `daily_basic` 和 `moneyflow` 表存在**两套分区目录**：

| 分区格式 | 示例路径 | 最新数据日期 | 状态 |
|---|---|---|---|
| `date=<ts_code>`（旧） | `daily_basic/date=002851.SZ/` | **2026-01-27** | 停更5个月 |
| `ts_code=<ts_code>`（新） | `daily_basic/ts_code=002851.SZ/` | **2026-06-11** | 正常更新 |

增量更新脚本 (`incremental_update.py`) 的 `_get_file_path` 方法在某个时间点**改了分区命名规则**——从 `date=XXX.SZ` 改为 `ts_code=XXX.SZ`。改了之后，新数据写入 `ts_code=` 分区，但 `tushare_analysis.py` 和分析代码还在读旧的 `date=` 分区，所以拿到的 PE/PB 数据只到 2026-01-27。

### 验证数据

```
=== 旧分区 (date=XXX.SZ) ===
  000001.SZ | latest=20260127
  600519.SH | latest=20260127
  002851.SZ | latest=20260127
  300870.SZ | latest=20260127
  600563.SH | latest=20260127

=== 新分区 (ts_code=XXX.SZ) ===
  000001.SZ | rows=6060 | latest=20260611
  600519.SH | rows=5921 | latest=20260611
  002851.SZ | rows=2154 | latest=20260611
  300870.SZ | rows=1387 | latest=20260611
  600563.SH | rows=5648 | latest=20260611
```

moneyflow 同理：

```
=== 旧分区 (date=XXX.SZ) ===
  002851.SZ | rows=2083 | latest=20260126

=== 新分区 (ts_code=XXX.SZ) ===
  002851.SZ | rows=2143 | latest=20260611
```

### 影响范围

- 所有通过 `tushare_analysis.py` 的 `_read_by_ts_code()` 函数读取 `daily_basic`、`moneyflow` 的功能均受影响
- 具体包括：`valuation_metrics()`、`moneyflow_analysis()` 以及之前 800V DC 分析中的 PE/PB/资金流数据
- 导致 800V DC 分析报告中估值数据滞后 5 个月（用的是 2026-01-27 而非 2026-06-11 的数据）

### 涉及代码

`/root/.claude/skills/serenity-skill/scripts/tushare_analysis.py` 第 85-97 行：

```python
def _read_by_ts_code(table: str, ts_code: str) -> pd.DataFrame:
    """Read a date=<ts_code> partitioned table for one stock."""
    partition_dir = _data_path(table) / f"date={ts_code}"  # ← 只读旧分区
    if not partition_dir.exists():
        raise FileNotFoundError(f"Partition not found: {partition_dir}")
    return pd.read_parquet(partition_dir)
```

### 修复方案

`_read_by_ts_code` 应优先读 `ts_code=` 分区，回退到 `date=` 分区：

```python
def _read_by_ts_code(table: str, ts_code: str) -> pd.DataFrame:
    """Read a ts_code-partitioned table for one stock.

    Tries ts_code=<ts_code> first (new format), falls back to date=<ts_code> (legacy).
    """
    new_dir = _data_path(table) / f"ts_code={ts_code}"
    if new_dir.exists():
        return pd.read_parquet(new_dir)

    old_dir = _data_path(table) / f"date={ts_code}"
    if old_dir.exists():
        return pd.read_parquet(old_dir)

    raise FileNotFoundError(
        f"Partition not found for {table}/{ts_code} "
        f"(tried ts_code= and date= prefixes)"
    )
```

---

## 问题二：财报数据从未通过增量调度更新，只到 2025Q3

### 现象

- `fina_indicator / income / balancesheet / cashflow` 最新报告期均为 **2025-09-30**
- 缺 2025 年报（2025-12-31）和 2026Q1（2026-03-31）

### 根因

`incremental_scheduler.py` 的 `CORE_SCHEDULED_APIS` 只包含日频行情数据：

```python
CORE_SCHEDULED_APIS = [
    "trade_cal",
    "daily",
    "adj_factor",
    "daily_basic",
    "moneyflow",
    "suspend_d",
    "stk_limit",
]
```

财报类 API（`fina_indicator / income / balancesheet / cashflow / forecast / express`）不在其中，且 `incremental_state.json` 中从未出现过这些 API——说明**财报数据从未通过增量调度更新过**，只靠最初的全量下载。

### 影响范围

- `tushare_analysis.py` 的 `financials_analysis()` 只能看到 2025Q3 数据
- 无法自动获取最新年报和季报
- 800V DC 分析中 ROE、毛利率、营收 YoY 等财务指标滞后 3 个季度

### 修复方案

1. 在 `incremental_scheduler.py` 中增加财报类 API 的定期更新（建议每天收盘后检查是否有新财报披露）
2. 或手动运行一次性补全：`python3 incremental_update.py --api fina_indicator,income,balancesheet,cashflow,forecast,express --target-date 20260612`

---

## 问题三：旧分区数据残留

### 现象

`daily_basic` 目录下同时存在 5478 个 `date=XXX.SZ` 旧分区和 5535 个 `ts_code=XXX.SZ` 新分区，占用双倍磁盘空间。

### 修复方案

确认新分区数据完整后，清理旧分区：

```bash
# 先验证新分区数量 >= 旧分区
ls daily_basic/ts_code=* | wc -l
ls daily_basic/date=* | wc -l

# 确认后删除旧分区
find daily_basic/ -maxdepth 1 -type d -name "date=*.SZ" -exec rm -rf {} +
```

对 `moneyflow` 做同样处理。

---

## 数据新鲜度完整汇总

| 表 | 旧分区最新 | 新分区最新 | 正确值 | 之前分析实际读到的 |
|---|---|---|---|---|
| daily_basic | 2026-01-27 | 2026-06-11 | 2026-06-11 | ❌ 2026-01-27 |
| moneyflow | 2026-01-26 | 2026-06-11 | 2026-06-11 | ❌ 2026-01-26 |
| daily | — | 2026-06-11 | 2026-06-11 | ✅ 2026-06-11 |
| adj_factor | — | 2026-06-11 | 2026-06-11 | ✅ 2026-06-11 |
| hsgt_top10 | — | 2026-06-11 | 2026-06-11 | ✅ 2026-06-11 |
| fina_indicator | 2025-09-30 | 2025-09-30 | 2025-09-30 | ⚠️ 缺 2025年报+2026Q1 |
| income | 2025-09-30 | 2025-09-30 | 2025-09-30 | ⚠️ 缺 2025年报+2026Q1 |
| balancesheet | 2025-09-30 | 2025-09-30 | 2025-09-30 | ⚠️ 缺 2025年报+2026Q1 |
| cashflow | 2025-09-30 | 2025-09-30 | 2025-09-30 | ⚠️ 缺 2025年报+2026Q1 |
| forecast | — | — | 2025-12-31（仅部分） | — |

## 增量调度器运行状态

- `incremental_scheduler.py` 进程存活（PID 270557，启动于 Jun 12）
- `supervisord` 运行正常
- `tushare_worker` 已 STOPPED（Jun 11 04:30 PM）
