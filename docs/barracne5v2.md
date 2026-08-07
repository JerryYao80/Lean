# Barra CNE5 V2 十五因子计算方法与数据来源分析

## 总览

Barra CNE5 V2在V1的10因子基础上，剔除无tushare数据支持的描述符（EPIBS、EGIBS、EGIBS_s），新增5个因子（moneyflow、quality、northbound、margin、chipcost），共15个因子、29个描述符。

计算流程：

```
tushare parquet → 描述符(raw) → winsorize z-score → 加权组合 → 因子 → 正交化 → 最终因子
```

- **winsorize z-score**: 截面标准化，clip mean±3σ后重新z-score
- **正交化**: 对有共线性的因子做截面回归取残差

---

## 原始10个因子（来自V1）

### 1. Beta (BETA) — 权重1.00

- **公式**: 加权回归 β = Σ(w·(r_i - r_f)(R_m - r_f)) / Σ(w·(R_m - r_f)²), 半衰期=63天
- **数据**: `daily.pct_chg` (个股收益率) + `index_daily.pct_chg` (沪深300收益率) + `shibor.1y` (无风险利率)
- **状态**: 真实数据，计算正确

### 2. Momentum (RSTR) — 权重1.00

- **公式**: 加权均值 Σ(w·(log(1+r_i) - log(1+r_f))) / Σ(w), 半衰期=126天，525天回看窗口，跳过最近21天
- **数据**: `daily.pct_chg` + `index_daily.pct_chg` + `shibor.1y`
- **状态**: 真实数据，计算正确

### 3. Size (LNCAP) — 权重1.00

- **公式**: ln(total_mv)
- **数据**: `daily_basic.total_mv`
- **状态**: 真实数据，计算正确

### 4. Earnings Yield (ETOP:0.35 + CETOP:0.65)

- **CNE5规范**: EPIBS(0.68) + ETOP(0.11) + CETOP(0.21)
- **V2实现**: ETOP(0.35) + CETOP(0.65)
- **ETOP**: TTM归母净利润 / 总市值 — `income.n_income_attr_p` + `daily_basic.total_mv` — 真实
- **CETOP**: TTM(归母净利润+折旧+摊销) / 总市值 — `income.n_income_attr_p` + `cashflow.depr_fa_coga_dpba` + `cashflow.amort_intang_assets` — 真实
- **EPIBS**: 分析师预测盈利/市值 — **缺失，无数据源**。权重被重新分配给ETOP和CETOP
- **状态**: EPIBS缺失后权重重分配，ETOP和CETOP是真实数据

### 5. Residual Volatility (DASTD:0.74 + CMRA:0.16 + HSIGMA:0.10) — 真实tushare数据

- **DASTD**(0.74): 加权标准差(超额收益), 半衰期=42天 — `daily.pct_chg` + `shibor.1y`
- **CMRA**(0.16): 12个月累计收益范围(max-min) — `daily.pct_chg` + `shibor.1y`
- **HSIGMA**(0.10): Beta回归残差标准差 — `daily.pct_chg` + `index_daily.pct_chg` + `shibor.1y`
- **状态**: 全部真实数据

**正交化**: resvol 对 beta 做截面回归取残差：`resvol = winsorize(resvol - β·beta)`

### 6. Growth (SGRO:0.43 + EGRO:0.57)

- **CNE5规范**: SGRO(0.18) + EGRO(0.24) + EGIBS(0.47) + EGIBS_s(0.11)
- **V2实现**: SGRO(0.43) + EGRO(0.57)
- **SGRO**: 近5年营收符号化对数增长斜率 — `income.revenue` — 真实
- **EGRO**: 近5年归母净利润符号化对数增长斜率 — `income.n_income_attr_p` — 真实
- **EGIBS**: 分析师预测长期盈利增长 — **缺失，无数据源**
- **EGIBS_s**: 分析师预测短期盈利增长 — **缺失，无数据源**
- **状态**: EGIBS/EGIBS_s缺失后权重重分配，SGRO和EGRO是真实数据

### 7. Book-to-Price (BTOP) — 权重1.00

