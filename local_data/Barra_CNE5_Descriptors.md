# Barra China Equity Model (CNE5) - 完整因子定义

## 概述

CNE5模型包含10个风格因子，由21个描述子组成。本文档详细定义所有描述子及其在风格因子中的权重。

---

## 因子 1: Beta (市场风险)

**定义:** `1.00 · BETA`

### BETA (β)
- **计算方法:** 超额收益率对市场超额收益率的时间序列回归斜率系数
- **回归方程:**
  ```
  r_t - rf_t = α + β·R_t + e_t
  ```
  其中:
  - `r_t`: 股票在t日的收益率
  - `rf_t`: 无风险收益率
  - `R_t`: 市场加权超额收益率
  - `e_t`: 残差项
- **回溯期:** 252个交易日
- **半衰期:** 63个交易日

---

## 因子 2: Momentum (动量)

**定义:** `1.00 · RSTR`

### RSTR (Relative Strength - 相对强度)
- **计算方法:** 超额对数收益率的加权和
- **公式:**
  ```
  RSTR = Σ(t=L to T+L) w_t · [ln(1+r_t) - ln(1+rf_t)]
  ```
  其中:
  - `T = 504` 个交易日 (约2年)
  - `L = 21` 个交易日滞后期
  - `w_t`: 指数权重，半衰期126个交易日
- **说明:** 排除最近21天以避免短期反转效应

---

## 因子 3: Size (规模)

**定义:** `1.00 · LNCAP`

### LNCAP (Natural Log of Market Cap - 市值对数)
- **计算方法:** `ln(总市值)`
- **说明:** 捕捉公司规模效应

---

## 因子 4: Earnings Yield (盈利收益率)

**定义:** `0.68 · EPIBS + 0.11 · ETOP + 0.21 · CETOP`

### EPIBS (Analyst Predicted Earnings-to-Price)
- **定义:** 分析师预测的盈利收益率
- **数据来源:** 分析师一致预期

### ETOP (Trailing Earnings-to-Price)
- **计算方法:** `过去12个月盈利 / 当前市值`
- **盈利定义:** 最近财年盈利 + (当前中期盈利 - 上年同期中期盈利)

### CETOP (Cash Earnings-to-Price)
- **计算方法:** `过去12个月现金盈利 / 当前市值`
- **现金盈利:** 盈利 + 折旧 + 摊销

---

## 因子 5: Residual Volatility (残差波动率)

**定义:** `0.74 · DASTD + 0.16 · CMRA + 0.10 · HSIGMA`

### DASTD (Daily Standard Deviation - 日波动率)
- **计算方法:** 日超额收益率的标准差
- **回溯期:** 252个交易日
- **半衰期:** 42个交易日

### CMRA (Cumulative Range - 累积区间)
- **计算方法:** 过去12个月累积收益的最大值与最小值之差
- **公式:**
  ```
  Z(T) = Σ(τ=1 to T) [ln(1+r_τ) - ln(1+rf_τ)]
  CMRA = Z_max - Z_min
  ```
  其中:
  - `Z_max = max{Z(T)}`, T = 1,...,12
  - `Z_min = min{Z(T)}`, T = 1,...,12
  - 每月定义为21个交易日

### HSIGMA (Historical Sigma - 历史波动率)
- **计算方法:** Beta回归残差的波动率
- **公式:** `σ = std(e_t)` (来自Beta回归方程)
- **回溯期:** 252个交易日
- **半衰期:** 63个交易日

**注意:** 残差波动率因子与Beta正交化以减少共线性

---

## 因子 6: Growth (成长性)

**定义:** `0.18 · SGRO + 0.24 · EGRO + 0.47 · EGIBS + 0.11 · EGIBS_s`

### SGRO (Sales Growth - 销售增长)
- **计算方法:** 5年销售额对数对时间的回归斜率
- **最小数据要求:** 3年数据

### EGRO (Earnings Growth - 盈利增长)
- **计算方法:** 5年盈利对数对时间的回归斜率
- **最小数据要求:** 3年数据

### EGIBS (Predicted Earnings Growth - 预测盈利增长)
- **定义:** 分析师预测的长期盈利增长率
- **数据来源:** 分析师一致预期

