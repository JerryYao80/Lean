# VaR 三层管线量化策略 — 设计规范

**日期**: 2026-07-03
**状态**: 已批准（待写实现计划）
**作者**: brainstorming 流程产出，5 组件并行设计 + 对抗式验证 + 完整性批评后合并

---

## 1. 概述

### 1.1 目标

在三层管线架构（Factor Zoo → Model Zoo → Strategy）内，基于现有因子，设计一个 Value-at-Risk (VaR) 量化策略。策略满足三个立足要求，复用 tushare 真实日线数据，纯加法、零侵入地新增能力。

### 1.2 三个立足要求（最高约束，源自 `2026-06-28-iv-calculation-fix-design.md` §三大设计原则）

1. **满足 A 股市场实际要求** — 真实 A 股标的（510050/510300/510500 ETF + CSI300 个股）、Asia/Shanghai 时区、A 股 100 股整手规则。
2. **使用 tushare_data 真实交易数据** — 直接读 LEAN `Data/equity/{sse,szse}/daily/` 真实日线 TradeBar 与 SHIBOR 1Y，不模拟不伪造。不依赖日内数据（`stk_mins` 未下载）。
3. **使用 LEAN 原生优秀稳定算法** — 实现原生 `IFactor` / `IRiskManagementModel` 接口，复用 LEAN Portfolio/Statistics，不自算组合价值或统计。复用 MathNet.Numerics 原语。

### 1.3 数据充裕性判断（已完成）

VaR 数据需求 vs tushare 现状：

| VaR 需求 | tushare 现状 | 判定 |
|---|---|---|
| 日线收益率序列（Historical/CF/MC 核心） | 2006-2026，SSE 5121 + SZSE 6177 + 463 ETF，10+ 年 | ✅ 专业级 |
| 无风险利率（Sharpe/折现） | SHIBOR 1Y，2006-2026，4802 点，`ChinaInterestRateProvider` | ✅ 高精度 |
| 交叉截面/协方差（组合 VaR） | 日线全市场 + Barra CNE5v2 因子 | ✅ 可用 |
| IV 作为波动率交叉验证 | 510050/510300/510500 ETF IV 面，2024-02 至 2025-04 | ⚠️ 仅近 1 年（仅作诊断） |
| 日内/高频波动率（随机波动率 VaR） | `stk_mins` 未下载 | ❌ 不可得（已规避） |

**结论**：所选 4 方法（Bootstrap Historical / Cornish-Fisher / Refined MC / Direct Quantile）全部基于日频收益率，正好落在数据充足区。不设计依赖日内或 2024 前 IV 序列的变体。

### 1.4 用户已确认的设计决策

| # | 决策点 | 选择 |
|---|---|---|
| 1 | VaR 角色 | 三层全做：VaRFactor (L1) + VaRRiskModel (L2) + VarStrategy (L3) |
| 2 | 标的范围 | 单资产（3 ETF）+ 多股组合（CSI300）两者都支持 |
| 3 | VaR 方法 | 全部 4 种：Bootstrap Historical、Cornish-Fisher、Refined MC、Direct Quantile |
| 4 | 置信/持有期 | 全部：1D/95%、1D/99%、10D/99% + ES（每场景都算） |
| 5 | 风控共存 | 新增 CompositeRiskModel 串联复合 VaR+MaxDrawdown+PositionLimit；现有模型不改 |
| 6 | 因子复用 | Volatility 族 (HV/IV/IV-HV) + Chip 族 (Concentration) + Trend 族 (Momentum) |

### 1.5 选定方案（方案 1）

**单一共享 VarEngine 纯数学库 + 三层薄封装**。三层各自是 VarEngine 的薄 wrapper，避免 VaR 数学在层间重复。

```
Layer 0  VarEngine (纯数学, Common/Risk/VaR/)
              ↑ 调用
Layer 1  VaRFactor (IFactor, Common/Factors/Risk/)
Layer 2  VaRRiskModel + CompositeRiskModel (Algorithm.CSharp/Models/Risk/)
Layer 3  VarStrategy (Algorithm.CSharp/)
```

---

## 2. 共享基线 VaRConstants（消除跨层不一致）

完整性批评指出 4 处跨层约定不一致（回看阈值 250 vs 60、简单 vs log 收益、VaR 符号、种子）。本节用单一常量块消除之。

| 约定 | 值 | 理由 |
|---|---|---|
| 回看窗口 | 默认 252 交易日，最低 250 | 1 个 A 股交易日年；与 VarEngine 一致 |
| 收益率类型 | 简单 close-to-close `r_t = Close_t / Close_{t-1} − 1` | 与 `HVFactor.cs:28` 一致；非 log 收益 |
| VaR 符号 | 正损失值（0.0234 = 2.34% 损失） | ES ≥ VaR 强制；Testing 断言用正 |
| 随机种子 | 固定 42（全链路统一） | 回测可复现；VaRFactor 与 VaRRiskModel 同种子 |
| 年化分母 | 252 交易日 | 与 HVFactor √252、Sharpe 年化、ChinaInterestRateProvider 一致 |

**实现**：`Common/Risk/VaR/VaRConstants.cs`（NEW，静态常量类），被 VarEngine / VaRFactor / VaRRiskModel / VarStrategy 共同引用。

---

## 3. Layer 0 — VarEngine（纯数学库）

### 3.1 位置与依赖

- **目录**：`Common/Risk/VaR/`（新目录；`Common.csproj` 是 SDK-style glob，零 csproj 编辑）。
- **命名空间**：`QuantConnect.Risk.VaR`。
- **程序集**：`QuantConnect.Common`（已引用 MathNet.Numerics 5.0.0，且被 Algorithm.CSharp 引用，三层可达）。
- **依赖**：MathNet.Numerics（Statistics、Distributions、LinearAlgebra、LinearAlgebra.Double、Random）+ System。唯一 LEAN 类型引用是 `QuantConnect.Symbol`（作 `VaRResult` 的 opaque tag）。
- **禁依赖**：无 `QuantConnect.Algorithm`、无 `QuantConnect.Factors`、无 `QCAlgorithm`。

