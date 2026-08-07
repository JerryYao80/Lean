# 本项目隐含波动率计算流程

**项目：** LEAN + A 股期权套利/IV 轮动系统
**核心模块：**
- C#：`Indicators/ImpliedVolatility.cs`（LEAN 原生）
- Python：`Scripts/ashare_implied_volatility.py`（A 股工具链）
**日期：** 2026-07-06

---

## 一、C# 原生实现（LEAN 引擎权威）

### 文件路径

- **核心指标类**：`Indicators/ImpliedVolatility.cs`（377 行）
- **定价公式辅助**：`Indicators/OptionGreekIndicatorsHelper.cs`
  - `BlackTheoreticalPrice()` — BSM 定价（第 36 行）
  - `CRRTheoreticalPrice()` — 二叉树 Cox-Ross-Rubinstein（第 82 行）
  - `ForwardTreeTheoreticalPrice()` — ForwardTree 二叉树（第 101 行）
- **enum**：`Common/Indicators/OptionPricingModelType.cs`（3 种模型）
- **入口 API**：`Algorithm/QCAlgorithm.Indicators.cs:1197-1206`（`IV()` 辅助）
- **消费者示例**：
  - `Algorithm.CSharp/AShareOptionVolatilityArbitrageAlgorithm.cs:212`
  - `Algorithm.CSharp/AShareIVExportAlgorithm.cs:149`
  - `Algorithm.CSharp/AShareHOVixAlgorithm.cs:117`

### 使用的数据

| 数据源 | 数据项 | 来源 | 说明 |
|---|---|---|---|
| **期权行情** | `Price.Current.Value`（optionPrice） | `Data.Subscribers`（TradeBar/QuoteBar） | 期权当日结算价或成交价 |
| **标的价格** | `UnderlyingPrice.Current.Value` | `Securities[symbol].Price` | 标的 ETF 每日收盘价 |
| **执行价** | `Strike` | `Security.Symbol.OptionStrike` | 期权合约行权价 |
| **到期时间** | `Expiry` | `Security.Symbol.OptionExpiration` | 到期日，转换为剩余年数 |
| **权利方向** | `Right` | `Security.Symbol.OptionRight` | Call 或 Put |
| **无风险利率** | `RiskFreeRate.Current.Value` | `IRiskFreeInterestRateModel.GetInterestRate(time)` | 默认 5%，A 股算法中可替换为 SHIBOR |
| **股息率** | `DividendYield.Current.Value` | `IDividendYieldModel.GetDividendYield(time, underlyingPrice)` | 默认 0 |
| **镜像合约** | `Price.Current.Value`（mirror） | `OppositePrice`（同到期/反权利 Call↔Put） | 用于 OTM 平滑，基于一价定律 |

### 计算流程

#### 阶段 1：指标更新（`ComputeIndicator()`）

**入口：** `ImpliedVolatility.cs:257-268`

```csharp
protected override decimal ComputeIndicator()
{
    var time = Price.Current.EndTime;
    RiskFreeRate.Update(time, _riskFreeInterestRateModel.GetInterestRate(time));
    DividendYield.Update(time, _dividendYieldModel.GetDividendYield(time, UnderlyingPrice.Current.Value));

    var timeTillExpiry = Convert.ToDecimal(
        OptionGreekIndicatorsHelper.TimeTillExpiry(Expiry, time));
    _impliedVolatility = CalculateIV(timeTillExpiry);
    return _impliedVolatility;
}
```

每个时间片触发：更新利率/股息率 → 计算剩余年数 → 求 IV。

#### 阶段 2：定价模型分派（`CalculateTheoreticalPrice()`）

**位置：** `ImpliedVolatility.cs:280-295`

```csharp
return optionModel switch
{
    OptionPricingModelType.BinomialCoxRossRubinstein =>
        OptionGreekIndicatorsHelper.CRRTheoreticalPrice(...),
    OptionPricingModelType.ForwardTree =>
        OptionGreekIndicatorsHelper.ForwardTreeTheoreticalPrice(...),
    _ => OptionGreekIndicatorsHelper.BlackTheoreticalPrice(...),  // 默认 BSM
};
```

