# 因子动物园 + 模型动物园 设计文档

**日期**: 2026-07-01
**状态**: 已批准（待用户审阅 spec）
**作者**: brainstorming session
**适用范围**: LEAN A 股项目全局（全市场个股 + ETF）

---

## 1. 背景与目标

### 1.1 起因

当前 IV-HV 波动率择时策略（`AShareETFVolatilityTimingStrategy` / `AShareETFVolatilityTimingMulti`）回测效果差：

| 版本 | 收益 | 问题 |
|---|---|---|
| V2（三档分配） | -1.04% | IV percentile 伪信号 |
| V3（频繁切换） | -1.47% | 高买低卖 |
| V4（恐惧择时） | -0.50% | 错过反弹 |

根因：IV percentile 是波动率指标，不是方向择时信号。策略把因子计算、择时逻辑、仓位分配、风控全部揉在 `OnData` 里，无法独立优化任一环节。

### 1.2 目标

建立**因子动物园**和**模型动物园**两个统一基础设施，形成三层管线：

```
因子动物园（Common/Factors/）  → 原子信号（高复用）
        ↓ 消费
模型动物园（Common/Models/）   → 组合信号成决策（中复用）
        ↓ 消费
策略（Algorithm.CSharp/）      → 固定 universe + 参数，端到端回测（低复用）
```

### 1.3 核心约束

1. **LEAN 原生**：遵循 LEAN Framework 五层架构（Universe → Alpha → Portfolio → Risk → Execution）
2. **纯加法、零侵入**：不修改任何现有因子类、AlphaModel、策略文件（遵循 "Never Modify Existing Features" 原则）
3. **现有策略回测必须通过**：所有现有策略的回测结果与改动前完全一致
4. **全局视角**：标的池为全市场所有 A 股个股 + ETF，不只服务于 IV-HV 策略
5. **分门别类、便于检索**：因子按逻辑分类，模型按 LEAN 层级分类

---

## 2. 因子动物园设计

### 2.1 目录结构

位置：`Common/Factors/`（新建）

按**因子逻辑**分类（方案 B），一级分类对应 `FactorCategory` 枚举：

```
Common/Factors/
├── Trend/                # 趋势类因子
│   ├── MomentumFactor.cs       # 动量（价格 n 日收益率）
│   ├── MACrossFactor.cs        # 均线交叉
│   └── RSIFactor.cs            # 相对强弱指数
│
├── Value/                # 估值类因子
│   ├── PEPercentileFactor.cs   # PE 历史分位数（Tushare daily_basic）
│   ├── PBPercentileFactor.cs   # PB 历史分位数
│   └── DividendYieldFactor.cs  # 股息率（Facade 封装 AShareDividendYieldModel）
│
├── Volatility/           # 波动率类因子
│   ├── IVPercentileFactor.cs   # IV 历史分位数（Facade 封装 IV CSV）
│   ├── IVHVSpreadFactor.cs     # IV-HV 偏离度（实时计算）
│   ├── HVFactor.cs             # 已实现波动率（实时计算）
│   └── VIXFactor.cs            # VIX 指数（Facade 封装 AShareVixIndicator）
│
├── Quality/              # 质量类因子
│   ├── ROEFactor.cs            # ROE（Tushare fina_indicator）
│   ├── MarginFactor.cs         # 净利率/毛利率
│   └── LeverageFactor.cs       # 杠杆率（资产负债率）
│
├── Sentiment/            # 情绪类因子
│   ├── CrowdingFactor.cs       # 拥挤度（Facade 封装 CrowdingFactors 逻辑）
│   ├── PCRFactor.cs            # Put-Call Ratio
│   └── NorthboundFactor.cs     # 北向资金占比
│
├── Liquidity/            # 流动性类因子
│   ├── TurnoverRateFactor.cs   # 换手率
│   └── AmihudIlliquidityFactor.cs # Amihud 非流动性
│
├── Chip/                 # 筹码类因子
│   ├── ConcentrationFactor.cs  # 筹码集中度（Facade 封装 ChipPeakFactors）
│   └── ProfitRatioFactor.cs    # 获利盘比例
│
└── Core/                 # 核心基础设施
    ├── IFactor.cs              # 因子统一接口
    ├── FactorRegistry.cs       # 因子注册表（全局索引）
    ├── FactorResult.cs         # 因子计算结果
    ├── FactorRankResult.cs     # 横截面排名结果
    ├── FactorMetadata.cs       # 因子元数据
    ├── FactorCategory.cs       # 分类枚举
    ├── FactorScope.cs          # 作用域枚举（TS/XS/Both）
    └── FactorComputeMode.cs    # 计算模式枚举
```

### 2.2 核心接口

#### IFactor 接口

所有因子（无论预计算 CSV 还是实时计算）实现同一接口：