### 3.2 复用原语（不重造轮子）

- 收益率序列：复用 `HVFactor.cs` 的 close-to-close 简单收益模式（`r_t = Close_t/Close_{t-1} − 1`，n−1 分母，去均值）。**VarEngine 接受收益序列作为输入**，收益推导留在因子层，引擎保持纯数学。
- 正态分位数：`Normal.InvCDF(mean, sd, p)`（`Indicators/ValueAtRisk.cs` 与 `Common/Statistics/PortfolioStatistics.GetValueAtRisk` 已用同约定）。
- 样本统计：`Statistics.Mean/StandardDeviation/Skewness/Kurtosis`（MathNet 5.0 `Kurtosis` 返回**超额**峰度）。
- 分位数：`SortedArrayStatistics.Quantile(sortedAsc, tau)`（Hyndman type 7）。
- 线性代数：`DenseMatrix.OfArray`、`matrix.Cholesky()`（null 即非 PD，沿用 `AShareBarraCNE5V4_2AlphaModel.cs:240-246`）、`matrix.Evd().EigenValues`。
- RNG：`MersenneTwister(seed, threadSafe:true)`，经 `VarConfig` 注入以保证单测确定性。

### 3.3 公共类型

```csharp
public enum VaRMethod {
    BootstrapHistorical,    // 方法 1
    CornishFisher,          // 方法 2（参数法，偏度/峰度修正）
    MonteCarlo,             // 方法 3（Bayesian Bootstrap MC，组合用 Cholesky 相关正态）
    DirectQuantile          // 方法 4（回测期直接经验分位数）
}

public enum VaRScenario { OneDay95, OneDay99, TenDay99 }

public readonly struct VarConfig {
    public int LookbackDays { get; }            // 默认 252
    public int MinHistoryDays { get; }          // 默认 250；低于返回 Quality=InsufficientHistory
    public int HorizonDays { get; }             // 1 或 10；传 scenario 时被忽略（scenario 为真值源）
    public double Confidence { get; }          // 0.95 或 0.99
    public int BootstrapIterations { get; }     // 默认 10000
    public int MonteCarloPaths { get; }         // 默认 50000
    public int RandomSeed { get; }             // 默认 42（VaRConstants.FixedSeed）
    public bool EnforcePdCovariance { get; }   // 默认 true
    public double EigenvalueFloor { get; }     // 默认 1e-10
    public bool UseOverlappingFor10Day { get; } // 默认 true（historical/bootstrap 路径）
    public bool UseSqrtScalingFor10Day { get; } // 默认 true（parametric/MC 路径）
}

public readonly struct VaRResult {
    public double ValueAtRisk { get; }       // 正损失值（如 0.0234 = 2.34%）
    public double ExpectedShortfall { get; } // CVaR
    public double Quantile { get; }
    public int HorizonDays { get; }
    public double Confidence { get; }
    public VaRMethod Method { get; }
    public VaRScenario Scenario { get; }
    public VaRDataQuality Quality { get; }
    public string DiagnosticMessage { get; }
    public int ObservationsUsed { get; }
    public double ComputeTimeMs { get; }
    public Symbol Symbol { get; }            // 可空（组合模式）
}

public enum VaRDataQuality {
    Valid, InsufficientHistory, ZeroVariance, NonPdCovariance, DegenerateTail, AllReturnsConstant
}

public readonly struct PortfolioVaRInput {
    public IReadOnlyList<IReadOnlyList<double>> AssetReturns { get; } // [nAssets][nObs]，按日对齐
    public IReadOnlyList<double> Weights { get; }
    public IReadOnlyList<Symbol> Symbols { get; }
    public DateTime AsOfDate { get; }
}
```

### 3.4 公共 API

```csharp
public static class VarEngine {
    // 单资产
    public static VaRResult Compute(Symbol symbol, IEnumerable<double> returns,
        VaRMethod method, VaRScenario scenario, VarConfig config = default);

    // 一次性算全部 4×3=12 结果（VaRFactor 用）
    public static IReadOnlyDictionary<(VaRMethod, VaRScenario), VaRResult> ComputeAll(
        Symbol symbol, IEnumerable<double> returns, VarConfig config = default);

    // 组合
    public static VaRResult ComputePortfolio(PortfolioVaRInput input,
        VaRMethod method, VaRScenario scenario, VarConfig config = default);

    public static IReadOnlyDictionary<(VaRMethod, VaRScenario), VaRResult> ComputePortfolioAll(
        PortfolioVaRInput input, VarConfig config = default);

    // regime 分数（VaRFactor 用）
    public static VaRRegimeScore RegimeScore(double[] dailyReturns, int trailingWindow = 252,
        VaRConfidence conf = VaRConfidence.P99, VaRHorizon horizon = VaRHorizon.Day1, DateTime time = default);

    // 边际 VaR 贡献（CrossSection 排名用）
    public static Dictionary<Symbol, double> MarginalContributions(
        Dictionary<Symbol, double[]> perSymbolReturns, Dictionary<Symbol, double> weights,
        VaRMethod method, VaRConfidence conf, VaRHorizon horizon, DateTime time);
}
```

**注**：纯静态 facade（验证者建议，匹配 `ChinaInterestRateProvider` 模式），不引入 `VarEngineRegistry`。

### 3.5 精确数学

#### 方法 1 — Bootstrap Historical Simulation