**默认 Black-Scholes-Merton**（带连续股息率 q）。BSM 公式（`OptionGreekIndicatorsHelper.cs:36-58`）：

```
d1 = [ln(S/K) + (r - q + σ²/2)·T] / (σ·√T)
d2 = d1 - σ·√T
Call = S·e^(-qT)·N(d1) - K·e^(-rT)·N(d2)
Put  = K·e^(-rT)·N(-d2) - S·e^(-qT)·N(-d1)
```

`N()` 用 `MathNet.Numerics.Distributions.Normal.CumulativeDistribution()`。
CRR 二叉树用 200 步（`Steps = 200`，`OptionGreekIndicatorsHelper.cs:31`）。

#### 阶段 3：求根求 IV（`CalculateIV()`，Brent 法）

**位置：** `ImpliedVolatility.cs:335-353`

```csharp
Func<double, double> f = (vol) =>
    CalculateTheoreticalPrice(vol, underlyingPrice, strike, timeTillExpiry,
        riskFreeRate, dividendYield, right, optionModel) - optionPrice;
impliedVol = Convert.ToDecimal(
    Brent.FindRoot(f, lowerBound, upperBound, accuracy, 100));
```

- **求解器**：`MathNet.Numerics.RootFinding.Brent.FindRoot`
- **目标函数**：`f(σ) = 理论价(σ) - 市场价`
- **边界**（`GetRootFindingMethodParameters`, lines 355-375）：
  - `lowerBound = 1e-7`
  - `upperBound = 4.0`（即 400% 波动率上限）
  - `accuracy = max(1e-4, 1e-4 × optionPrice)`
  - 最大 100 次迭代
- **非 BSM 模型时的两步法**：先用 BSM 算 IV 当初值，再把 bracket 收窄到 `[0.5×, 1.5×]` 初值（lines 365-374），加速收敛
- **失败处理**：catch 异常 → 打日志 `"ImpliedVolatility.CalculateIV(): Fail to converge, returning 0."` → 返回 0

#### 阶段 4：镜像合约平滑（可选）

**位置：** `ImpliedVolatility.cs:314-330` + `SmoothingFunction`（lines 58-69）

若传入 `mirrorOption`（同到期、反权利的 Call↔Put），同时算两边的 IV，然后按 OTM 约定取值：

```csharp
SmoothingFunction = (impliedVol, mirrorImpliedVol) =>
{
    if (Strike > UnderlyingPrice && Right == OptionRight.Put)
        return mirrorImpliedVol;   // OTM put 用 call 的 IV
    else if (Strike < UnderlyingPrice && Right == OptionRight.Call)
        return mirrorImpliedVol;   // OTM call 用 put 的 IV
    return impliedVol;
};
```

逻辑：基于一价定律（call/put 同 IV），用 OTM 合约的 IV（流动性更好、extrinsic value 占比高）作为最终值。

#### 完整调用链

```
QCAlgorithm.IV(symbol, mirrorOption, riskFreeRate, dividendYield, optionModel)
  → new ImpliedVolatility(...)
    → ComputeIndicator()                        [每个时间片]
      → RiskFreeRate.Update / DividendYield.Update
      → CalculateIV(timeTillExpiry)
        → CalculateIV(symbol, strike, ..., optionModel)
          → GetRootFindingMethodParameters       [设边界]
          → Brent.FindRoot(f, lower, upper, accuracy, 100)
            → f(σ) = CalculateTheoreticalPrice(σ, ...) - optionPrice
              → BlackTheoreticalPrice / CRR / ForwardTree
          → SmoothingFunction (若 UseMirrorContract)
        → 返回 _impliedVolatility
```

---

## 二、Python 平行实现（A 股期权工具链）

### 文件路径

