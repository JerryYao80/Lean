# A 股隐含波动率计算修复设计（LEAN 原生架构）

**日期**: 2026-06-28
**分支**: fix/price-scaling-10000x
**主题**: 重构 `export_ashare_implied_volatility_data.py` 为 LEAN 原生架构，使用真实 tushare 数据 + LEAN 原生 BS/IV 算法

---

## 背景与问题

### 现状

当前 A 股隐含波动率计算由两个 Python 脚本承担：

- `Scripts/ashare_implied_volatility.py` — 自写 Black-Scholes 公式 + Newton-Raphson 求解器
- `Scripts/export_ashare_implied_volatility_data.py` — 离线批处理导出 LEAN 格式 CSV

### 审计发现的问题

#### 计算正确性问题

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | **T 用「日历天数 / 242 交易日」混用** | `days_to_maturity` 返回日历天数，`T = dte/242` 用交易日年化 | T 被放大（242 < 365），IV 系统性偏低约 15-20%。CBOE/LEAN 用日历天数/365 |
| 2 | **股息率 q=0** | 全程未设 q | 50/300/500ETF 有 1-2% 分红，忽略 q 高估远期，轻微偏置 IV |
| 3 | **近月/次月窗口 23-37 天** | `compute_daily_iv` | A 股月末到期常只有 1 个落窗，次月靠 fallback；VIX 经常算不出 |
| 4 | **用 settle 结算价** | ATM/skew 计算 | 可接受，结算价更稳健 |
| 5 | **数据停在 2025-04-01** | CSV 末尾 | opt_daily 实际有 2026 年数据，`prepare_iv_data` 管线阶段未更新 |

#### 架构问题（核心）

违反用户既定原则 `LEAN Native Only — never self-compute; derive from LEAN interfaces`：

- 自写 Python BS 求解器，重复造轮子且偏离 LEAN 权威实现
- LEAN 已内置 `Indicators/ImpliedVolatility.cs`（Brent 求解、含 q、日历/365）
- 当前 IV 数据造好但**没有任何策略实际消费**（半成品基础设施）

### LEAN 原生 IV 实现对比

`Indicators/ImpliedVolatility.cs` + `OptionGreekIndicatorsHelper.cs`：

```csharp
timeTillExpiry = (expiry - referenceDate).TotalDays / 365d;  // 日历天数/365
dividendYield  = _dividendYieldModel.GetDividendYield(...);   // 含股息率
impliedVol = Brent.FindRoot(f, lowerBound, upperBound, accuracy);  // Brent 求根
// 价格模型：BlackTheoreticalPrice（含 q 的完整 BSM）
```

**结论**：所谓"修 bug"，正确做法不是 patch Python 公式，而是改用 LEAN 原生 `ImpliedVolatility` 作为唯一真相来源。

---

## 三大设计原则

本设计严格遵循三条原则，所有决策以此为最高约束：

1. **满足 A 股市场实际要求** — 上交所实际合约代码、欧式期权、Asia/Shanghai 时区、结算价计算 IV
2. **使用 tushare_data 真实交易数据** — 直接读取 `opt_daily`/`opt_basic`/`shibor`/`fund_div` parquet，1:1 映射，不模拟不伪造
3. **使用 LEAN 原生优秀稳定算法** — `ImpliedVolatility`（Brent）+ CBOE model-free VIX，无自写 BS

---

## 架构设计

### 架构总览

#### 旧架构（待废弃）

```
tushare_data_v2/ → Python脚本（自写BS） → CSV → InfluxDB
```

#### 新架构（纯原生）

```
tushare_data_v2/
  ├─ opt_basic.parquet
  ├─ opt_daily/trade_date=*/data.parquet
  ├─ fund_daily/ts_code=*/data.parquet
  ├─ shibor/
  └─ fund_div/

转换器（ToolBox/AShareOptionDataConverter.cs）
  └─ 输出 Data/option/china/（LEAN 标准格式）

LEAN 原生引擎
  ├─ AddOption("510050", market: Market.China)
  ├─ ImpliedVolatility indicator（C# 原生，Brent 求解器）
  └─ 动态 IRiskFreeInterestRateModel / IDividendYieldModel

VIX 聚合器（C#）
  ├─ 从 OptionChain 读取真实 OTM 结算价
  ├─ 按 CBOE 白皮书公式计算 VIX
  └─ 输出 CSV + InfluxDB

输出（格式与现有完全兼容）
  ├─ Data/alternative/ashare-implied-volatility/.../510050.csv
  └─ InfluxDB: lean_ashare_iv, lean_ashare_vix, lean_ashare_iv_skew
```

