# A股个股隐含波动率可行性分析

## 核心结论：无法直接计算个股IV，但有替代方案

---

## 一、为什么无法直接计算个股隐含波动率

**中国没有个股期权市场。** 现有期权数据全部是 ETF 期权：

| 交易所 | 品种 | 合约数 | 数据状态 |
|--------|------|--------|---------|
| SSE | 50ETF期权 | 6,878 | ✅ `opt_basic` + `opt_daily` |
| SSE | 300ETF期权(华泰柏瑞) | 2,664 | ✅ |
| SSE | 500ETF期权 | 1,428 | ✅ |
| SZSE | 300ETF期权(嘉实)/创业板ETF/中证1000ETF | 666 | ⚠️ 有数据但`opt_basic`缺映射 |
| SSE/SZSE | **个股期权** | **0** | ❌ **不存在** |

隐含波动率（IV）的定义是"从期权价格反推的市场波动率预期"。没有个股期权，就不存在个股IV。这是一个**市场结构限制**，不是数据或技术问题。

---

## 二、现有架构已经做了什么

项目已有完整的 ETF 期权 IV 计算管线（`Scripts/ashare_implied_volatility.py`，889行）：

```
opt_basic + opt_daily → Black-Scholes Newton-Raphson → ATM IV / 25Δ Skew / VIX
                                         ↓
                    Data/alternative/ashare-implied-volatility/sse/daily/
                    ├── 510050.csv (50ETF:  1,008行, 2020~2025)
                    ├── 510300.csv (300ETF: 1,005行, 2020~2025)
                    └── 510500.csv (500ETF:   343行, 2022~2025)
```

输出的列包括：`atm_iv`, `iv_call_25delta`, `iv_put_25delta`, `skew`, `vix` 等。

### 现有 IV 数据样例

```
trade_date, atm_iv, iv_call_25delta, iv_put_25delta, skew, term_days_near, term_days_next, option_count, vix, sigma_near, sigma_next, t_near, t_next
20250331,   0.1223, 0.1271,          0.1216,         -0.01, 23,            86,             80,          12.3, 12.1,       13.5,        0.095, 0.355
```

### 现有管线组件

| 组件 | 文件 | 功能 |
|------|------|------|
| IV 计算核心 | `Scripts/ashare_implied_volatility.py` | BS Newton-Raphson IV + VIX-like 模型无关IV |
| IV 数据导出 | `Scripts/export_ashare_implied_volatility_data.py` | 导出 LEAN 格式 CSV |
| IV 单元测试 | `Tests/Python/Scripts/AShareImpliedVolatilityTests.py` | 验证 IV 计算正确性 |
| 波动率偏斜策略 | `Results/ashare-volatility-skew-rotation/` | 基于 skew/IVTS 的回测结果 |
| SoloQuant 集成 | `Scripts/soloquant_pipeline_runner.py` (line 706) | 管线中调用 IV 导出 |

---

## 三、替代方案：从 ETF-IV 推导个股预期波动率

虽然不能直接获得个股 IV，但可以用以下方法**间接推导**：

### 方案 1：Beta 调整法（最简单可行）

**原理**：个股波动率 = 系统性波动率 + 特质波动率，系统性部分可用 ETF IV 近似

```python
# 个股预期波动率 = |β_i| × ETF_IV × (σ_i / σ_ETF)
import numpy as np

# 1. 计算个股 Beta（对沪深300）
stock_ret = daily['pct_chg'] / 100
market_ret = index_daily['pct_chg'] / 100  # 沪深300
beta = np.cov(stock_ret, market_ret)[0,1] / np.var(market_ret)

# 2. 历史波动率比值
sigma_stock = stock_ret.rolling(60).std() * np.sqrt(242)
sigma_etf = etf_ret.rolling(60).std() * np.sqrt(242)
vol_ratio = sigma_stock / sigma_etf

# 3. 映射 ETF IV
# 大盘股 → 300ETF IV, 中小盘 → 500ETF IV
if total_mv > 500e8:  # > 500亿
    base_iv = iv_300etf['atm_iv']
else:
    base_iv = iv_500etf['atm_iv']

# 4. 个股预期波动率
implied_vol_stock = abs(beta) * base_iv * vol_ratio
```

**所需数据**：`daily`（pct_chg）+ 已有 ETF IV CSV ✅ **全部具备**

**优点**：
- 实现极简（< 30行核心代码）
- 物理含义清晰：ETF IV 反映市场整体预期，β 调整反映个股暴露
- 可直接复用已有 ETF IV 数据