```csharp
namespace QuantConnect.Factors.Core
{
    /// <summary>
    /// 因子接口 - 所有因子的统一契约。
    /// 支持两种计算模式：预计算 CSV（AddData 订阅）和实时计算（算法内调用）。
    /// 支持两种作用域：时间序列（择时）和横截面（选股排名）。
    /// </summary>
    public interface IFactor
    {
        string Id { get; }
        string Name { get; }
        FactorCategory Category { get; }
        FactorScope Scope { get; }
        FactorComputeMode ComputeMode { get; }
        string DataSource { get; }

        FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null);
        FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time);
        bool IsAvailable(Symbol symbol, DateTime time);
    }
}
```

#### FactorCategory 枚举

```csharp
namespace QuantConnect.Factors.Core
{
    public enum FactorCategory
    {
        Trend, Value, Volatility, Quality, Sentiment, Liquidity, Chip
    }
}
```

#### FactorScope 枚举

```csharp
namespace QuantConnect.Factors.Core
{
    public enum FactorScope
    {
        TimeSeries,      // 时间序列（择时）
        CrossSection,    // 横截面（选股排名）
        Both             // 两者皆可
    }
}
```

#### FactorComputeMode 枚举

```csharp
namespace QuantConnect.Factors.Core
{
    public enum FactorComputeMode
    {
        Precomputed,     // 预计算 CSV
        Runtime          // 实时计算
    }
}
```

### 2.3 因子注册表（FactorRegistry）

因子动物园的目录索引。所有因子在启动时注册，策略/模型通过分类、作用域、计算模式、数据源检索。

```csharp
namespace QuantConnect.Factors.Core
{
    public static class FactorRegistry
    {
        private static readonly Dictionary<string, IFactor> _factors = new();
        private static readonly Dictionary<string, FactorMetadata> _metadata = new();
        private static bool _initialized = false;

        public static void Initialize()
        {
            if (_initialized) return;

            // 注册所有因子（Trend/Value/Volatility/Quality/Sentiment/Liquidity/Chip）
            Register(new MomentumFactor(windowDays: 20));
            Register(new IVPercentileFactor(lookbackDays: 252));
            Register(new HVFactor(windowDays: 20));
            // ... 其他因子

            _initialized = true;
        }

        public static IFactor Get(string factorId) { EnsureInitialized(); return _factors.TryGetValue(factorId, out var f) ? f : null; }
        public static IReadOnlyList<IFactor> GetByCategory(FactorCategory category) { ... }
        public static IReadOnlyList<IFactor> GetByScope(FactorScope scope) { ... }
        public static IReadOnlyList<IFactor> GetTimingFactors() => GetByScope(FactorScope.TimeSeries);
        public static IReadOnlyList<IFactor> GetSelectionFactors() => GetByScope(FactorScope.CrossSection);
    }
}
```

### 2.4 现有因子的 Facade 封装（方案 C）

现有因子类**原地不动**，动物园新增 Facade 类引用它们：

| 现有类（不动） | 动物园 Facade（新增） | 封装方式 |
|---|---|---|
| `AShareDividendYieldModel` | `DividendYieldFactor` | 内部持有实例 |
| `AShareVixIndicator` | `VIXFactor` | 内部持有实例 |
| `AShareImpliedVolatilityData` | `IVPercentileFactor` | 读 CSV + percentile |
| `ChipPeakFactors.py` | `ConcentrationFactor` | C# 重写或 Python.NET |
| `CrowdingFactors.py` | `CrowdingFactor` | C# 重写或 Python.NET |

---

## 3. 模型动物园设计

### 3.1 目录结构

位置：`Common/Models/`（新建）。按 LEAN Framework 五层架构分类：

```
Common/Models/
├── Universe/                   # 第一层：选股模型（筛选候选池）
│   ├── Core/IUniverseSelectionModel.cs
│   ├── IndexConstituentUniverseModel.cs
│   ├── ETFUniverseModel.cs
│   ├── AllAStockUniverseModel.cs
│   └── VolatilityRankedUniverseModel.cs
│
├── Alpha/                      # 第二层：择时模型（发出信号）
│   ├── Core/IAlphaModel.cs
│   ├── Trend/MomentumAlphaModel.cs
│   ├── Volatility/IVHVSpreadAlphaModel.cs
│   ├── MultiFactor/BarraCNE5AlphaModel.cs
│   └── Chip/ChipPeakAlphaModel.cs
│
├── Portfolio/                   # 第三层：组合模型（分配仓位）
│   ├── Core/IPortfolioConstructionModel.cs
│   ├── EqualWeightPortfolioModel.cs
│   ├── RiskParityPortfolioModel.cs
│   └── VolatilityScaledPortfolioModel.cs
│
├── Risk/                        # 第四层：风控模型（调整仓位）
│   ├── Core/IRiskManagementModel.cs
│   ├── MaxDrawdownRiskModel.cs
│   ├── VolatilityTargetingRiskModel.cs
│   └── PositionLimitRiskModel.cs
│
├── Execution/                   # 第五层：执行模型（下单）
│   ├── Core/IExecutionModel.cs
│   ├── ImmediateExecutionModel.cs
│   └── TWAPExecutionModel.cs
│
└── Core/
    ├── ModelRegistry.cs
    └── ModelMetadata.cs
```

