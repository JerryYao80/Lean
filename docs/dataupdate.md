# Tushare Data 更新状态报告

> 扫描时间：2026-06-17
> 数据路径：`/home/project/tushare-downloader/tushare_data`（共 161 个子目录）
> LEAN 转换数据：`/home/project/hope/Lean/Data/equity/{sse,szse}/daily/`

---

## 1. 核心结论

**tushare_data 数据并非只更新到 2026-01-27。** 截至扫描时：

| 数据层级 | 最新日期 | 说明 |
|----------|----------|------|
| tushare_data（ts_code= 分区）| **2026-06-11~12** | 当前增量更新格式，大部分核心行情数据已到 6 月 |
| LEAN 转换 CSV（equity daily）| **2026-06-11** | 93% 的 A 股日线数据已到 6 月 |
| LEAN 利率数据 | **2026-01-23** | ❌ 停滞在 1 月，需更新 |
| tushare_data（date= 分区）| **2026-01-27** | ⚠️ 旧格式，已废弃但仍存在 |

**"只更新到 2026-01-27" 说法的来源**：tushare_data 目录中存在 **三种分区格式** 的历史遗留数据，其中 `date=` 格式（共 5478 个分区）恰好在 2026-01-26~27 停止更新，这可能是误判的来源。

---

## 2. tushare_data 目录分区格式历史

tushare_data 的 `daily/` 目录下有三种格式的分区并存：

| 格式 | 数量 | 数据截止日期 | 来源 |
|------|------|-------------|------|
| `year=YYYY` | 37 | ~2000 年代 | 最早的全量批量下载 |
| `date=TS_CODE` | 5478 | **2026-01-26~27** | 中期下载格式（已废弃） |
| `ts_code=TS_CODE` | 5534 | **2026-06-11** | 当前增量更新格式（活跃） |

**同一只股票在两种格式中都存在**，例如 `600519.SH`：
- `date=600519.SH/data.parquet` → trade_date: 20010827 ~ **20260126**（旧）
- `ts_code=600519.SH/data.parquet` → trade_date: 20010827 ~ **20260611**（新）

LEAN 引擎的 `TushareDataConverter`（行 697/703）和 `convert_to_lean.py` 均使用 `ts_code=` 格式，**不受旧 `date=` 分区影响**。

---

## 3. 各数据类别更新状态详情

### 3.1 更新到 2026 年 6 月（活跃维护，共 92 个目录）

| API | 日期列 | 最新日期 | 备注 |
|-----|--------|----------|------|
| **daily** | trade_date | 20260611 | A 股日线行情（核心） |
| **daily_basic** | trade_date | 20260611 | 日线指标（PE/PB/换手率等） |
| **adj_factor** | trade_date | 20260611 | 复权因子 |
| **moneyflow** | trade_date | 20260123 | ⚠️ 仅到 1 月，同旧格式截止 |
| **stk_limit** | trade_date | 20260126 | ⚠️ 仅到 1 月 |
| **suspend_d** | trade_date | 20260611 | 停牌信息 |
| **index_daily** | trade_date | 20260612 | 指数日线 |
| **index_weight** | trade_date | 20260611 | 指数权重 |
| **fund_daily** | trade_date | 20260612 | 基金日线 |
| **fund_adj** | trade_date | 20260612 | 基金复权 |
| **fut_daily** | trade_date | 20260612 | 期货日线 |
| **shibor** | date | 20260611 | 上海银行间同业拆放利率 |
| **limit_list_d** | trade_date | 20260611 | 涨跌停列表 |
| **ths_daily** | trade_date | 20260611 | 同花顺日线 |
| **tdx_daily** | trade_date | 20260611 | 通达信日线 |
| **sw_daily** | trade_date | 20260611 | 申万日线 |
| **ci_daily** | trade_date | 20260611 | 中信日线 |
| **hk_daily_adj** | trade_date | 20260611 | 港股日线（复权） |
| **hk_hold** | trade_date | 20260611 | 港股通持仓 |
| **weekly** | trade_date | 20260605 | 周线 |
| **monthly** | trade_date | 20260529 | 月线 |
| bak_basic | trade_date | 20260612 | 备用基本信息 |
| bak_daily | trade_date | 20260611 | 备用日线 |
| ccass_hold | trade_date | 20260611 | CCASS 持仓 |
| dc_daily | trade_date | 20260612 | 大单日线 |
| eco_cal | date | 20260612 | 经济日历 |
| etf_share_size | trade_date | 20260206 | ETF 份额（仅到 2 月） |
| moneyflow_hsgt | trade_date | 20260611 | 沪深港通资金流 |
| moneyflow_ths | trade_date | 20260612 | 同花顺资金流 |
| opt_daily | trade_date | 20260123 | 期权日线（仅到 1 月） |
| repo_daily | trade_date | 20260611 | 回购日线 |
| sge_daily | trade_date | 20260611 | 上金所日线 |
| stk_auction_c | trade_date | 20260608 | 收盘集合竞价 |
| stk_premarket | trade_date | 20260608 | 盘前数据 |