**缺点**：
- 仅捕获系统性波动率预期，忽略特质波动率预期
- β 估计有滞后性

---

### 方案 2：Parkinson / Garman-Klass 估计器（纯日线OHLC）

**原理**：利用日内最高价/最低价信息，比收盘价波动率更精确地估计"已实现波动率"

```python
import numpy as np

def parkinson_vol(high, low, window=20):
    """Parkinson (1980) 波动率估计器"""
    hl_ratio = np.log(high / low)
    sigma2 = (1 / (4 * window * np.log(2))) * hl_ratio.rolling(window).apply(lambda x: np.sum(x**2))
    return np.sqrt(sigma2 * 242)  # 年化

def garman_klass_vol(open, high, low, close, window=20):
    """Garman-Klass (1980) 波动率估计器"""
    hl = np.log(high / low)
    co = np.log(close / open)
    sigma2 = 0.5 * hl.rolling(window).apply(lambda x: np.sum(x**2)) \
           - (2*np.log(2) - 1) * co.rolling(window).apply(lambda x: np.sum(x**2))
    return np.sqrt(sigma2 / window * 242)  # 年化

# 使用
df = pd.read_parquet('daily/000001.SZ/data.parquet')
pk_vol = parkinson_vol(df['high'], df['low'], window=20)
gk_vol = garman_klass_vol(df['open'], df['high'], df['low'], df['close'], window=20)
```

**所需数据**：`daily` → `open`, `high`, `low`, `close` ✅ **全部具备**

**优点**：
- 不依赖期权数据，适用于所有 11,046 只股票
- Parkinson 比 close-to-close 效率高 5 倍（方差更小）
- Garman-Klass 进一步利用 O→C 信息，效率更高

**缺点**：
- 是"已实现波动率"而非"隐含/预期波动率"
- 无法反映市场对未来波动率的预期变化
- 日内高低价可能受开盘跳空影响

---

### 方案 3：GARCH(1,1) 模型波动率（最学术）

**原理**：GARCH 捕捉波动率聚集效应，给出**条件波动率预测**，这是非期权方法中最接近"隐含"概念的

```python
# GARCH(1,1): σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}
from arch import arch_model

def garch_volatility(returns, horizon=1):
    """GARCH(1,1) 条件波动率预测"""
    model = arch_model(returns * 100, vol='Garch', p=1, q=1, dist='normal')
    res = model.fit(disp='off')
    # 预测未来 horizon 天的波动率
    forecast = res.forecast(horizon=horizon)
    cond_vol = np.sqrt(forecast.variance.iloc[-1] / 10000) * np.sqrt(242)  # 年化
    return cond_vol

# EGARCH 捕捉杠杆效应（跌时波动率上升更多）
def egarch_volatility(returns, horizon=1):
    """EGARCH 捕捉非对称波动率响应"""
    model = arch_model(returns * 100, vol='EGARCH', p=1, q=1, dist='normal')
    res = model.fit(disp='off')
    forecast = res.forecast(horizon=horizon)
    cond_vol = np.sqrt(forecast.variance.iloc[-1] / 10000) * np.sqrt(242)
    return cond_vol
```

**所需数据**：`daily` → `pct_chg` ✅ **全部具备**
**额外依赖**：`arch` Python 包（需安装，`pip install arch`）

**优点**：
- 学术标准方法，广泛验证
- 捕捉波动率聚集和均值回归
- EGARCH 可捕捉杠杆效应（A股中显著：跌时波动率上升更多）
- 输出是**前瞻性**的条件波动率预测，最接近 IV 概念

**缺点**：
- 计算量大：11,046 只 × GARCH 拟合，全量计算约需 2~4 小时
- 需安装额外依赖 `arch`
- 模型可能不收敛（需异常处理）

---

### 方案 4：ETF IV 曲面映射（最精细）

**原理**：利用多个 ETF 的 IV 构建市值分位的波动率曲面，再根据个股特征映射