- **核心模块**：`Scripts/ashare_implied_volatility.py`（1024 行）
- **导出脚本**：`Scripts/export_ashare_implied_volatility_data.py`
- **数据接入**：`Algorithm.CSharp/AShareImpliedVolatilityData.cs`（BaseData 子类）
- **策略消费**：`AShareImpliedVolatilityRotationAlgorithm.cs` / `AShareImpliedVolatilitySignalModel.cs`
- **单元测试**：`Tests/Python/Scripts/AShareImpliedVolatilityTests.py`

### 使用的数据（tushare 本地 parquet）

| 数据源 | 表名 | 路径 | 关键字段 |
|---|---|---|---|
| **期权合约信息** | `opt_basic` | `tushare_data_v2/opt_basic/data.parquet` | `opt_code`, `ts_code`, `call_put`, `exercise_price`, `maturity_date`, `per_unit` |
| **期权日线行情** | `opt_daily` | `tushare_data_v2/opt_daily/trade_date=YYYYMMDD/data.parquet` | `ts_code`, `trade_date`, `settle`（结算价）, `exchange` |
| **标的 ETF 价格** | `fund_daily` | `tushare_data_v2/fund_daily/ts_code=XXXXXX.SH/data.parquet` | `trade_date`, `close` |
| **无风险利率** | `shibor` | `tushare_data_v2/shibor/year=YYYY/data.parquet` | `date`, `1y`（1 年期 SHIBOR，单位 %） |

**标的覆盖**（`UNDERLYING_MAP`）：
- `OP510050.SH` → `510050.SH`（50ETF）
- `OP510300.SH` → `510300.SH`（300ETF）
- `OP510500.SH` → `510500.SH`（500ETF）
- `OP588000.SH` → `588000.SH`（科创 50ETF）
- `OP588080.SH` → `588080.SH`（科创 50ETF）

**仅处理 SSE 期权**（`opt_daily` 过滤 `exchange == "SSE"`，因无 SZSE opt_basic）。

### 计算流程

#### 阶段 1：数据加载（`run_pipeline()`）

**位置：** `ashare_implied_volatility.py:871-974`

```python
opt_basic = load_opt_basic(tushare_data_path)           # 合约信息
opt_daily = load_opt_daily(...)                         # 所有日期的日线
shibor_df = load_shibor(tushare_data_path)              # SHIBOR 利率
underlying_prices = load_underlying_price(...)          # ETF 收盘价
```

#### 阶段 2：单日 IV 计算（`compute_daily_iv()`）

**位置：** lines 411-539

1. **筛选当日本标的合约**：`opt_basic[opt_code == underlying_code]` 与 `opt_daily` merge
2. **计算 DTE**：`days_to_maturity(trade_date, maturity_date)`，过滤 `dte > 0`
3. **选近月/次月**：
   - 近月：DTE ∈ [23, 37] 天
   - 次月：DTE > 近月的下一个到期
   - 兜底：取最近两个到期
4. **ATM IV**：`compute_atm_iv()` — 取标的价格 ±5% 内的合约，逐个用 Newton-Raphson 求 IV，取中位数
5. **25-delta skew**：`compute_25delta_iv()` — 近似 25-delta 行权价，取最近 3 个合约 IV 的中位数，`skew = IV_put_25d - IV_call_25d`
6. **IV 曲面偏度**：`compute_iv_surface_skew()` — 按到期分期限，行权价分四分位，`skew = mean(低行权价 IV) - mean(高行权价 IV)`，按 1/DTE 加权
7. **VIX-like 指数**：CBOE model-free 方差分解法（见阶段 4）

#### 阶段 3：单合约 IV（Newton-Raphson，`implied_vol_newton()`）

**位置：** lines 96-139

```python
def implied_vol_newton(option_price, S, K, T, r, call_put, q=0.0,
                      max_iter=50, tol=1e-8):
    if T <= 1e-10 or option_price <= 0:
        return None
    # 内含价值检查
    intrinsic = max(S - K, 0.0) if call_put == "C" else max(K - S, 0.0)
    if option_price < intrinsic - 0.0001:
        return None

    sigma = 0.3  # 初值
    for _ in range(max_iter):
        model_price = bs_call_price(...) if call_put == "C" else bs_put_price(...)
        diff = model_price - option_price
        if abs(diff) < tol:
            return sigma
        vega = bs_vega(S, K, T, r, sigma, q)
        if vega < 1e-12:
            return None
        sigma -= diff / vega          # Newton 更新
        if sigma <= 0: sigma = 0.001
        if sigma > 5.0: return None

    if abs(diff) < option_price * 0.01:
        return sigma                   # 容忍 1% 误差
    return None
```