### 3.2 五层模型职责

| 层 | 接口 | 决策内容 |
|---|---|---|
| **Universe** | `IUniverseSelectionModel` | 哪些标的进入候选池 |
| **Alpha** | `IAlphaModel` | 每个标的买/卖/持有 |
| **Portfolio** | `IPortfolioConstructionModel` | 每个标的权重 |
| **Risk** | `IRiskManagementModel` | 是否调整仓位 |
| **Execution** | `IExecutionModel` | 如何下单 |

### 3.3 模型注册表（ModelRegistry）

```csharp
namespace QuantConnect.Models.Core
{
    public static class ModelRegistry
    {
        public static void Initialize()
        {
            RegisterUniverse(new ETFUniverseModel());
            RegisterAlpha(new IVHVSpreadAlphaModel());
            RegisterPortfolio(new EqualWeightPortfolioModel());
            RegisterRisk(new MaxDrawdownRiskModel(maxDrawdown: 0.15m));
            RegisterExecution(new ImmediateExecutionModel());
        }

        public static IAlphaModel GetAlpha(string id) { ... }
        public static IPortfolioConstructionModel GetPortfolio(string id) { ... }
        // ...
    }
}
```

---

## 4. 三层管线示例

### 4.1 IV-HV 偏离择时策略（单标的）

```csharp
public class IVHVSpreadStrategy : QCAlgorithmFramework
{
    public override void Initialize()
    {
        AddEquity("510300", Resolution.Daily, Market.SSE);

        var ivPct = FactorRegistry.Get("iv_pct_252d");
        var hv = FactorRegistry.Get("hv_20d");

        SetAlpha(new IVHVSpreadAlphaModel(ivPct, hv, spreadThreshold: 0.05m));
        SetPortfolioConstruction(new EqualWeightPortfolioModel());
        SetRiskManagement(new MaxDrawdownRiskModel(maxDrawdown: 0.10m));
    }
}
```

### 4.2 多因子选股策略（全 A 股）

```csharp
public class MultiFactorStockSelectionStrategy : QCAlgorithmFramework
{
    public override void Initialize()
    {
        SetUniverseSelection(new AllAStockUniverseModel(minMarketCap: 50e8m));

        var momentum = FactorRegistry.Get("momentum_rank_20d");
        var quality = FactorRegistry.Get("quality_rank_roe");

        SetAlpha(new FactorCompositeAlphaModel(new[] { momentum, quality }));
        SetPortfolioConstruction(new RiskParityPortfolioModel());
        SetRiskManagement(new VolatilityTargetingRiskModel(targetVol: 0.15m));
    }
}
```

---

## 5. 兼容性与回测验证

### 5.1 设计原则：纯加法，零侵入

| 原则 | 具体做法 |
|---|---|
| 新命名空间 | `QuantConnect.Factors.*` / `QuantConnect.Models.*` |
| 新目录 | `Common/Factors/` / `Common/Models/` |
| Facade 封装 | 不改现有因子类、AlphaModel、策略文件 |

### 5.2 三道验证门

**Gate 1: 编译门** — `dotnet build` 0 Error

**Gate 2: 单元测试门** — 新因子/模型单元测试全绿

**Gate 3: 回测回归门** — 现有策略回测结果与改动前完全一致：

| 策略 | 期望基线 |
|---|---|
| `AShareETFVolatilityTimingStrategy` | Net Profit +0.057% |
| `AShareETFVolatilityTimingMulti` | Net Profit -0.50% |
| `ChipPeakStrategyAlgorithm` | 收益与基线一致 |
| `ETFMomentumStrategy` | 收益与基线一致 |

### 5.3 实现顺序

每加一层，都跑一次回归验证，确保不破坏现有策略。

---

## 6. 首批实现优先级

1. `Factors/Core/` 全部接口
2. `Factors/Volatility/IVPercentileFactor` + `HVFactor` + `IVHVSpreadFactor`
3. `Models/Alpha/Volatility/IVHVSpreadAlphaModel`
4. `Models/Portfolio/EqualWeightPortfolioModel`
5. `Models/Risk/MaxDrawdownRiskModel`
6. 新策略 `IVHVSpreadStrategy`

其余因子/模型作为占位接口先行建立，具体实现按需补充。