```python
def iv_surface_mapping(total_mv, beta, idio_vol, iv_50etf, iv_300etf, iv_500etf):
    """ETF IV 曲面映射到个股预期波动率"""

    # Step 1: 根据市值确定基准 ETF IV
    if total_mv > 2000e8:    # > 2000亿 → 大盘股
        base_iv = iv_50etf
        size_adj = 0.9       # 大盘股波动率通常略低于50ETF
    elif total_mv > 500e8:   # 500~2000亿 → 中大盘
        base_iv = iv_300etf
        size_adj = 1.0
    elif total_mv > 100e8:   # 100~500亿 → 中小盘
        base_iv = iv_500etf
        size_adj = 1.1       # 中小盘波动率通常高于500ETF
    else:                    # < 100亿 → 小盘
        base_iv = iv_500etf
        size_adj = 1.3       # 小盘股波动率显著高于500ETF

    # Step 2: Beta 调整系统性部分
    systematic_vol = abs(beta) * base_iv * size_adj

    # Step 3: 加入特质波动率（与系统性正交）
    # 特质波动率从 Parkinson/GARCH 估计
    total_vol = np.sqrt(systematic_vol**2 + idio_vol**2)

    return total_vol
```

**映射逻辑**：

| 市值区间 | 基准 IV | 规模调整 | ETF 来源 |
|----------|--------|---------|---------|
| > 2000亿 | 50ETF IV × 0.9 | 大盘波动率低于50ETF | 510050 |
| 500~2000亿 | 300ETF IV × 1.0 | 中性 | 510300 |
| 100~500亿 | 500ETF IV × 1.1 | 中小盘波动率高于500ETF | 510500 |
| < 100亿 | 500ETF IV × 1.3 | 小盘波动率显著高于500ETF | 510500 |

**所需数据**：已有 ETF IV CSV + `daily_basic` → `total_mv` + `daily` → OHLC ✅ **全部具备**

**优点**：
- 最精细：同时利用期权市场信息（ETF IV）和个股特征（β、市值、特质波动率）
- 物理含义：系统性预期波动率 + 特质波动率 = 总预期波动率
- 可直接用于 midsml2.md 中的条件性 IVOL 因子

**缺点**：
- 实现复杂度较高
- 规模调整系数（0.9/1.0/1.1/1.3）需用历史数据校准
- SZSE ETF 期权（创业板ETF/中证1000ETF）的 `opt_basic` 缺映射

---

## 四、方案对比与推荐

| 维度 | 方案1: Beta调整 | 方案2: Parkinson/GK | 方案3: GARCH | 方案4: IV曲面映射 |
|------|---------------|-------------------|-------------|-----------------|
| **所需数据** | daily + ETF IV | daily OHLC | daily | daily + daily_basic + ETF IV |
| **数据完备** | ✅ | ✅ | ✅ | ✅ |
| **实现复杂度** | 极低（<30行） | 极低（<20行） | 中（需arch包） | 中高（~80行） |
| **计算耗时** | 秒级 | 秒级 | 2~4小时(全量) | 分钟级 |
| **前瞻性** | ✅ 有（ETF IV是前瞻的） | ❌ 无（历史已实现） | ✅ 有（条件预测） | ✅ 有（ETF IV + 特质调整） |
| **个股特异性** | 中（仅β调整） | 中（OHLC信息） | 高（条件波动率） | 最高（β+市值+特质） |
| **适用范围** | 全市场 | 全市场 | 全市场 | 全市场 |
| **学术认可度** | 中 | 高 | 最高 | 中高 |

### 推荐实施路径

| 优先级 | 方案 | 目的 | 预计工作量 |
|--------|------|------|-----------|
| **P0** | Parkinson/GK 历史波动率 | 作为"已实现波动率"基线，替代简单收盘价std | 0.5天 |
| **P0** | Beta×ETF_IV 映射 | 作为"预期波动率"，直接替代 IV | 0.5天 |
| **P1** | GARCH(1,1) 条件波动率 | 最学术的前瞻性波动率，用于条件性IVOL | 1天（含arch包安装） |
| **P2** | ETF IV 曲面 + β 调整 | 最精细方案，整合所有信息源 | 1.5天 |

**建议**：先实施 P0 的两个方案（代码量 < 100行），生成每只股票的"预期波动率"字段，作为 `midsml2.md` 中条件性 IVOL 因子的替代输入。GARCH 和曲面映射作为后续增强。

---

## 五、与 midsml2.md 因子体系的衔接

### 条件性 IVOL 因子的数据输入

midsml2.md 中的条件性 IVOL 因子原文：

```python
# 计算 IVOL（残差波动率）
residuals = pct_chg - beta * market_return
ivol = residuals.rolling(20).std() * np.sqrt(252)

# 基本面催化事件
has_catalyst = (abs(surprise) > 10) | (q_sales_yoy > 30) | (rd_exp / revenue > 0.08)

# 条件性 IVOL
cond_ivol = ivol * has_catalyst * 1 - ivol * (~has_catalyst) * (-1)
```