- **1D**：清洗后日收益 `r_1..r_n`（n ≥ 250）。B=10000 次有放回重采样，每次取 `tau = 1 − confidence` 的经验分位数；VaR = B 次分位数的**中位数**（非均值，对极端重采样更稳健），以正损失值返回 `VaR = −median(q*_b)`。ES：每次重采样内取 `≤ q*_b` 的收益均值，跨 B 次取中位数。
- **10D**：先构造重叠 10 日对数收益 `s_t = ln(∏_{i=0..9}(1+r_{t-i}))`，再在 `s_t` 序列上 bootstrap。**不用 √10 缩放**——A 股波动聚集使 i.i.d. √10 低估尾部；重叠块保留真实多日跌幅。重叠引入自相关，用中位数 + 大 B 缓解。
- **边界**：n < 250 → `InsufficientHistory`；重叠点 < 30 → 回退 √10 并打 flag。

#### 方法 2 — Cornish-Fisher 参数法

- **1D**：算 μ、σ、偏度 S、超额峰度 K（MathNet `Kurtosis` 返回超额，已验证）。
  ```
  z_alpha = Normal.InvCDF(0,1, 1−confidence)   [= −1.6449 @95%, −2.3263 @99%]
  z_cf = z_alpha + (1/6)(z_alpha²−1)S + (1/24)(z_alpha³−3z_alpha)K − (1/36)(2z_alpha³−5z_alpha)S²
  VaR_1d = −(μ + σ·z_cf)        [正损失值]
  ES_1d  = −(μ + σ·[φ(z_alpha)/(1−confidence) + (z_alpha/6)(z_alpha²−1)S])   [φ=标准正态 PDF，含一阶偏度修正]
  ```
  高阶 K/S² ES 项数值不稳定，仅含到 S 项并在 `DiagnosticMessage` 注明。
- **10D**：`VaR_10d = VaR_1d·√10`、`ES_10d = ES_1d·√10`。参数法无干净 10D CF 展开，√时间是 Basel 标准项结构假设；Bootstrap 与 DirectQuantile 已覆盖经验 10D 尾部。
- **边界**：|S|>4 或 |K|>30 → `DegenerateTail`（仍返回值但 flag）；σ<1e-10 → `ZeroVariance`。

#### 方法 3 — Refined Monte Carlo（Bayesian Bootstrap MC，验证者修正命名）

> **命名澄清**：本方法为 **Bayesian Bootstrap MC**（权重 `w_i = −ln(U_i)`, `U_i~U(0,1)`），**不是** Filtered Historical Simulation（FHS 用 λ 衰减权重 `w_i = λ^(n−i)`）。两者都合法但非同一方法，命名须准确以防实现错建。EWMA λ=0.94 FHS 留作 defer。

- **单资产**：每次路径 p 拨 `w_i = −ln(U_i)` 权重，取加权经验分位；P=50000 路径，VaR = 跨路径分位数的 0.5 分位，ES = 路径尾均值跨路径平均。
- **组合（delta-normal + Cholesky）**：建协方差 Σ（n−1），`EnsurePd`，`Σ = LLᵀ`；每路径 `z~N(0,I_n)`，**`x = L·z`（列向量，统一矩阵定向）**→ `Cov(x)=Σ`；组合收益 `R_p = wᵀx`；VaR = `−Quantile({R_p}, 1−conf)`，ES = 尾均值。
- **10D 组合**：每路径模拟 10 个相关日步 `x_t = L·z_t`，几何累计 `R_p_10 = ∏_{t=1..10}(1+wᵀx_t) − 1`；取分位数。线性组合下收敛 √10，非线性捕获复利。
- **边界**：Σ 非 PD 且修复失败 → `NonPdCovariance`，回退对角协方差；P<1000 → `InsufficientHistory`。
- **n=1 组合委托**：`ComputePortfolio(method=MonteCarlo, nAssets=1)` 返回 **delta-normal** 结果（σ·|z|），**非** Bayesian-bootstrap 单资产 MC。调用者要单资产 Bayesian MC 须调 `Compute()` 非 `ComputePortfolio()`。

#### 方法 4 — Direct Quantile（回测期直接经验分位数）

- **1D**：`VaR = −SortedArrayStatistics.Quantile(sorted(r), 1−conf)`，`ES = −mean({r : r ≤ quantile})`。教科书历史模拟 VaR。
- **10D**：建重叠 10 日收益（同方法 1），取直接分位数。Basel 10D 压力 VaR 回测用此法。

#### ES 共享定义

- 经验法（1/3/4）：`ES_tau = −mean({r : r ≤ quantile_tau})`。
- 参数法（2）：`ES_tau = −(μ + σ·φ(z_alpha)/(1−conf))` + 一阶偏度修正。
- **不变量**：`ES ≥ VaR` 恒成立（数值噪声时 clamp 到 VaR）。

#### 协方差 / 组合处理

- `BuildCovariance`：`Σ_ij = (1/(n_ij−1)) Σ_{t∈overlap(i,j)}(r_t,i−mean_i)(r_t,j−mean_j)`，pairwise complete（每对只用两资产都有非 NaN 的日期；对 <30 重叠置 0 并 flag）。
- `EnsurePd`：(a) 对称化；(b) Cholesky；(c) null 则 Evd → 特征值 < 1e-10 抬到地板 → 重建 → 重试 Cholesky；(d) 仍 null → `NonPdCovariance` + 对角 fallback。
- delta-normal 闭环校验（仅 `DiagnosticMessage`，**非**第 5 方法）：`VaR_p_1d = √(wᵀΣw)·|z_alpha|`，应与 MC 组合 1D VaR 在 ~5% 内吻合。
- 权重内部归一化，报告 VaR 按 `sum(weights)` 缩回原单位。

#### 10D 缩放策略汇总（验证者确认正确）