- **公式**: 归母股东权益 / 总市值
- **数据**: `balancesheet.total_hldr_eqy_exc_min_int` + `daily_basic.total_mv`
- **状态**: 真实数据，计算正确

### 8. Leverage (MLEV:0.38 + DTOA:0.35 + BLEV:0.27) — 真实tushare数据

- **MLEV**(0.38): (市值+长借+短借) / 市值 — `daily_basic.total_mv` + `balancesheet.lt_borr/st_borr`
- **DTOA**(0.35): 总负债 / 总资产 — `balancesheet.total_liab/total_assets`
- **BLEV**(0.27): (账面权益+长借+短借) / 账面权益 — `balancesheet.total_hldr_eqy_exc_min_int/lt_borr/st_borr`
- **状态**: 全部真实数据

### 9. Liquidity (STOM:0.35 + STOQ:0.35 + STOA:0.30) — 真实tushare数据

- **STOM**(0.35): ln(21日成交量/流通股本)
- **STOQ**(0.35): ln(63日成交量/流通股本)
- **STOA**(0.30): ln(252日成交量/流通股本)
- **数据**: `daily.vol` + `daily_basic.float_share`
- **状态**: 全部真实数据

### 10. Non-linear Size (NLSIZE) — 权重1.00

- **公式**: LNCAP³ 对 LNCAP 的截面回归残差，再做winsorize z-score
- **数据**: `daily_basic.total_mv` (间接通过LNCAP)
- **状态**: 真实数据，计算正确

---

## 新增5个因子（V2独有）

### 11. Money Flow (MFNET:0.50 + BFRATIO:0.50)

#### MFNET — 资金净流入率

- **公式**: 最近30个交易日 `net_mf_amount / circ_mv` 的加权均值, 半衰期=10天
- **计算步骤**:
  1. 加载moneyflow数据(最近30个交易日): `moneyflow.net_mf_amount`
  2. 加载daily_basic(同期): `daily_basic.circ_mv`
  3. 按trade_date内连接合并
  4. 逐日: daily_ratio[t] = net_mf_amount[t] / circ_mv[t]
  5. MFNET = weighted_mean(daily_ratio, half_life=10)
- **数据**: `moneyflow.net_mf_amount` + `daily_basic.circ_mv`
- **最低要求**: 合并后至少5个有效数据点

#### BFRATIO — 大单净比

- **公式**: 最近30个交易日 `(buy_lg_amount - sell_lg_amount) / amount` 的加权均值, 半衰期=10天
- **计算步骤**:
  1. 加载moneyflow数据(最近30个交易日): `moneyflow.buy_lg_amount`, `moneyflow.sell_lg_amount`
  2. 加载daily数据(同期): `daily.amount`
  3. 按trade_date内连接合并
  4. 逐日: daily_ratio[t] = (buy_lg_amount[t] - sell_lg_amount[t]) / amount[t]
  5. BFRATIO = weighted_mean(daily_ratio, half_life=10)
- **数据**: `moneyflow.buy_lg_amount` + `moneyflow.sell_lg_amount` + `daily.amount`
- **最低要求**: 合并后至少5个有效数据点

**正交化**: moneyflow 对 liquidity 做截面回归取残差（资金流与换手率高度相关）

### 12. Quality (ROE:0.40 + GPM:0.30 + DTA:0.30)

#### ROE — 净资产收益率

- **公式**: 最新PIT财务数据中的净资产收益率
- **数据**: `fina_indicator.roe`
- **PIT逻辑**: 使用 `load_point_in_time()` 按 `ann_date <= trade_date` 过滤，取最新记录

#### GPM — 毛利率

- **公式**: 最新PIT财务数据中的毛利率
- **数据**: `fina_indicator.grossprofit_margin`
- **PIT逻辑**: 同ROE

#### DTA — 资产负债率(取负)

- **公式**: -fina_indicator.debt_to_assets (取负: 低负债=高质量)
- **数据**: `fina_indicator.debt_to_assets`
- **PIT逻辑**: 同ROE
- **注意**: 取负值，使低负债公司获得高质量评分

### 13. Northbound (HKRATIO:0.50 + NMFLOW:0.50)

#### HKRATIO — 港资持股比例

