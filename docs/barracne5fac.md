# Barra CNE5 十因子计算方法与数据来源分析

## 计算方法与数据来源

### 1. Beta (BETA) — 真实tushare数据

- **公式**: 加权回归 β = Σ(w·(r_i - r_f)(R_m - r_f)) / Σ(w·(R_m - r_f)²), 半衰期=63天
- **数据**: `daily.pct_chg` (个股收益率) + `index_daily.pct_chg` (沪深300收益率) + `shibor.1y` (无风险利率)
- **状态**: 真实数据，计算正确

### 2. Momentum (RSTR) — 真实tushare数据

- **公式**: 加权均值 Σ(w·log(1+r_i) - log(1+r_f)) / Σ(w), 半衰期=126天，525天回看窗口，跳过最近21天
- **数据**: `daily.pct_chg` + `index_daily.pct_chg` + `shibor.1y`
- **状态**: 真实数据，计算正确

### 3. Size (LNCAP) — 真实tushare数据

- **公式**: ln(total_mv)
- **数据**: `daily_basic.total_mv`
- **状态**: 真实数据，计算正确

### 4. Earnings Yield (ETOP + CETOP) — 部分真实，部分缺失

- **CNE5规范**: EPIBS(0.68) + ETOP(0.11) + CETOP(0.21)
- **Builder实现**: ETOP(0.34) + CETOP(0.66)
- **ETOP**: TTM归母净利润 / 总市值 — `income.n_income_attr_p` + `daily_basic.total_mv` — 真实
- **CETOP**: TTM(归母净利润+折旧+摊销) / 总市值 — `income.n_income_attr_p` + `cashflow.depr_fa_coga_dpba` + `cashflow.amort_intang_assets` — 真实
- **EPIBS**: 分析师预测盈利/市值 — **缺失，无数据源**。权重被重新分配给ETOP和CETOP
- **状态**: EPIBS是**假数据**（缺失后权重重分配），ETOP和CETOP是真实数据

### 5. Residual Volatility (DASTD + CMRA + HSIGMA) — 真实tushare数据

- **DASTD**(0.74): 加权标准差(超额收益), 半衰期=42天 — `daily.pct_chg` + `shibor.1y`
- **CMRA**(0.16): 12个月累计收益范围(max-min) — `daily.pct_chg` + `shibor.1y`
- **HSIGMA**(0.10): Beta回归残差标准差 — `daily.pct_chg` + `index_daily.pct_chg` + `shibor.1y`
- **状态**: 全部真实数据

**注意**: `_compose_and_standardize()`中，resvol最终做了正交化: `resvol = winsorize(resvol - β·beta)`，这是正确的CNE5规范

### 6. Growth (SGRO + EGRO) — 部分真实，部分缺失

- **CNE5规范**: SGRO(0.18) + EGRO(0.24) + EGIBS(0.47) + EGIBS_s(0.11)
- **Builder实现**: SGRO(0.43) + EGRO(0.57)
- **SGRO**: 近5年营收对数增长斜率 — `income.revenue` — 真实
- **EGRO**: 近5年归母净利润对数增长斜率 — `income.n_income_attr_p` — 真实
- **EGIBS**: 分析师预测长期盈利增长 — **缺失，无数据源**
- **EGIBS_s**: 分析师预测短期盈利增长 — **缺失，无数据源**
- **状态**: SGRO和EGRO真实，EGIBS/EGIBS_s是**假数据**（缺失后权重重分配）

### 7. Book-to-Price (BTOP) — 真实tushare数据

- **公式**: 归母股东权益 / 总市值
- **数据**: `balancesheet.total_hldr_eqy_exc_min_int` + `daily_basic.total_mv`
- **状态**: 真实数据，计算正确

### 8. Leverage (MLEV + DTOA + BLEV) — 真实tushare数据

- **MLEV**(0.38): (市值+长借+短借) / 市值 — `daily_basic.total_mv` + `balancesheet.lt_borr/st_borr`
- **DTOA**(0.35): 总负债 / 总资产 — `balancesheet.total_liab/total_assets`
- **BLEV**(0.27): (账面权益+长借+短借) / 账面权益 — `balancesheet.total_hldr_eqy_exc_min_int/lt_borr/st_borr`
- **状态**: 全部真实数据

### 9. Liquidity (STOM + STOQ + STOA) — 真实tushare数据

- **STOM**(0.35): ln(21日成交量/流通股本)
- **STOQ**(0.35): ln(63日成交量/流通股本)
- **STOA**(0.30): ln(252日成交量/流通股本)
- **数据**: `daily.vol` + `daily_basic.float_share`
- **状态**: 全部真实数据

### 10. Non-linear Size (NLSIZE) — 真实tushare数据

- **公式**: LNCAP³ 对 LNCAP 的截面回归残差，再做winsorize z-score
- **数据**: `daily_basic.total_mv`
- **状态**: 真实数据，计算正确

## 总结

| 因子 | 描述符 | CNE5规范权重 | Builder实际权重 | 数据来源 | 真实/假 |
|------|--------|-------------|----------------|---------|--------|
| Beta | BETA | 1.00 | 1.00 | daily+index_daily+shibor | **真实** |
| Momentum | RSTR | 1.00 | 1.00 | daily+index_daily+shibor | **真实** |
| Size | LNCAP | 1.00 | 1.00 | daily_basic.total_mv | **真实** |
| Earnings Yield | EPIBS | 0.68 | **0** | 分析师一致预期 | **假(缺失)** |
| | ETOP | 0.11 | **0.34** | income.n_income_attr_p | 真实 |
| | CETOP | 0.21 | **0.66** | income+cashflow | 真实 |
| Residual Vol | DASTD | 0.74 | 0.74 | daily+shibor | **真实** |
| | CMRA | 0.16 | 0.16 | daily+shibor | **真实** |
| | HSIGMA | 0.10 | 0.10 | daily+index_daily+shibor | **真实** |
| Growth | SGRO | 0.18 | **0.43** | income.revenue | 真实 |
| | EGRO | 0.24 | **0.57** | income.n_income_attr_p | 真实 |
| | EGIBS | 0.47 | **0** | 分析师一致预期 | **假(缺失)** |
| | EGIBS_s | 0.11 | **0** | 分析师一致预期 | **假(缺失)** |
| Book-to-Price | BTOP | 1.00 | 1.00 | balancesheet+daily_basic | **真实** |
| Leverage | MLEV | 0.38 | 0.38 | balancesheet+daily_basic | **真实** |
| | DTOA | 0.35 | 0.35 | balancesheet | **真实** |
| | BLEV | 0.27 | 0.27 | balancesheet | **真实** |
| Liquidity | STOM | 0.35 | 0.35 | daily+daily_basic | **真实** |
| | STOQ | 0.35 | 0.35 | daily+daily_basic | **真实** |
| | STOA | 0.30 | 0.30 | daily+daily_basic | **真实** |
| Non-linear Size | NLSIZE | 1.00 | 1.00 | daily_basic.total_mv | **真实** |

**结论**: 10个因子中，**3个描述符是假数据**（EPIBS、EGIBS、EGIBS_s），全部因为依赖分析师一致预期数据而缺失。Builder将缺失权重重新分配给了同因子的其他描述符。磁盘上有`local_data/analyst_estimates/synthetic_consensus_estimates.csv`（合成数据），但Builder未使用。其余17个描述符全部使用真实tushare数据计算。