| 路径 | 方法 | 10D 处理 | 理由 |
|---|---|---|---|
| 参数 | CornishFisher | √10 | 矩量法无 10D CF 展开 |
| 历史 | Bootstrap、DirectQuantile | 重叠 10D log 块 | 保留真实多日跌幅，A 股波动聚集 |
| MC 组合 | MonteCarlo | 10 步路径模拟 | 捕获非线性复利 |
| MC 单资产 | MonteCarlo | 块 bootstrap 块大小 10 | 保留局部依赖 |

### 3.6 内部 helper（private static）

`PrepareReturns`（drop NaN/Inf、查 count、查常数/零方差）、`SampleMoments`、`QuantileFromReturns`、`ScaleToHorizon`、`BuildOverlappingKDayReturns`、`BuildCovariance`、`EnsurePd`、`CholeskySimulate`。

### 3.7 边界情形

- 历史 < 250（1D）或 < 30（10D 重叠）→ `InsufficientHistory`，VaR=NaN。
- 全常数收益（停牌股）→ `AllReturnsConstant`，VaR=0（停牌无可观察损失），**不返回 NaN** 以免破坏组合数学。
- 近零方差 → `ZeroVariance`，参数法用 1e-10 ε 方差兜底。
- 非 PD 协方差 → 特征值修复 → 对角 fallback + flag。
- NaN/Inf 收益 → `PrepareReturns` 剥离。
- 前导 NaN（新上市资产）→ pairwise complete。
- 退化尾部（|S|>4 或 |K|>30）→ `DegenerateTail`，CF 仍算但 flag。**批评者补充**：此 regime 下 CF 可能产生**低于**高斯 VaR 的反保守值，建议回退 BootstrapHistorical——留作 v1 实现细节，在 `DiagnosticMessage` 标注并降级 Quality。
- 组合 n=1 → 委托 delta-normal。
- 组合 n>100（CSI300）→ Cholesky O(n³)≈27M ops 可接受；MC 50000×300×10≈150M draws，日频回测可接受；过慢则 `VarConfig.MonteCarloPaths=10000`。
- 非归一化权重（股数）→ 内部归一化 + 按 sum 缩回。
- 种子=−1（时间基）→ 失去可复现，用户显式选择。

### 3.8 文件

```
[NEW] Common/Risk/VaR/VaRConstants.cs        # 共享常量（§2）
[NEW] Common/Risk/VaR/VarEngine.cs           # 公共 API
[NEW] Common/Risk/VaR/VarConfig.cs
[NEW] Common/Risk/VaR/VaRResult.cs
[NEW] Common/Risk/VaR/VaRMethod.cs
[NEW] Common/Risk/VaR/VaRScenario.cs
[NEW] Common/Risk/VaR/VaRDataQuality.cs
[NEW] Common/Risk/VaR/PortfolioVaRInput.cs
[NEW] Common/Risk/VaR/VaRMath.cs             # 内部 helper
[NEW] Tests/Common/Risk/VaR/VarEngineTests.cs
```

---

## 4. Layer 1 — VaRFactor（IFactor）

### 4.1 位置

- `Common/Factors/Risk/VaRFactor.cs` + `Common/Factors/Risk/VaRFactors.cs`（注册助手）。
- 命名空间 `QuantConnect.Factors.Risk`，镜像现有 per-category 文件夹约定。

### 4.2 IFactor 实现

```csharp
public class VaRFactor : IFactor {
    public string Id => "var_1d99";
    public string Name => "Value-at-Risk Regime (1D 99%)";
    public FactorCategory Category => FactorCategory.Volatility;   // 不新增 Risk 枚举值
    public FactorScope Scope => FactorScope.Both;                  // TS regime + XS 边际贡献
    public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
    public string DataSource => "price_history+shibor";
    // 构造参数：historyDays=252, minUsable=250, conf=P99, horizon=Day1,
    //           method=BootstrapHistorical, bootstrapSamples=10000, seed=42, regimeWindow=252
}
```

### 4.3 Category 决策：复用 `Volatility`，**不**新增 `Risk` 枚举值

理由（验证者确认）：
1. 零侵入是硬约束。改 `FactorCategory.cs` 触碰现有文件。即使枚举无 exhaustive switch、无显式序号、无外部 `GetByCategory` 消费者（已 grep 验证），最安全路径是不碰枚举。
2. VaR 本质是波动率衍生：`VaR_{1D,99} ≈ 2.326·σ_daily`（高斯），历史/CF/Bootstrap 都是 HV 也概括的收益分布函数。归入 Volatility 语义诚实。
3. "Risk" 概念已在 Layer 2（`IRiskManagementModel`）表达。Layer 1 再加 Risk 枚举会混淆层级语义。
4. 未来确需显式 Risk 类别时另起 spec；现 (a) 严格更安全且无损失。

### 4.4 Dependencies

声明于 `VaRFactors.Metadata.Dependencies`（自声明，不改 `FactorRegistry.Register`）：

| 依赖 ID | 用途 |
|---|---|
| `hv_20d` | 实现波动率，VaR 主驱动，直接复用 |
| `iv_pct_252d` | 隐含波动率 regime，标 IV 是否已定价尾部（Outlier 检测） |
| `iv_hv_spread` | IV−HV spread，正且极端 ⇒ IV 定价恐慌尚未在实现收益体现 ⇒ boost Outlier |
| `chip_concentration` | 筹码集中度，贴尾部/崩塌（选 Concentration 而非 ProfitRatio——集中度更直接贴尾部） |
| `momentum_20d` | 20d 动量，regime 代理（选 Momentum 而非 RSI——同在收益空间、更干净的 regime 信号） |

**运行时解析**：`EnsureDependencies()` 在首次 `Compute()` 调 `FactorRegistry.Get(id)` 懒加载并缓存（仿 `OptionVolArbFactorZooAlphaModel`）。

### 4.5 注册策略（不改 FactorRegistry.Initialize）