### 3.2 更新到 2026 年 1 月后停滞（共 ~20 个目录）

| API | 日期列 | 最新日期 | 可能原因 |
|-----|--------|----------|---------|
| **moneyflow** | trade_date | 20260123 | 旧分区格式遗留；ts_code= 格式可能更全 |
| **moneyflow_dc** | trade_date | 20260123 | 同上 |
| **stk_limit** | trade_date | 20260126 | 旧分区格式遗留 |
| **margin** | trade_date | 20260123 | 融资融券汇总 |
| **margin_detail** | trade_date | 20260123 | 融资融券明细 |
| **opt_daily** | trade_date | 20260123 | 期权日线 |
| **block_trade** | trade_date | 20260123 | 大宗交易 |
| **pledge_stat** | end_date | 20260123 | 股权质押统计 |
| **cb_daily** | trade_date | 20260123 | 可转债日线 |
| **shibor_lpr** | date | 20260120 | LPR 利率 |
| **shibor_quote** | date | 20260123 | SHIBOR 报价 |
| **etf_share_size** | trade_date | 20260206 | ETF 份额 |
| **new_share** | ipo_date | 20260202 | 新股 |
| **repurchase** | end_date | 20260127 | 回购 |

**共性特征**：这些数据的新分区（`ts_code=` 或 `year=`）大多在 2026-01-23~27 附近停止，与旧 `date=` 格式的截止点几乎一致。**根本原因：2026-01-27 左右增量调度器可能中断或 API 权限变更，之后重新恢复时，部分 API 的增量更新未能补齐缺口。**

### 3.3 更新到 2025 年（季报/年报类数据，共 21 个目录）

| API | 日期列 | 最新日期 | 说明 |
|-----|--------|----------|------|
| fina_indicator | end_date | 20250930 | 财务指标 → 2025Q3 |
| income | end_date | 20250930 | 利润表 → 2025Q3 |
| balancesheet | end_date | 20250930 | 资产负债表 → 2025Q3 |
| cashflow | end_date | 20250930 | 现金流量表 → 2025Q3 |
| express | end_date | 20251231 | 业绩快报 → 2025Q4 |
| forecast | end_date | 20251231 | 业绩预告 → 2025Q4 |
| dividend | end_date | 20250630 | 分红 → 2025H1 |
| top10_holders | end_date | 20250930 | 前十大股东 → 2025Q3 |
| top10_floatholders | end_date | 20250930 | 前十大流通股东 → 2025Q3 |
| cn_cpi | month | 202512 | CPI → 2025-12 |
| cn_gdp | quarter | 2025Q4 | GDP → 2025Q4 |
| cn_m | month | 202512 | 货币供应量 → 2025-12 |
| cn_ppi | month | 202512 | PPI → 2025-12 |

**说明**：这些是季报/月报类数据，2025Q4/2025-12 是目前可获得的最新数据，属于**正常**（A 股 2025 年报数据尚未全部披露，2026Q1 数据要到 4 月底才完整）。

### 3.4 更新到更早（可能长期未维护，共 14 个目录）

| API | 日期列 | 最新日期 | 说明 |
|-----|--------|----------|------|
| hk_daily | trade_date | 19901231 | ❌ 港股日线数据严重缺失 |
| us_daily | trade_date | 19901231 | ❌ 美股日线数据严重缺失 |
| disclosure_date | end_date | 20001231 | ❌ 披露日期严重滞后 |
| pledge_detail | end_date | 20190328 | 质押明细 |
| libor | date | 20200624 | LIBOR 已停报 |
| hibor | date | 20200624 | HIBOR 数据停滞 |
| bo_weekly | date | 20200203 | 银行间周报 |
| ggt_monthly | month | 202012 | 港股通月度 |

### 3.5 无日期列（基础信息类，共 28 个目录）

etf_basic, fund_basic, fut_basic, hk_basic, index_basic, index_member, opt_basic, stock_basic, stock_company, trade_cal 等基础/静态信息表，不按日期更新。

### 3.6 空目录（共 6 个）

a50_futures, announcements, cb_price_chg, fut_weekly_monthly, market-hours, stk_weekly_monthly

---

## 4. LEAN 转换数据状态

### 4.1 A 股日线（equity daily）