### EGIBS_s (Short-term Predicted Earnings Growth - 短期预测盈利增长)
- **计算方法:** `(FY2预测EPS - FY1预测EPS) / |FY1预测EPS|`
- **说明:** 捕捉短期盈利增长预期

---

## 因子 7: Book-to-Price (账面市值比)

**定义:** `1.00 · BTOP`

### BTOP (Book-to-Price Ratio)
- **计算方法:** `普通股账面价值 / 市值`
- **说明:** 价值投资的经典指标

---

## 因子 8: Leverage (杠杆)

**定义:** `0.38 · MLEV + 0.35 · DTOA + 0.27 · BLEV`

### MLEV (Market Leverage - 市场杠杆)
- **计算方法:**
  ```
  (市值 + 优先股 + 长期债务 + 短期债务) / 市值
  ```

### DTOA (Debt-to-Assets - 负债资产比)
- **计算方法:** `总债务 / 总资产`

### BLEV (Book Leverage - 账面杠杆)
- **计算方法:**
  ```
  (账面权益 + 优先股 + 长期债务 + 短期债务) / 账面权益
  ```

---

## 因子 9: Liquidity (流动性)

**定义:** `0.35 · STOM + 0.35 · STOQ + 0.30 · STOA`

### STOM (Share Turnover 1 Month - 月换手率)
- **计算方法:** `ln(月成交量 / 流通股数)`
- **回溯期:** 1个月

### STOQ (Share Turnover 1 Quarter - 季换手率)
- **计算方法:** `ln(季度成交量 / 流通股数)`
- **回溯期:** 3个月

### STOA (Share Turnover 1 Year - 年换手率)
- **计算方法:** `ln(年成交量 / 流通股数)`
- **回溯期:** 12个月

---

## 因子 10: Non-linear Size (非线性规模)

**定义:** `1.00 · NLSIZE`

### NLSIZE (Non-linear Size)
- **计算方法:** 规模因子的立方（正交化后）
- **公式:** `NLSIZE = (LNCAP)³` (去除与LNCAP的相关性后)
- **说明:** 捕捉规模效应的非线性特征

---

## 数据需求汇总

### 价格数据 (日频)
- 股票收益率 (`r_t`)
- 无风险收益率 (`rf_t`)
- 市场加权指数收益率 (`R_t`)
- 成交量
- 流通股数

### 基本面数据 (季度/年度)
- 总市值
- 普通股账面价值
- 盈利 (过去12个月、财年、中期)
- 销售额 (5年历史)
- 现金流 (折旧、摊销)
- 总债务 (长期 + 短期)
- 总资产
- 优先股

### 分析师数据
- 预测EPS (FY1, FY2)
- 长期盈利增长预测
- 盈利收益率预测

### 回溯期汇总
- **21天:** 动量滞后期
- **42天:** DASTD半衰期
- **63天:** Beta半衰期、HSIGMA半衰期
- **126天:** RSTR权重半衰期
- **252天:** 1年日度数据
- **504天:** 2年动量数据
- **5年:** 成长因子数据

---

## 实现优先级建议

### Phase 1: 基础因子 (仅需价格数据)
1. **Beta** - 市场回归
2. **Momentum (RSTR)** - 历史收益
3. **Size (LNCAP)** - 市值
4. **Residual Volatility** - DASTD, CMRA, HSIGMA
5. **Liquidity** - STOM, STOQ, STOA

### Phase 2: 基本面因子 (需要财务数据)
6. **Earnings Yield** - ETOP, CETOP (EPIBS需要分析师数据)
7. **Book-to-Price (BTOP)**
8. **Leverage** - MLEV, DTOA, BLEV
9. **Growth** - SGRO, EGRO (EGIBS需要分析师数据)

### Phase 3: 高级因子 (需要分析师数据)
10. **Earnings Yield** - EPIBS
11. **Growth** - EGIBS, EGIBS_s
12. **Non-linear Size** - NLSIZE (需要正交化处理)

---

## 注意事项

1. **数据质量:** 确保财务数据的时点一致性，避免前视偏差
2. **缺失值处理:** 建立标准化的缺失值填充策略
3. **异常值处理:** 使用winsorization或标准化处理极端值
4. **因子标准化:** 所有因子需要标准化为均值0、标准差1
5. **正交化:** 残差波动率需要对Beta正交化
6. **行业中性化:** 考虑行业中性化处理以隔离纯因子效应