- `FactorRegistry.Initialize()` 保持原样（22 因子不变）。
- 新增 `VaRFactors.Register(...)` 静态助手，由 `VarStrategy.Initialize()` 调用：
  1. `FactorRegistry.Initialize()`（幂等）；
  2. 补注册 `IVHVSpreadFactor`（Initialize 不注册它，从已注册 IV+HV 构造，幂等覆写）；
  3. 构造 `VaRFactor` 并 `FactorRegistry.Register`（按 Id 覆写，幂等）；
  4. 填充 `VaRFactors.Metadata`（独立 metadata 记录，可被未来 metadata browser 发现）。
- `FactorRegistry.Register()` 仅从 IFactor 属性拷贝 Id/Name/Category/Scope/ComputeMode/DataSource，**不**读 Dependencies——故 Dependencies 走独立 metadata。

### 4.6 Compute（TimeSeries regime）

1. 从 `history`（TradeBar）建日简单收益序列（≥250 可用，否则 `Missing`）。
2. 拉依赖因子值（HV/IV/IV-HV/Chip/Momentum，**仅用于 regime 融合与 Quality flag，不进 VaR 数学**）。
3. 调 `VarEngine.ComputeAll(symbol, returns, config)`（单次，12 结果）。
4. 调 `VarEngine.RegimeScore(...)` 算今日 VaR 对 trailing 252d VaR 序列的分位。
5. Quality：`Outlier`（regime≥0.99 且 IV-HV spread 正且极端）/ `Stale`（HV 与 IV 都 Missing 但 VaR 有效）/ `Missing`（<250 历史）。
6. 输出：`Value` = regime 分位 [0,1]（低→满仓，高→减仓），`RawValue` = 当前 1D99 VaR 分数（如 0.0234）。

### 4.7 ComputeRank（CrossSection 边际 VaR 贡献）

- 多股模式：`VarStrategy` 先 `SetCrossSectionData(perSymbolReturns, weights)` 注入，再调 `ComputeRank`。
- `MarginalContribution_i = VaR(w_full) − VaR(w_{−i})`（去 i 后权重重归一化）。
- 排名降序（最高 MVaR = 最差尾部贡献 = 分位 0）。
- 单资产模式：返回空 `FactorRankResult`（`Values`/`PercentileRanks` 初始化为空 dict，非 null——验证者修正）。

### 4.8 边界情形

- 历史 < 250 → `Missing`，Value=0，RawValue=0（风险模型视为无信号，保持仓位）。
- NaN/Inf 收益 → `Missing`（note `nan_input`）。
- 非 ETF 标的（无 IV CSV）→ IV/IV-HV 返回 `Missing`，VaRFactor 仍从价格历史返回 Valid VaR，但 Quality=`Stale`（HV 单独足以 Valid）。
- regime 窗口不足 252 → 回退 in-sample 排名（降级但非 Missing）。
- 种子=42 → 回测可复现（仅影响新策略，不波及现有策略）。
- `FactorRankResult` 空字段 → 防御性初始化空 dict（非 null）。

### 4.9 文件

```
[NEW] Common/Factors/Risk/VaRFactor.cs
[NEW] Common/Factors/Risk/VaRFactors.cs     # 注册助手 + metadata
```

`Common/QuantConnect.csproj` SDK-glob 自动收纳，无 csproj 编辑。

---

## 5. Layer 2 — VaRRiskModel + CompositeRiskModel

### 5.1 VaRRiskModel

- **位置**：`Algorithm.CSharp/Models/Risk/VaRRiskModel.cs`（NEW）。
- **接口**：实现 LEAN 原生 `IRiskManagementModel`（经 `RiskManagementModel` 基类，仿 `AShareBarraCNE5V4RiskManagementModel`）。
- **职责**：每次 `ManageRisk` 算组合 VaR（多资产，VarEngine `ComputePortfolio`），与预算比，超预算时按 `scale = budget/VaR`（clamp [0,1]，**永不加仓**）比例缩放所有 target。用 `algorithm.Portfolio.TotalPortfolioValue`（LEAN 原生，不自算）。

### 5.2 缩放数学（此层唯一数学，VaR/ES 分位在 VarEngine）

1. 每仓权重：`weight_i = (targetQuantity_i · price_i) / TPV`，`price_i = algorithm.Securities[sym].Price`。
2. 组合 VaR 分数：`VarEngine.ComputePortfolio(returns, weights).VaRFraction`。
3. Breach：`breach = VaR_portfolio`（若启用 ES：`esNorm = (ES/esBudget)·varBudget`，`breach = max(VaR, esNorm)`）。
4. **缩放公式**：`breach ≤ budget → scale=1.0`（不加仓）；否则 `scale = budget/breach`，`clamp[0,1]`。VaR 随仓位线性（`VaR_w = quantile(w·r)`，s·w → s·loss quantile）。
5. **迭代精修**（仅 HistoricalBootstrap/BacktestQuantile，重采样依赖权重）：最多 8 次，1% 容差，重算 `VaR(weights·scale)` 直到收敛。
6. **整手**：`newQty = floor(|raw|/LotSize)·LotSize·sign`，`LotSize = algorithm.Securities[sym].SymbolProperties.LotSize`（LEAN 原生，不硬编码 100——验证者修正）。sub-lot → 0。

### 5.3 性能修正（批评者+验证者关键要求）

- **增量收益矩阵缓存**：维护滚动窗口，每 `ManageRisk` 只 append 最新 bar 收益，`OnSecuritiesChanged` 时 flush。仿 `AShareBarraCNE5V4RiskManagementModel.cs:63,139-145` `_dailyReturns` 模式。**否则 CSI300 每次 ManageRisk 重拉 252d History → O(years·252·300·252) 数据点，回测几小时。**
- **日期门控**：缓存 `_lastVarDate`；`algorithm.Time.Date == _lastVarDate` 则返回缓存 scale，不重算、不重拉。防 sub-daily 分辨率下同日重复算。