| 市场 | 总数 | ≥2026-06-01 | 2026-04~05 | 2026-01~03 | ≤2025-12 |
|------|------|------------|-----------|-----------|---------|
| SSE | 2582 | 2314 (89.6%) | 1 (0.0%) | 28 (1.1%) | 239 (9.3%) |
| SZSE | 3097 | 2887 (93.2%) | 9 (0.3%) | 4 (0.1%) | 197 (6.4%) |

**≤2025-12 的 436 只股票**：大部分是 ETF/基金代码（5xxxxx/1xxxxx），少量为已退市/长期停牌股票（如 600090、600823 等）。

### 4.2 利率数据（interest-rate/china）

`Data/alternative/interest-rate/china/interest-rate.csv`：**仅到 2026-01-23**，远落后于 tushare_data/shibor 的 2026-06-11。

**根因**：此 CSV 是一次性生成的，未纳入增量转换流程。tushare_data 中的 shibor 数据已到 6 月，但 LEAN 的利率 CSV 未同步更新。

---

## 5. 增量调度器状态

```
最后成功目标日期：20260612
最后尝试目标日期：20260616
最后状态：partial_failed（6 个 API 失败，1 个成功）
失败原因：Failed to fetch SSE trade calendar（Tushare API 不可用）
```

自 2026-06-12 以来，核心行情 API（daily, adj_factor, suspend_d, daily_basic, stk_limit, moneyflow）增量更新失败，原因是 Tushare API 的 `trade_cal` 接口返回空数据，导致无法判断交易日历。

---

## 6. "只更新到 2026-01-27" 说法的成因分析

1. **旧分区格式遗留**：`date=` 格式的 5478 个分区在 2026-01-26~27 停止更新。如果只扫描了这些分区（按字母排序时 `date=` 排在 `ts_code=` 前面），会得出"数据只到 1 月"的错误结论。

2. **部分 API 确实停滞在 1 月**：moneyflow、margin_detail、opt_daily、block_trade 等目录的 `ts_code=` 格式也仅在 2026-01-23 左右有数据，说明这些 API 在增量更新恢复后未被补齐。

3. **LEAN 利率数据未同步**：`interest-rate.csv` 停留在 2026-01-23，这也是一个佐证——转换流程在 1 月底中断后，部分辅助数据从未被重新转换。

4. **Tushare API 权限/可用性波动**：`jiaoch.site` 的 API 在 2026-01-27 之后可能经历过服务中断或权限变更，导致旧格式下载中断，之后切换到新的 `ts_code=` 格式增量下载，但部分 API 的历史缺口未被补齐。

---

## 7. 待修复项

| 优先级 | 问题 | 影响范围 | 修复建议 |
|--------|------|---------|---------|
| 🔴 P0 | LEAN 利率 CSV（2026-01-23） | Sharpe 计算依赖 SHIBOR 1Y | 重新运行 convert_to_lean.py 或写脚本从 tushare_data/shibor 同步 |
| 🔴 P0 | 增量调度器 API 失败 | 6 月 12 日后无新数据 | 检查 Tushare API 可用性，修复 trade_cal 接口 |
| 🟡 P1 | moneyflow 等仅到 1 月 | 资金流策略无法使用 1-6 月数据 | 对停滞 API 执行一次性全量补齐 |
| 🟡 P1 | hk_daily/us_daily 数据残缺 | 港股/美股策略不可用 | 执行专项全量下载 |
| 🟢 P2 | 旧 date= 分区清理 | 占用磁盘，可能造成混淆 | 确认 ts_code= 覆盖后删除 date= 分区 |
| 🟢 P2 | 436 只 ≤2025-12 的 LEAN CSV | 含已退市/ETF | 区分已退市（正常）vs 数据缺失（需补齐） |

---

## 8. 数据流全景

```
Tushare API (jiaoch.site)
    │
    ▼ incremental_scheduler.py (每日 16:00 CST 触发)
    │
tushare_data/ (parquet, ts_code= 分区)
    │   ├── daily/ → 2026-06-11 ✓
    │   ├── daily_basic/ → 2026-06-11 ✓
    │   ├── adj_factor/ → 2026-06-11 ✓
    │   ├── shibor/ → 2026-06-11 ✓
    │   ├── moneyflow/ → 2026-01-23 ⚠️
    │   └── ...
    │
    ├── convert_to_lean.py (手动/一次性)
    │
    ▼
Lean/Data/ (CSV, LEAN 格式)
    │   ├── equity/sse/daily/ → 2026-06-11 ✓ (89.6%)
    │   ├── equity/szse/daily/ → 2026-06-11 ✓ (93.2%)
    │   ├── alternative/interest-rate/china/ → 2026-01-23 ❌
    │   └── ...
    │
    ▼ TushareDataConverter (运行时)
    │
LEAN Engine (回测/实盘)
```