**问题**：这里的 `ivol` 是**历史已实现**的异质波动率，不是隐含/预期波动率。

**改进**：用上述替代方案替换 `ivol`，使其具有前瞻性：

```python
# 改进方案：用 Beta×ETF_IV + Parkinson 特质波动率 替代简单 rolling std
# 1. 系统性预期波动率
systematic_expected = abs(beta) * etf_iv  # 方案1

# 2. 特质预期波动率（Parkinson 估计的已实现特质波动率作为代理）
residuals = pct_chg - beta * market_return
idio_vol_parkinson = parkinson_vol_from_residuals(residuals)  # 方案2

# 3. 总预期波动率
expected_vol = np.sqrt(systematic_expected**2 + idio_vol_parkinson**2)

# 4. 条件性预期波动率（替代原来的 cond_ivol）
cond_expected_vol = expected_vol * has_catalyst * 1 - expected_vol * (~has_catalyst) * (-1)
```

### 其他可增强的因子

| midsml2.md 因子 | 当前计算方式 | 可用替代方案增强 |
|----------------|------------|----------------|
| resvol（残差波动率） | `daily → pct_chg.rolling(20).std()` | → Parkinson/GK 估计器（效率更高） |
| 条件性 IVOL | `rolling(20).std() * sqrt(252)` | → Beta×ETF_IV + Parkinson 特质（前瞻性） |
| Amihud 非流动性 | `abs(pct_chg) / amount` | → 用 Parkinson vol 替代 pct_chg（更精确） |
| accumulation_score | 放量不涨信号 | → 加入 IV 斜率变化（ETF IV 上升=市场恐慌=吸筹窗口） |
| distribution_score | 量价背离 | → 加入 ETF VIX 飙升信号（市场恐慌=出货加速） |

---

## 六、SZSE 期权数据缺口

当前 `opt_basic` 仅包含 SSE 期权合约信息（10,970 份），SZSE 期权（666 份活跃合约）缺少合约基础信息映射。

### 缺失的 SZSE 期权品种

| 品种 | 交易所 | 状态 | 用途 |
|------|--------|------|------|
| 嘉实沪深300ETF期权 | SZSE | ⚠️ 有日线数据但缺opt_basic | 补量大盘预期波动率（深市视角） |
| 创业板ETF期权 | SZSE | ⚠️ 同上 | **中小盘预期波动率的最佳代理** |
| 中证1000ETF期权 | SZSE | ⚠️ 同上 | **中小盘预期波动率的最佳代理** |

**修复方式**：通过 tushare API 补充 SZSE `opt_basic` 数据：

```python
# 需要调用 tushare opt_basic API 并指定 exchange='SZSE'
import tushare as ts
pro = ts.pro_api('your_token')
szse_basic = pro.opt_basic(exchange='SZSE', fields='ts_code,name,call_put,exercise_price,maturity_date,list_date,delist_date')
```

**优先级**：中。创业板ETF期权和中证1000ETF期权的 IV 对中小盘策略最有价值，因为它们的标的成分股与中小盘策略的选股范围高度重合。

---

## 七、数据完备性总结

| 数据 | 来源 | 状态 | 用途 |
|------|------|------|------|
| ETF 期权日线 | `opt_daily` (1,864日分区) | ✅ 完备 | 计算 ETF IV |
| SSE 期权合约 | `opt_basic` (10,970份) | ✅ 完备 | 50ETF/300ETF/500ETF IV |
| SZSE 期权合约 | 缺失 | ❌ 需补充 | 创业板/中证1000 IV |
| 50ETF IV | `Data/alternative/.../510050.csv` | ✅ 1,008行 | 大盘预期波动率 |
| 300ETF IV | `Data/alternative/.../510300.csv` | ✅ 1,005行 | 中大盘预期波动率 |
| 500ETF IV | `Data/alternative/.../510500.csv` | ✅ 343行 | 中小盘预期波动率 |
| 日线OHLC | `daily` (11,046只) | ✅ 完备 | Parkinson/GK/GARCH |
| 日线指标 | `daily_basic` (11,047只) | ✅ 完备 | 市值、换手率 |
| SHIBOR | `shibor` (37年分区) | ✅ 完备 | 无风险利率 |
| 个股期权 | — | ❌ **中国市场不存在** | 无法直接计算个股IV |

---

*数据截至 2026-06-11，基于 /home/project/tushare-downloader/tushare_data 本地 Parquet 数据*