### 5.4 CompositeRiskModel

- **位置**：`Algorithm.CSharp/Models/Risk/CompositeRiskModel.cs`（NEW）。
- **继承**：LEAN 原生 `CompositeRiskManagementModel`（非 sealed），只加静态工厂 `FromVaR()`/`FromChain()`/`FromRegistryIds()` + ModelRegistry 注册助手。基类 `ManageRisk` 不重写。
- **链序**：**VaR → MaxDrawdown → PositionLimit**。
  1. VaR 先：组合级前瞻约束，须在全仓位向量完整时执行；先做单标的帽会扭曲权重向量使 VaR 失义。比例缩放保留相对权重。
  2. MaxDrawdown 次：状态级断路器（peak-to-trough），与目标向量无关；VaR 缩放后若仍救不回，断路器全平。让 VaR 当日即便触发断路器也能记录 scale。
  3. PositionLimit 末：局部机械帽，作用于 post-VaR、post-断路器的最终可交易向量。
- **链语义（验证者关键修正）**：原生 `CompositeRiskManagementModel` 用 `riskAdjusted.Concat(targets).DistinctBy(Symbol)`（newer wins），**非**真正串联（每模型见原始 targets）。本 spec **明确记录此行为**（option a：独立模型 + DistinctBy 合并），不虚假宣称串联。因本链单调递减（每阶段只缩或透传，不加仓），newer-wins 合并安全。自定义 `ChainedRiskModel`（显式 feed model N+1 with model N 输出）留作 defer。

### 5.5 ModelRegistry 注册（纯加法）

`Algorithm.CSharp/Models/Core/ModelRegistry.cs` 的 `Initialize()` 追加 2 行（已验证无现有策略/alpha 读 `ModelRegistry.GetRisk`）：
```csharp
RegisterRisk(new VaRRiskModel(), "risk_var");
RegisterRisk(CompositeRiskModel.FromVaR(), "risk_composite_var_default");
```
现有 `risk_max_drawdown`/`risk_position_limit` 行不变；`RegisterRisk` 已 public。

### 5.6 边界情形

- Warmup（`IsWarmingUp` 或收益矩阵 < 250 条）→ 透传 scale=1.0。
- 空 targets / 全零 → 透传。
- 单资产 universe → VarEngine 多资产退化为单资产（权重长度 1）。
- TPV ≤ 0 → 透传，防除零。
- 标的不在 `algorithm.Securities` → 跳过；全跳过 → 透传。
- Price ≤ 0（停牌）→ 跳过，不喂 NaN/0 入 VarEngine。
- VarEngine 返回 IsValid=false → 透传 scale=1.0 + `algorithm.Error` log，优雅降级不阻塞交易。
- VaR 内预算但 ES 超预算 → `breach=max(VaR, ES_norm)` 触发缩放（ES 是尾均值，更严约束）。
- 缩放后 equity 低于 1 手 → 该标的归 0（lot 规则平仓，非预算平仓，log 标注）。
- scale=0 → 全平。
- 迭代发散 → 8 次硬上限，取末值。
- 同日 VaR breach + Drawdown breach → VaR 先记 scale，MaxDrawdown 见缩放后组合；若仍触发则全平。两模型状态各自可见。
- MC 种子：固定 42（VaRConstants），**非**日期派生（批评者统一）。

### 5.7 文件

```
[NEW] Algorithm.CSharp/Models/Risk/VaRRiskModel.cs
[NEW] Algorithm.CSharp/Models/Risk/CompositeRiskModel.cs
[MODIFY-EXISTING] Algorithm.CSharp/Models/Core/ModelRegistry.cs   # 仅 Initialize 追加 2 行 RegisterRisk，纯加法
```

**不改**：`MaxDrawdownRiskModel.cs`、`PositionLimitRiskModel.cs`、`IRiskManagementModel.cs`、`RiskManagementModel.cs`、`CompositeRiskManagementModel.cs`、`FactorRegistry.cs`、任何现有因子/策略/alpha。

### 5.8 已记录限制（验证者要求）

- `MaxDrawdownRiskModel` 仅平多仓（`Quantity > 0`，`MaxDrawdownRiskModel.cs:27`）。VaR 策略 long-only A 股无影响，但 `CompositeRiskModel.FromVaR` 作通用工厂须在 XML 注释声明 long-only 限制（pre-existing 限制，非本设计引入）。

---

## 6. Layer 3 — VarStrategy

### 6.1 位置

- `Algorithm.CSharp/VarStrategy.cs`（NEW），`Launcher/config/config-var-strategy.json`（NEW）。
- 镜像 `OptionVolArbFactorZooStrategy.cs` / `AShareOptionVolatilityArbitrage3SymbolsAlgorithm.cs` 结构。

### 6.2 两模式（config `"var-mode"`）

- **`single-etf`**：universe = 510050、510300、510500。VaRFactor 每 ETF 出 regime 信号（低分位→满仓，高→减仓）。Risk = `CompositeRiskModel.FromVaR(...)`。组合：3 ETF 等权或 price-following。
- **`multi-stock`**：universe = CSI300 成分或流动性 top-50 子集。VaRFactor `ComputeRank` 按边际 VaR 贡献排名，选 tail 风险最低的 bottom-N。组合：现有 `EqualWeightPortfolioModel`。Risk：`CompositeRiskModel`（VaR+MaxDrawdown+PositionLimit）。

### 6.3 Initialize

```csharp
public override void Initialize() {
    ValidateConfig();                       // fail-fast（见 §6.5）
    VaRFactors.Register(VaRConfidence.P99, VaRHorizon.Day1);
    // universe by var-mode
    // SetAlpha(...);                        // 单 ETF: regime alpha; 多股: 选股 alpha
    SetPortfolioConstruction(new EqualWeightPortfolioModel());
    SetRiskManagement(CompositeRiskModel.FromVaR(
        varBudgetFraction: 0.02m, maxDrawdown: 0.20m, maxPositionWeight: 0.40m));
    SetExecution(new ImmediateExecutionModel());
}
```