- **公式**: hk_hold.ratio的最新可用值
- **计算步骤**:
  1. 加载hk_hold(trade_date <= T): `hk_hold.ratio`
  2. 按trade_date排序取最新记录
- **数据**: `hk_hold.ratio`
- **特性**: 个股级数据，每只股票不同

#### NMFLOW — 北向资金净流入

- **公式**: 最近30个交易日 moneyflow_hsgt.north_money 的加权均值, 半衰期=10天
- **计算步骤**:
  1. 加载moneyflow_hsgt(最近30个交易日): `moneyflow_hsgt.north_money`
  2. NMFLOW = weighted_mean(north_money, half_life=10)
- **数据**: `moneyflow_hsgt.north_money`
- **特性**: 市场级数据，所有股票同值；与个股级HKRATIO组合后产生截面差异
- **最低要求**: 至少5个有效数据点

### 14. Margin (MGRATIO:0.50 + MGCHANGE:0.50)

#### MGRATIO — 融资买入比率

- **公式**: rzmre / (rzmre + rqye) (若分母>0)
- **数据**: `margin_detail.rzmre` (融资买入额) + `margin_detail.rqye` (融券余额)
- **PIT逻辑**: 按trade_date排序取最新记录

#### MGCHANGE — 融资余额变化

- **公式**: 最近30个交易日融资余额(rzye)逐日对数变化的加权均值, 半衰期=10天
- **计算步骤**:
  1. 加载margin_detail.rzye(最近30个交易日)
  2. 计算逐日对数变化: log_change[t] = ln(rzye[t]/rzye[t-1])
  3. MGCHANGE = weighted_mean(log_change, half_life=10)
- **数据**: `margin_detail.rzye` (融资余额)
- **最低要求**: 至少5个有效数据点，且rzye序列首值>0

**正交化**: margin 对 leverage 做截面回归取残差（融资融券比率与杠杆因子共线性）

### 15. Chip Cost (WINRATE:0.50 + CSPREAD:0.50)

#### WINRATE — 获利盘比例

- **公式**: cyq_perf.winner_rate的最新值
- **数据**: `cyq_perf.winner_rate`
- **PIT逻辑**: 按trade_date排序取最新记录

#### CSPREAD — 筹码集中度(取负)

- **公式**: -(cost_95pct - cost_5pct) / cost_50pct (若cost_50pct>0)
- **数据**: `cyq_perf.cost_5pct` + `cyq_perf.cost_50pct` + `cyq_perf.cost_95pct`
- **注意**: 取负值，使筹码分布紧凑(价差小)的公司获得高分
- **PIT逻辑**: 同WINRATE

---

## 数据依赖汇总

| tushare数据集 | 用到的字段 | 涉及因子 |
|--------------|-----------|---------|
| daily | pct_chg, vol, amount, pre_close, close | beta, momentum, resvol, liquidity, moneyflow(BFRATIO) |
| daily_basic | total_mv, circ_mv, float_share, turnover_rate | size, earnyld, btop, leverage, liquidity, moneyflow(MFNET) |
| index_daily | pct_chg, pre_close, close | beta, momentum, resvol |
| shibor | 1y | beta, momentum, resvol |
| income | revenue, n_income_attr_p | growth, earnyld(ETOP, CETOP) |
| cashflow | depr_fa_coga_dpba, amort_intang_assets | earnyld(CETOP) |
| balancesheet | total_hldr_eqy_exc_min_int, lt_borr, st_borr, total_assets, total_liab | btop, leverage |
| stock_basic | name, list_date | 辅助(上市天数、ST标记) |
| **moneyflow** | net_mf_amount, buy_lg_amount, sell_lg_amount | **moneyflow** |
| **fina_indicator** | roe, grossprofit_margin, debt_to_assets | **quality** |
| **hk_hold** | ratio | **northbound** |
| **moneyflow_hsgt** | north_money | **northbound** |
| **margin_detail** | rzye, rqye, rzmre | **margin** |
| **cyq_perf** | winner_rate, cost_5pct, cost_50pct, cost_95pct | **chipcost** |

粗体为V2新增的6个数据集。

---