### 核心改动

| 组件 | 旧 | 新 |
|------|-----|-----|
| BS 公式 | Python 自写（scipy） | C# 原生（MathNet.Numerics） |
| IV 求解 | Newton-Raphson（50 次） | Brent 算法（LEAN 原生） |
| T 计算 | 日历天数 / 242 | 日历天数 / 365（LEAN 原生） |
| 股息率 | 硬编码 0 | 动态 `IDividendYieldModel`（tushare fund_div） |
| 无风险利率 | 硬编码/简单 SHIBOR | 动态 `IRiskFreeInterestRateModel`（tushare shibor） |
| 数据源 | 直接读 parquet | LEAN 标准格式 + AddOption |

---

## 第 2 节：数据转换层（tushare → LEAN 格式）

### tushare 真实数据 schema（已确认）

```
opt_basic（10970 行）:
  ts_code          # 10010606.SH（上交所合约代码，8位数字）
  exchange         # SSE
  name             # 华夏上证50ETF期权2606认沽2.80
  per_unit         # 10000（合约单位）
  opt_code         # OP510050.SH（标的）
  opt_type         # ETF期权
  call_put         # C/P
  exercise_type    # 欧式
  exercise_price   # 2.800
  maturity_date    # 20260624
  list_date        # 20240126

opt_daily（1827 个交易日文件）:
  ts_code, trade_date, exchange
  pre_settle, pre_close, open, high, low
  close            # 收盘价
  settle           # 结算价（IV 计算必须用）
  vol, amount, oi  # 持仓量

覆盖范围: 2020-01-02 ~ 2026-12-31
```

### LEAN 期权数据格式（适配 A 股）

```
Data/option/china/
  ├─ daily/510050/{option_symbol}.csv     # 合约日线
  ├─ universes/510050/{date}.csv          # 每日合约列表
  └─ symbol-properties/                    # 合约属性
```

### 关键映射表

| tushare 字段 | LEAN 字段 | 转换逻辑 |
|-------------|----------|---------|
| `ts_code` (10010606.SH) | `symbol` | `Symbol.CreateOption(...)` 生成 |
| `exchange` (SSE) | `market` (china) | 硬编码 `Market.China` |
| `maturity_date` | `expiration` | YYYYMMDD → DateTime |
| `exercise_price` | `strike` | 直接映射，保留精度 |
| `call_put` (C/P) | `right` (Call/Put) | C → Call, P → Put |
| `per_unit` (10000) | 合约单位 | SymbolPropertiesDatabase |
| `settle` (结算价) | `Close` | **settle 写入 Close（IV 计算用）** |
| `oi` | `open_interest` | 质量监控 |

### Settlement 价格处理（关键决策）

**确认结论**：LEAN 原生 `TradeBar` 只有 OHLCV，**无 settlement 字段**；`Option.cs` 的 `SettlementModel` 是结算方式而非结算价。

**解决方案**：转换器把 tushare `settle`（结算价）写入 LEAN `Close` 字段。LEAN 原生 `ImpliedVolatility` 读取 `Price`（即 Close）反推 IV——数据真实 + 算法原生，二者兼顾。

**合理性**：A 股 ETF 期权 IV 计算应使用结算价（交易所每日官方公布，比收盘价更稳定、更抗操纵）。中金所官方 VIX 即用结算价。

### 转换器接口

```csharp
namespace QuantConnect.ToolBox
{
    public class AShareOptionDataConverter
    {
        public void ConvertToLeanFormat(string underlying = "510050");
        // 1. 加载 opt_basic（筛选 underlying + SSE）
        // 2. 遍历 opt_daily 日期文件
        // 3. 按合约分组，Symbol.CreateOption（欧式期权）
        // 4. 写日线 CSV（settle → Close）
        // 5. 写 universe CSV（每日合约列表）
    }
}
```

### 数据完整性验证

```python
# 转换前后对比
tushare_contracts = len(pd.read_parquet('opt_basic/opt_code=OP510050.SH'))
lean_contracts = len(glob.glob('Data/option/china/daily/510050/*.csv'))
assert lean_contracts >= tushare_contracts * 0.95  # 覆盖率 > 95%
```

---

## 第 3 节：期权数据加载（AddOption + Market.China）

### LEAN AddOption 底层流程