### 6.4 OnData

- 日频 slice：`algorithm.History<TradeBar>(symbols, 252+10)` 拉收益。
- 调 `VaRFactor.Compute` / `ComputeRank`。
- 发射 `SetRuntimeStatistic` 与 `Plot`（见 §7）。
- Portfolio/Statistics 全用 LEAN 原生，不自算。

### 6.5 Config 校验（批评者要求，fail-fast）

`ValidateConfig()` 在 Initialize 抛 `ArgumentException`：
- `var-budget <= 0`
- `lookback < 250`
- methods 字段含未知方法名
- `max-position-weight` 不在 (0,1]
- start-date 早于 2006-10-08（首条 SHIBOR）
- `var-mode` 非 `single-etf`/`multi-stock`

### 6.6 Config JSON schema（示例）

```json
{
  "algorithm-type-name": "VarStrategy",
  "algorithm-language": "CSharp",
  "algorithm-location": "../../../Algorithm.CSharp/VarStrategy.cs",
  "data-folder": "../../../Data/",
  "environment": "backtesting",
  "var-mode": "single-etf",
  "var-symbols": ["510050", "510300", "510500"],
  "var-budget": 0.02,
  "var-es-budget": 0.015,
  "var-confidence": 99,
  "var-horizon-days": 1,
  "var-method": "BootstrapHistorical",
  "var-lookback-days": 252,
  "var-bootstrap-samples": 10000,
  "var-mc-paths": 50000,
  "var-mc-seed": 42,
  "risk-max-drawdown": 0.20,
  "risk-max-position-weight": 0.40,
  "algorithm-start-date": "20180101T00:00:00Z",
  "algorithm-end-date": "20251231T23:59:59Z",
  "timezone": "Asia/Shanghai",
  "cash-amount": 1000000
}
```

### 6.7 文件

```
[NEW] Algorithm.CSharp/VarStrategy.cs
[NEW] Launcher/config/config-var-strategy.json
```

---

## 7. 运营 / 报告层（批评者要求，in-scope-v1）

### 7.1 SetRuntimeStatistic 发射

`VarStrategy.OnData` 仿 `AShareLlmQuantLeanAlgorithm.cs:168-325` 发射：
- `VaR 1D99`、`ES 1D99`、`VaR Breach`（bool）、`VaR Scale`（最近缩放）、`VaR Quality`（Valid/Stale/Outlier）。

### 7.2 Plot 图表

`Plot("VaR", "1D99", varValue)` + `Plot("VaR", "ES 1D99", esValue)` 日线图（`QCAlgorithm.Plotting.cs:174`）。风险经理可在回测结果图看 VaR 时间序列、breach 事件、缩放历史。

### 7.3 Grafana / InfluxDB 桥

**defer**（spec 记录路径）：遵循 `barra-v2-live-bridge` 模式（memory: barra-v2-live-bridge），新增 `var_strategy_*` InfluxDB measurement + 新增（additive，不改现有）dashboard panel。dashboard-strategy-template memory 要求新策略遵循 BarraCNE5V2 模板写共享 measurement（`lean_chart`/`lean_portfolio`/`lean_metric`）。§7.1 的 SetRuntimeStatistic 已让数据可达 `lean_metric`，故桥为 follow-up。

---

## 8. 测试 + 模型验证

### 8.1 VarEngine 数学正确性（`Tests/Common/Risk/VaR/VarEngineTests.cs`）

- 正态样本 → VaR ≈ 1.645σ@95%、2.326σ@99%。
- CF 对已知 S/K vs 解析。
- ES 单调（ES ≥ VaR）+ 公式正确性。
- √10 缩放（参数路径）。
- 组合 VaR 对已知协方差 vs 解析。
- MC 大 N 收敛。

### 8.2 模型验证（批评者最大缺口，in-scope-v1）

新增 `Tests/Common/Risk/VaR/VaRModelBacktestTests.cs`：
- **Kupiec POF 似然比检验** + **Christoffersen 独立性检验** + Basel traffic-light。
- 在真实 510050/510300/510500 日线历史（2018-2025）滚动 252d 窗口 VaR-vs-实现损失对比。
- 断言 4 方法在测试窗口上 Kupiec 95% 置信通过。
- VaR 模型自身必须被回测，否则违反立足要求 2（真实数据）——Testing 设计原只验"算得对"，不验"校准度"，本节补齐。

### 8.3 VaRFactor

- 依赖解析经 FactorRegistry。
- Quality flag（Missing/Outlier/Stale）。
- `ComputeRank` 排名正确性。
- 空 `FactorRankResult` 字段非 null。

### 8.4 VaRRiskModel

- 预算 breach 缩放（算 VaR、breach、验 target 按 `budget/VaR` 缩）。
- 内预算不缩放（不 scale-up）。
- CompositeRiskModel 链合并行为（DistinctBy newer-wins，**非**串联）。
- 增量收益缓存正确性（append + flush）。
- 日期门控（同日不重算）。

### 8.5 VarStrategy

- 单 ETF 烟测 + 多股烟测，用 LEAN `AlgorithmRunner` 回归测试模式（找 `Tests/Algorithm/` 回归样例）。

### 8.6 边界情形目录

<250d 历史；停牌缺失数据；全零/常数收益（零方差）；组合模式单资产；非 PD 协方差；收益序列极端离群；10D horizon 且 <10D 历史；VaR budget=0；空 universe；CF 退化尾部（|S|>4/|K|>30）回退 Bootstrap。

### 8.7 错误处理策略