- **定价模型**：Black-Scholes（带股息 q），`scipy.stats.norm.cdf/pdf`
- **求根方法**：Newton-Raphson，用 vega 当导数
- **初值**：σ₀ = 0.3
- **容差**：1e-8，最多 50 次迭代
- **边界保护**：σ ≤ 0 → 重置为 0.001；σ > 5.0 → 返回 None
- **IV 合理范围过滤**：`0.01 < iv < 3.0`（`compute_atm_iv` 等处）

#### 阶段 4：VIX-like model-free IV（CBOE 方法）

**位置：** `compute_vix_for_term()` lines 146-219，`find_forward_price()` lines 222-243

1. **求远期价 F**：put-call parity，取 |call - put| 最小的 ATM 行权价
   `F = K + e^(rT)·(C - P)`
2. **找 K₀**：低于 F 的最高行权价
3. **方差分解**（CBOE 公式）：
   ```
   σ² = (2/T)·Σ [ΔK / K²]·e^(rT)·Q(K) - (1/T)·[F/K₀ - 1]²
   ```
   - ΔK：相邻行权价间距的一半
   - Q(K)：OTM 期权中间价（K<K₀ 用 put，K>K₀ 用 call，K=K₀ 用 call/put 均值）
4. **30 天插值**：近月/次月方差按时间加权
   ```
   σ²₃₀ = w₁·σ²_near·(T_near/t₃₀) + w₂·σ²_next·(T_next/t₃₀)
   VIX = √(σ²₃₀) × 100
   ```
   - `t₃₀ = 30/242`（年化，242 交易日/年）
   - `w₁ = (T_next - t₃₀)/(T_next - T_near)`，`w₂ = 1 - w₁`

#### 阶段 5：写入 InfluxDB

**位置：** `iv_result_to_line()` / `vix_result_to_line()` / `write_lines_to_influx()` lines 772-857

- 时间戳：交易日 15:00 北京时间 → UTC 纳秒
- measurement：
  - `lean_ashare_iv`：atm_iv、iv_call_25delta、iv_put_25delta、skew、iv_skew_surface_minus、skew_near_term、skew_next_term
  - `lean_ashare_vix`：vix、sigma_near、sigma_next、t_near、t_next
  - `lean_ashare_iv_skew`：偏度时间序列
- 批量写入：每批 5000 行，HTTP POST 到 InfluxDB v2 `/api/v2/write`

#### 完整调用链

```
main() / run_pipeline()
  → load_opt_basic / load_opt_daily / load_shibor / load_underlying_price
  → for trade_date in trade_dates:
      → r = get_risk_free_rate(shibor_df, trade_date)    # 1Y SHIBOR / 100
      → compute_daily_iv(trade_date, ...)
        → compute_atm_iv()       → implied_vol_newton()  (BSM + Newton)
        → compute_25delta_iv()   → implied_vol_newton()
        → compute_iv_surface_skew() → implied_vol_newton()
        → find_forward_price()   (put-call parity)
        → compute_vix_for_term() (CBOE 方差分解, 近月+次月)
        → 30 天插值 → VIX
  → iv_result_to_line() / vix_result_to_line()
  → write_lines_to_influx()  (批量 5000 行/批)
```

---

## 三、两套实现的对照