```
AddOption("510050", market: Market.China)
  ├─ 1. 创建 canonical option symbol
  ├─ 2. 注册 OptionChainProvider
  │      └─ GetOptionContractList(symbol, date) ← universe CSV
  ├─ 3. 应用 SetFilter（行权价/到期日范围）
  ├─ 4. 订阅每个合约日线数据
  └─ 5. 引擎构建 OptionChain，策略 OnData 读取
```

### 自定义 OptionChainProvider（A 股专用）

```csharp
namespace QuantConnect.Data.AShare
{
    public class AShareOptionChainProvider : IOptionChainProvider
    {
        public IEnumerable<Symbol> GetOptionContractList(Symbol symbol, DateTime date)
        {
            var underlyingTicker = (symbol.Underlying ?? symbol).Value;
            var universePath = $"{_dataFolder}/option/china/universes/" +
                              $"{underlyingTicker}/{date:yyyyMMdd}.csv";
            
            if (!File.Exists(universePath))
                universePath = FindNearestUniverse(underlyingTicker, date);  // 节假日处理
            
            // universe CSV: actual_code,expiration,strike,right,style
            foreach (var line in File.ReadAllLines(universePath).Skip(1))
            {
                var parts = line.Split(',');
                yield return Symbol.CreateOption(
                    symbol.Underlying ?? symbol,
                    Market.China,
                    OptionStyle.European,
                    parts[3] == "C" ? OptionRight.Call : OptionRight.Put,
                    decimal.Parse(parts[2]),
                    DateTime.ParseExact(parts[1], "yyyyMMdd", null)
                );
            }
        }
    }
}
```

### 动态利率模型（tushare 真实 SHIBOR）

LEAN 接口（已确认）：
```csharp
public interface IRiskFreeInterestRateModel
{
    decimal GetInterestRate(DateTime date);
}
```

实现：
```csharp
namespace QuantConnect.Data.AShare
{
    public class AShareShiborRateModel : IRiskFreeInterestRateModel
    {
        private readonly Dictionary<DateTime, decimal> _rates;
        
        public AShareShiborRateModel(string tusharePath)
        {
            // 加载 tushare shibor 真实数据
            // shibor 表: date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y
            _rates = LoadShiborFromParquet(tusharePath);
        }
        
        public decimal GetInterestRate(DateTime date)
        {
            // 用 1Y SHIBOR 作为无风险利率（A 股惯例）
            return _rates.TryGetValue(date.Date, out var rate) ? rate : 0.02m;
        }
    }
}
```

### 动态股息率模型（tushare 真实 fund_div）

LEAN 接口（已确认）：
```csharp
public interface IDividendYieldModel
{
    decimal GetDividendYield(DateTime date);
    decimal GetDividendYield(DateTime date, decimal securityPrice);
}
```

实现：
```csharp
namespace QuantConnect.Data.AShare
{
    public class AShareDividendYieldModel : IDividendYieldModel
    {
        private readonly Dictionary<DateTime, decimal> _yields;
        
        public AShareDividendYieldModel(string tusharePath, string underlying)
        {
            // 加载 ETF 分红记录，计算滚动 12 个月年化股息率
            // fund_div 表: ts_code, ex_date, div_cash
            _yields = ComputeRollingDividendYield(tusharePath, underlying);
        }
        
        public decimal GetDividendYield(DateTime date, decimal securityPrice)
        {
            return _yields.TryGetValue(date.Date, out var y) ? y : 0m;
        }
        
        public decimal GetDividendYield(DateTime date) => GetDividendYield(date, 0m);
    }
}
```

### 策略中使用 ImpliedVolatility（LEAN 原生）

```csharp
var iv = new ImpliedVolatility(
    contract.Symbol,
    rateModel,     // 动态 SHIBOR（真实）
    divModel,      // 动态股息率（真实）
    optionModel: OptionPricingModelType.BlackScholes
);
```

---

## 第 4 节：VIX 聚合导出（CBOE model-free）

### 设计决策：混合方案

- **ATM IV / 25-delta skew**：用 LEAN 原生 `ImpliedVolatility`（单合约，最需要原生算法的部分）
- **VIX**：用 CBOE model-free 公式（国际标准定义，输入为 LEAN 原生数据流提供的真实结算价 + 远期价）

VIX 用 model-free 而非单合约 IV 加权，因其可与中金所官方 VIX 数值对标验证。

### 输出指标定义