- `FactorDataQuality.Missing/Outlier/Stale` 从 VaRFactor 上传。
- VarEngine：边界返回 `VaRResult` 带 `Quality` flag + NaN/0 值，**不**抛异常（防崩溃回测）；仅 `ArgumentException` 用于编程错误（null 输入）。
- VaRRiskModel：VarEngine 不可算时透传 scale=1.0 + `algorithm.Error` log，优雅降级。
- 日志：`algorithm.Log` / `algorithm.Error`。

### 8.8 数据流（文本图）

```
tushare daily parquet
  → LEAN DataFolder Data/equity/{sse,szse}/daily/*.csv
  → LEAN history provider (algorithm.History<TradeBar>)
  → VaRFactor.Compute / VaRRiskModel.ManageRisk
  → VarEngine (pure math)
  → FactorResult / 调整后 IPortfolioTarget[]
  → LEAN Portfolio/Execution (native)
  → LEAN Statistics (native)
  → SetRuntimeStatistic / Plot (§7)
  → [defer] InfluxDB lean_metric → Grafana
```

### 8.9 文件

```
[NEW] Tests/Common/Risk/VaR/VarEngineTests.cs
[NEW] Tests/Common/Risk/VaR/VaRModelBacktestTests.cs      # Kupiec/Christoffersen
[NEW] Tests/Algorithm/VarStrategyRegressionTests.cs       # 或加入现有回归文件
```

---

## 9. Defer 项（spec 显式记录，留后续路径）

| 项 | 理由 | 后续 |
|---|---|---|
| EWMA λ=0.94 VaR（RiskMetrics） | 波动聚集标准法，但加第 5 代码路径 | v2，作 `VarConfig.UseEwmaWeighting` flag |
| Stressed-VaR（Basel III） | 压力窗口（2015-06~09、2020-03）并行 VaR，`max(current, stressed)` | v2 |
| t-Copula 组合 MC | 捕获超越高斯的尾部依赖；10D 路径模拟部分覆盖 | v2 |
| Grafana `var_strategy_*` 桥 + 新 dashboard panel | 遵 barra-v2-live-bridge 模式；SetRuntimeStatistic 已让数据可达 | follow-up |
| live-paper 延迟回归 | 增量缓存已覆盖大部分；回测稳定后补 live-paper env 回归 | post-v1 |
| 自定义 ChainedRiskModel | 显式 feed model N+1 with N 输出；v1 用原生 DistinctBy 合并（option a） | v2 |

---

## 10. 零侵入证明

| 文件 | 状态 |
|---|---|
| `Common/Risk/VaR/*` (9 文件) | NEW |
| `Common/Factors/Risk/VaRFactor.cs`、`VaRFactors.cs` | NEW |
| `Algorithm.CSharp/Models/Risk/VaRRiskModel.cs`、`CompositeRiskModel.cs` | NEW |
| `Algorithm.CSharp/VarStrategy.cs`、`Launcher/config/config-var-strategy.json` | NEW |
| `Tests/Common/Risk/VaR/*`、`Tests/Algorithm/VarStrategy*` | NEW |
| `Algorithm.CSharp/Models/Core/ModelRegistry.cs` | MODIFY-EXISTING（仅 Initialize 追加 2 行 RegisterRisk，纯加法，已验证无现有代码读 GetRisk） |
| `Common/QuantConnect.csproj` | 不改（SDK-glob 自动收纳新 .cs） |
| `FactorCategory.cs` | **不改**（复用 Volatility） |
| `FactorRegistry.Initialize()` | **不改**（VaRFactor 经 VaRFactors.Register 注册） |
| `MaxDrawdownRiskModel.cs`、`PositionLimitRiskModel.cs` | **不改** |
| 现有策略（OptionVolArbFactorZooStrategy、ChipPeak、BarraCNE5、AShareOptionVolatilityArbitrage*） | **不改**，回测 byte-for-byte 不变 |

**回归保证**：所有新文件 [NEW]；ModelRegistry.Initialize 只加不删；FactorCategory/FactorRegistry 不碰；现有策略不引用新类。现有回测全部不受影响。

---

## 11. 与三个立足要求对照

| 立足要求 | 满足方式 |
|---|---|
| 1. A 股市场实际 | 510050/510300/510500 + CSI300 真实标的；Asia/Shanghai 时区；A 股 100 股整手（LEAN `SymbolProperties.LotSize`） |
| 2. tushare 真实数据 | 直接读 `Data/equity/{sse,szse}/daily/` 真实日线 + SHIBOR；不模拟不伪造；不依赖日内（未下载）；§8.2 Kupiec 验证模型在真实数据上的校准度 |
| 3. LEAN 原生算法 | 实现 `IFactor`/`IRiskManagementModel`；复用 LEAN Portfolio/Statistics；复用 MathNet 原语 + `Normal.InvCDF`/`SortedArrayStatistics.Quantile`/`DenseMatrix.Cholesky`；VaR 引擎是 LEAN 未提供的新组合能力，非重造已有功能 |

---

## 12. 开放问题（实现阶段定）

- VarEngine 是否暴露 delta-normal 闭环作为第 5 公共方法？建议**不**（仅 DiagnosticMessage）。
- 10D 重叠 bootstrap 是否提供非重叠块备选？默认重叠（252d lookback 数据更多），`UseOverlappingFor10Day=false` 切非重叠 + MinHistoryDays 警告。
- VaR 预算固定分数 vs 由目标 Sharpe 推导？引擎只算 VaR，预算是策略参数。
- IV 因子作 ETF universe 诊断 vs 股票 universe 不可用？IV 因子 diagnostic-only，出 IV 日期范围标 Stale。
- 引擎持久化 per-symbol 滚动收益缓存？保持 stateless（纯度），VaRFactor 在 Compute 内缓存一次跨 12 内部调用复用。

---

**批准状态**：用户已批准本设计（2026-07-03）。下一步：进入 writing-plans skill 出实现计划。