## 正交化规则

| 因子 | 对谁正交 | 理由 |
|------|---------|------|
| resvol | beta | CNE5规范：波动因子中含市场成分，需剔除 |
| moneyflow | liquidity | 资金流与换手率高度相关 |
| margin | leverage | 融资融券比率与杠杆因子共线性 |

---

## 因子与描述符对照表

| 因子 | 描述符/权重 | 数据来源 | 真实/假 |
|------|-----------|---------|--------|
| beta | BETA:1.00 | daily+index_daily+shibor | **真实** |
| momentum | RSTR:1.00 | daily+index_daily+shibor | **真实** |
| size | LNCAP:1.00 | daily_basic.total_mv | **真实** |
| earnyld | ETOP:0.35 | income.n_income_attr_p+daily_basic | 真实 |
| | CETOP:0.65 | income+cashflow+daily_basic | 真实 |
| | ~~EPIBS~~:0 | 分析师一致预期 | **假(缺失)** |
| resvol | DASTD:0.74 | daily+shibor | **真实** |
| | CMRA:0.16 | daily+shibor | **真实** |
| | HSIGMA:0.10 | daily+index_daily+shibor | **真实** |
| growth | SGRO:0.43 | income.revenue | 真实 |
| | EGRO:0.57 | income.n_income_attr_p | 真实 |
| | ~~EGIBS~~:0 | 分析师一致预期 | **假(缺失)** |
| | ~~EGIBS_s~~:0 | 分析师一致预期 | **假(缺失)** |
| btop | BTOP:1.00 | balancesheet+daily_basic | **真实** |
| leverage | MLEV:0.38 | balancesheet+daily_basic | **真实** |
| | DTOA:0.35 | balancesheet | **真实** |
| | BLEV:0.27 | balancesheet | **真实** |
| liquidity | STOM:0.35 | daily+daily_basic | **真实** |
| | STOQ:0.35 | daily+daily_basic | **真实** |
| | STOA:0.30 | daily+daily_basic | **真实** |
| nlsize | NLSIZE:1.00 | daily_basic.total_mv | **真实** |
| moneyflow | MFNET:0.50 | moneyflow+daily_basic | **真实** |
| | BFRATIO:0.50 | moneyflow+daily | **真实** |
| quality | ROE:0.40 | fina_indicator | **真实** |
| | GPM:0.30 | fina_indicator | **真实** |
| | DTA:0.30 | fina_indicator | **真实** |
| northbound | HKRATIO:0.50 | hk_hold | **真实** |
| | NMFLOW:0.50 | moneyflow_hsgt | **真实** |
| margin | MGRATIO:0.50 | margin_detail | **真实** |
| | MGCHANGE:0.50 | margin_detail | **真实** |
| chipcost | WINRATE:0.50 | cyq_perf | **真实** |
| | CSPREAD:0.50 | cyq_perf | **真实** |

---

## 输出格式

每股一个CSV文件，保存在 `Data/alternative/barra-cne5v2-factors/{sse,szse}/daily/`。

CSV列（21列）：

```
trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,moneyflow,quality,northbound,margin,chipcost,total_mv,turnover_rate,listed_days,missing_factor_count,is_st
```

- 前16列：1个日期列 + 15个因子列
- 后5列：总市值、换手率、上市天数、缺失因子数、是否ST

---

## 与V1的差异

| 项目 | V1 | V2 |
|------|----|----|
| 因子数 | 10 | 15 |
| 描述符数 | 18 | 29 |
| earnyld权重 | ETOP:0.34, CETOP:0.66 | ETOP:0.35, CETOP:0.65 |
| growth权重 | SGRO:0.43, EGRO:0.57 | SGRO:0.43, EGRO:0.57 (不变) |
| 新增因子 | — | moneyflow, quality, northbound, margin, chipcost |
| 新增正交化 | — | moneyflow↔liquidity, margin↔leverage |
| 新增数据集 | — | moneyflow, fina_indicator, hk_hold, moneyflow_hsgt, margin_detail, cyq_perf |
| CSV列数 | 16 | 21 |
| 输出目录 | barra-cne5-factors | barra-cne5v2-factors |