| 维度 | C# `ImpliedVolatility.cs` | Python `ashare_implied_volatility.py` |
|---|---|---|
| **定位** | LEAN 引擎内、算法实时计算 | 离线批处理、出日度 CSV/InfluxDB |
| **定价模型** | BSM（默认）/ CRR 二叉树 / ForwardTree | BSM（带股息 q） |
| **求根方法** | **Brent**（MathNet.Numerics） | **Newton-Raphson**（vega 为导数） |
| **初值/边界** | bracket [1e-7, 4.0]，accuracy 自适应 | σ₀=0.3，迭代 50 次，σ∈(0, 5.0] |
| **期权价格** | 实时行情（QuoteBar/TradeBar） | 日线结算价 `settle` |
| **无风险利率** | 模型注入（默认 5% 常数） | 1Y SHIBOR（tushare `shibor` 表，兜底 2%） |
| **股息率** | 模型注入（默认 0） | 0（ETF 期权，q=0） |
| **额外产出** | 单合约 IV + 镜像平滑 | ATM IV + 25-delta skew + 曲面偏度 + VIX |
| **失败处理** | 返回 0 + 日志 | 返回 None + 跳过 |
| **典型消费者** | `AShareOptionVolatilityArbitrageAlgorithm`、Greeks 指标 | `AShareImpliedVolatilityData`（CSV 注入）→ IV 轮动策略 |

---

## 四、auto_optimize 层如何使用 IV

`Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml` **不自己计算 IV**，只消费上述两套实现产出的信号：

- `iv-rv-z-score-threshold`：IV 与已实现波动率差值的 z-score 阈值
- `ivts-threshold`：IV 期限结构（近月/次月比）阈值
- `skew-percentile-high/low`：25-delta skew 历史分位数

回测时通过 `lean_runner.py` 调 LEAN，IV 数字来自 C# 指标或 Python 导出器，不在 auto_optimize 内重新求解。

---

## 五、关键参数速查

### C# Brent 求根
- `lowerBound = 1e-7`
- `upperBound = 4.0`
- `accuracy = max(1e-4, 1e-4 × optionPrice)`
- 最大迭代 100 次
- 非 BSM 模型：bracket 收窄到 `[0.5×, 1.5×]` BSM 初值

### Python Newton-Raphson
- `σ₀ = 0.3`
- `max_iter = 50`
- `tol = 1e-8`
- σ ≤ 0 → 重置 0.001；σ > 5.0 → 返回 None
- IV 合理范围 `0.01 < iv < 3.0`

### 共享参数
- 年化交易日：`TRADING_DAYS_PER_YEAR = 242`（Python）
- CRR 二叉树步数：`Steps = 200`（C#）
- VIX 期限窗口：近月 DTE ∈ [23, 37]，30 天插值

---

## 六、数据流总览

```
tushare 下载（tushare_worker daemon）
  ↓
tushare_data_v2/（parquet）
  ├─ opt_basic      ─┐
  ├─ opt_daily      ─┤
  ├─ fund_daily     ─┼─→ Python ashare_implied_volatility.py
  ├─ shibor         ─┘     ├─ BSM + Newton-Raphson（单合约 IV）
  │                        ├─ CBOE 方差分解（VIX）
  │                        └─ InfluxDB（lean_ashare_iv / vix / skew）
  │                                          ↓
  │                        Grafana 仪表盘（IV/VIX/skew 可视化）
  │
  └─ LEAN 行情数据 ─→ C# ImpliedVolatility 指标
                        ├─ BSM + Brent（单合约 IV）
                        ├─ 镜像合约 OTM 平滑
                        └─ Greeks（Delta/Gamma/Vega/Theta/Rho 复用 IV）
                                      ↓
                        AShareOptionVolatilityArbitrageAlgorithm
                        AShareIVExportAlgorithm
                        AShareHOVixAlgorithm
                                      ↓
                        auto_optimize option_vol_arb_5layer（消费 IV 信号）
```

---

## 来源文件

- `Indicators/ImpliedVolatility.cs`
- `Indicators/OptionGreekIndicatorsHelper.cs`
- `Common/Indicators/OptionPricingModelType.cs`
- `Algorithm/QCAlgorithm.Indicators.cs`
- `Scripts/ashare_implied_volatility.py`
- `Scripts/export_ashare_implied_volatility_data.py`
- `Algorithm.CSharp/AShareImpliedVolatilityData.cs`
- `Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml`