| 指标 | 算法来源 | 输入数据 |
|------|---------|---------|
| `atm_iv` | LEAN 原生 `ImpliedVolatility`（ATM 合约中位数） | 真实结算价 |
| `iv_call_25delta` | LEAN 原生 `ImpliedVolatility`（25Δ Call） | 真实结算价 |
| `iv_put_25delta` | LEAN 原生 `ImpliedVolatility`（25Δ Put） | 真实结算价 |
| `skew` | `iv_put_25delta − iv_call_25delta` | 上述两个 |
| `vix` | CBOE model-free 30 天插值 | 真实 OTM 价格 + 原生远期价 F |
| `option_count` | 合约计数 | 质量监控 |

### CBOE model-free VIX 实现

```csharp
namespace QuantConnect.Indicators.AShare
{
    public class AShareVixIndicator
    {
        public VixResult Calculate(DateTime date, OptionChain near, OptionChain next,
                                    decimal underlyingPrice)
        {
            var r = _rateModel.GetInterestRate(date);
            
            // 1. 近月方差
            var tNear = TimeToExpiry(date, near.Expiry);
            var fNear = ForwardPriceFromParity(near, r, tNear);  // put-call parity
            var varNear = ModelFreeVariance(near, fNear, tNear, r);
            
            // 2. 次月方差
            var tNext = TimeToExpiry(date, next.Expiry);
            var fNext = ForwardPriceFromParity(next, r, tNext);
            var varNext = ModelFreeVariance(next, fNext, tNext, r);
            
            // 3. 30 天线性插值（CBOE 标准公式）
            var t30 = 30.0 / 365.0;
            var w1 = (tNext - t30) / (tNext - tNear);
            var w2 = 1.0 - w1;
            var var30 = w1 * varNear * (tNear / t30) + w2 * varNext * (tNext / t30);
            
            return new VixResult
            {
                Vix = Math.Sqrt(var30) * 100,
                SigmaNear = Math.Sqrt(varNear) * 100,
                SigmaNext = Math.Sqrt(varNext) * 100,
                TNear = tNear, TNext = tNext
            };
        }
        
        // σ² = (2/T)Σ[ΔK/K²]e^(RT)Q(K) − (1/T)[F/K₀−1]²
        // Q(K) 来自 LEAN 原生 OptionChain 的真实结算价
        private double ModelFreeVariance(OptionChain chain, double F, double T, double r) { ... }
    }
}
```

### 导出策略算法

```csharp
public class AShareIVExportAlgorithm : QCAlgorithm
{
    public override void Initialize()
    {
        AddEquity("510050", Resolution.Daily, market: Market.China);
        var option = AddOption("510050", Resolution.Daily, market: Market.China);
        option.SetOptionChainProvider(new AShareOptionChainProvider(DataFolder));
        option.SetFilter(u => u.Includes(OptionRight.Call).Includes(OptionRight.Put)
                              .FrontMonth().BackMonths().MovesAfter(20));
        
        _rateModel = new AShareShiborRateModel(TusharePath);
        _divModel  = new AShareDividendYieldModel(TusharePath, "510050");
        _vixIndicator = new AShareVixIndicator(_rateModel);
    }
    
    public override void OnData(Slice data)
    {
        // 1. 分离近月/次月
        // 2. 原生 IV: ATM + 25-delta
        // 3. VIX (model-free)
        // 4. 记录（CSV 格式兼容）
    }
    
    public override void OnEndOfAlgorithm()
    {
        ExportToCsv("Data/alternative/ashare-implied-volatility/sse/daily/510050.csv");
        ExportToInflux();
    }
}
```

### CSV 格式（与现有完全兼容）

```csv
trade_date,atm_iv,iv_call_25delta,iv_put_25delta,skew,term_days_near,term_days_next,option_count,vix,sigma_near,sigma_next,t_near,t_next
```

**单位统一**：`atm_iv`/`skew` 为年化小数（0.25），`vix`/`sigma_*` 为年化百分比（25.0）。C# Reader 已正确处理。

---

## 第 5 节：测试与验证

### 测试体系

```
Tests/AShare/
  ├─ AShareOptionDataConverterTests.cs     # 转换器单元测试
  ├─ AShareOptionChainProviderTests.cs     # Provider 单元测试
  ├─ AShareShiborRateModelTests.cs         # 利率模型测试
  ├─ AShareDividendYieldModelTests.cs      # 股息率模型测试
  ├─ AShareVixIndicatorTests.cs            # VIX 算法测试
  └─ AShareIVExportAlgorithmTests.cs       # 端到端测试
```

### 三原则验证矩阵

| 原则 | 验证方法 | 通过标准 |
|------|---------|---------|
| **数据真实** | 转换器单元测试 + 结算价精确映射 | 覆盖率 > 95%，误差 < 0.0001 |
| **算法真实** | 理论价 vs 市场价 + VIX 对标中金所 | 偏差 < 2% / < 5% |
| **结果真实** | IV/VIX 历史范围 + 市场压力检测 | 8-35%，暴跌期飙升 |

### 关键测试用例

**转换器**：
- `SettlementPriceMappedToClose_Exactly` — settle → Close 精确映射，误差 < 0.0001
- `ConvertsSSEOptionContracts_WithRealData` — 合约数量覆盖率 > 95%
- `OptionStyleSetToEuropean_ForAllContracts` — 全部欧式
- `StrikeAndExpiryMatchTushareExactly` — 行权价/到期日精确

**VIX 算法**：
- `MatchesOfficialCFFEX_VIX300_Within5Percent` — 对标中金所官方 < 5%
- `VixWithinHistoricalRange` — VIX 落在 8-35%
- `BackwardationDetected_DuringMarketStress` — 2020-03 暴跌期 VIX 飙升

**端到端**：
- `IVTheoryPriceMatchesMarketPrice_Within2Percent` — LEAN 原生 IV 算出的理论价 ≈ 真实结算价（IV 定义本身）

### 验证报告（自动化）

```python
# Scripts/validate_iv_results.py
report = {
    "data_integrity":    { coverage_ratio > 0.95, settlement_price_accuracy },
    "algorithm_correctness": {
        "iv_theory_price_match": max_error < 0.02,
        "cboe_vix_vs_cffex": bias < 0.05,
    },
    "results_real":      { atm_iv_range, vix_range, skew_distribution }
}
```

CI 集成：在 soloquant pipeline `prepare_iv_data` 阶段后新增 `validate_iv_results` 阶段。

---

## 第 6 节：文件清单与实施计划

### 新增文件（C# 实现）

```
ToolBox/AShareOptionDataConverter.cs          # 转换器
ToolBox/AShareOptionChainProvider.cs          # A 股 Provider
ToolBox/AShareShiborRateModel.cs              # 动态利率模型
ToolBox/AShareDividendYieldModel.cs           # 动态股息率模型
ToolBox/AShareVixIndicator.cs                 # VIX 计算
ToolBox/AShareVixHelper.cs                    # VIX 辅助函数
Tests/AShare/AShareOptionDataConverterTests.cs
Tests/AShare/AShareOptionChainProviderTests.cs
Tests/AShare/AShareShiborRateModelTests.cs
Tests/AShare/AShareDividendYieldModelTests.cs
Tests/AShare/AShareVixIndicatorTests.cs
Tests/AShare/AShareIVExportAlgorithmTests.cs
```

### 实施阶段（按依赖排序）

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| 1. 数据转换层 | Converter + 单元测试，确保数据 100% 还原 | 2-3h |
| 2. Provider + 动态模型 | ChainProvider + ShiborRate + DividendYield + 测试 | 2h |
| 3. VIX 计算 | VixHelper + VixIndicator + 对标官方测试 | 2h |
| 4. 策略算法与导出 | ExportAlgorithm + 端到端测试 | 1.5h |
| **总计** | | **~10-12h** |

### 向后兼容性

- CSV 格式不变（13 列）→ 现有 `export_ashare_market_sentiment_data.py` 无需改
- InfluxDB 测量点不变（`lean_ashare_iv`/`lean_ashare_vix`/`lean_ashare_iv_skew`）→ Grafana 无需改
- `AShareImpliedVolatilityData.cs`（C# BaseData）无需改

### 风险与缓解

| 风险 | 缓解 |
|------|------|
| 合约数量不一致 | 单元测试覆盖率 > 95%，全量对照 tushare |
| settle→Close 错误 | `assertAlmostEqual(settle, close, 0.0001)` |
| 中金所 VIX 不可得 | 放宽标准 < 5%，或手动下载历史官方值 |
| LEAN 原生 IV 求解失败 | log 所有失败合约，人工复核 |

---

## 三原则最终确认

| 原则 | 实现路径 |
|------|---------|
| **数据真实** | 转换器 1:1 读取 tushare parquet，settle→Close 精确映射，真实交易日历 |
| **算法真实** | ImpliedVolatility（Brent）+ VIX model-free（CBOE），无自写 BS |
| **结果真实** | 单元测试：理论价偏差 < 2%，VIX 对标官方 < 5%，历史范围覆盖真实数据 |
