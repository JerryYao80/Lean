# 定量+定性融合方案 V3：宏观信号作为策略基础设施

> V1/V2 的根本问题：把 Serenity 宏观分析窄化为"给 V2/V4 策略注入信号"。
> V3 的核心修正：宏观信号是 **SoloQuant 所有策略共享的基础设施层**，不是某个策略的插件。
> LEAN 五层架构中每一层都可以消费宏观信号，策略按需组合。

## 1. V1/V2 的概念错误

### 1.1 错误定位

| V1/V2 的说法 | 实际应该是 |
|-------------|----------|
| "宏观信号注入 V2 策略的 BL 层" | 宏观信号服务于**所有**量化策略 |
| "MacroSignalAlphaModel 是 V4 的第二 Alpha" | 宏观 Alpha 是**独立模块**，任何策略都可以用 |
| "FixedBlackLitterman 双视图融合" | BL 融合只是 Portfolio Construction 层的一种消费方式 |
| "新建 MacroAwareRiskManagementModel" | 风控层只是五层之一，不是全部 |

### 1.2 为什么必须修正

1. **Serenity 的分析对象是市场/产业/政策**，不是某个策略。同一个"出口管制收紧"信号，V4 策略需要调仓，但一个纯防御策略可能反而加仓。
2. **不同策略对宏观信号的消费方式不同**：趋势策略可能只关心风险等级（决定仓位），而多因子策略需要板块权重调整。
3. **五层架构每一层都有宏观感知的必要**：Universe Selection 决定"在哪个池子里选鱼"，比 Alpha/BL 的"怎么称鱼"更根本。
4. **新策略生成（路径A）本身就是跨策略的**：Serenity 发现 InP 产业链卡点 → 触发 SoloQuant 生成全新策略，与任何已有策略无关。

## 2. V3 核心原则

### 2.1 宏观信号是基础设施，不是插件

```
V1/V2 架构（错误）:
  V4 策略 ← 宏观信号插件
  V2 策略 ← 无宏观信号
  新策略  ← 无宏观信号

V3 架构（正确）:
  宏观信号基础设施（独立服务）
       ↓
  ┌────┼────┐
  V4   V2   新策略A  新策略B  ...
  各策略按需组合各层的宏观感知模块
```

### 2.2 五层全感知，策略按需组合

LEAN 五层架构中，每一层都有对应的宏观感知版本。策略不需要全部使用，按需选用：

| 层 | 基础模块 | 宏观感知模块 | 策略按需选用 |
|---|---------|------------|------------|
| Universe Selection | ManualUniverseSelectionModel | MacroAwareUniverseSelectionModel | ✅ 按需 |
| Alpha | AShareBarraCNE5V4AlphaModel | MacroSignalAlphaModel | ✅ 按需 |
| Portfolio Construction | FixedBlackLitterman / NativeBL | MacroAwareBLPortfolioConstructionModel | ✅ 按需 |
| Risk Management | AShareBarraCNE5V4RiskManagementModel | MacroAwareRiskManagementModel | ✅ 按需 |
| Execution | AShareLotSizeExecutionModel | MacroAwareExecutionModel | ✅ 按需 |

### 2.3 不修改现有功能

遵循 `never-modify-existing-features` 规则：
- 所有宏观感知模块是**新增文件**，不修改任何已有 C# 类
- 宏观信号数据源是**新增自定义数据**，不影响已有数据流
- 宏观信号服务脚本是**新增脚本**，不影响已有 bridge/pipeline

### 2.4 LEAN 原生优先

所有宏观信号的消费走 LEAN 原生接口：
- 宏观数据作为 `BaseData` 自定义数据源注册
- 宏观 Alpha 作为 `IAlphaModel` 实现，产出标准 `Insight`
- 宏观 Universe 作为 `IUniverseSelectionModel` 实现
- 宏观风控作为 `IRiskManagementModel` 实现
- 宏观执行作为 `IExecutionModel` 实现
- 不在 `OnData()` 中硬编码宏观逻辑

## 3. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Serenity 定性分析层                                 │
│  五层透镜 → 板块权重信号 + 风险等级信号 + 标的增减信号 + 政策冲击信号    │
│  输出: scorecard JSON (结构化) + Markdown (人阅读)                     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    宏观信号基础设施层                                   │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  macro_signal_service.py (常驻服务, 非 bridge)               │     │
│  │  1. 读 Serenity scorecard JSON → 提取结构化信号              │     │
│  │  2. 拉取 tushare macro → 计算 PMI/M2/CPI z-score            │     │
│  │  3. 融合定性+定量 → 生成 macro_signal_latest.json            │     │
│  │  4. 写入 InfluxDB (serenity_* measurements)                 │     │
│  │  5. 路径A: 检测新机会 → 写 local-strategies JSON             │     │
│  │  运行模式: daemon (每4小时) / event-driven                    │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  macro_signal_latest.json (统一信号文件)                      │     │
│  │  所有策略共享同一个信号源，不按策略拆分                          │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                           │                                          │
│                           ▼                                          │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  LEAN MacroIndicatorData (自定义数据源)                       │     │
│  │  所有策略通过 AddData<MacroIndicatorData>() 订阅              │     │
│  └─────────────────────────────────────────────────────────────┘     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
     ┌──────────┐   ┌──────────┐   ┌──────────┐
     │ 策略 A    │   │ 策略 B    │   │ 策略 C    │
     │ (V4+宏观) │   │ (V2+宏观) │   │ (纯定量)  │
     │ 五层全用  │   │ 只用Alpha │   │ 不订阅    │
     │          │   │ +Risk     │   │ 宏观信号  │
     └──────────┘   └──────────┘   └──────────┘
```

## 4. 五层宏观感知模块详细设计

### 4.1 第一层：MacroAwareUniverseSelectionModel

**这是最重要的注入点**——宏观环境决定了"在哪个池子里选鱼"。

**文件**: `Algorithm.CSharp/MacroAwareUniverseSelectionModel.cs`

```csharp
/// <summary>
/// 宏观感知的 Universe Selection Model。
/// 根据宏观风险等级动态调整股票池：
/// - low/medium: 全量股票池（宽松周期，机会多）
/// - high: 收缩到防御性板块（银行、公用事业、必选消费）
/// - critical: 仅保留大盘蓝筹 + 黄金ETF对冲
/// </summary>
public class MacroAwareUniverseSelectionModel : UniverseSelectionModel
{
    private MacroIndicatorData _latestMacro;
    private readonly IEnumerable<Symbol> _fullUniverse;
    private readonly IEnumerable<Symbol> _defensiveUniverse;
    private readonly IEnumerable<Symbol> _crisisUniverse;

    // 板块分类映射 (SW一级行业 → 防御/周期/成长)
    private static readonly HashSet<string> DefensiveSectors = new()
    {
        "银行", "非银金融", "公用事业", "交通运输", "食品饮料", "医药生物"
    };

    private static readonly HashSet<string> CrisisSafeSectors = new()
    {
        "银行", "公用事业", "食品饮料"
    };

    public override IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
    {
        var universe = SelectUniverseByRiskLevel();
        yield return new ManualUniverse(universe, algorithm.UniverseSettings);
    }

    private IEnumerable<Symbol> SelectUniverseByRiskLevel()
    {
        if (_latestMacro == null) return _fullUniverse;

        return _latestMacro.CompositeRiskLevel switch
        {
            "critical" => _crisisUniverse,
            "high"     => _defensiveUniverse,
            _          => _fullUniverse  // low/medium: 全量
        };
    }

    /// <summary>
    /// 更新宏观信号（由策略 OnData 调用）
    /// </summary>
    public void UpdateMacroSignal(MacroIndicatorData macro)
    {
        _latestMacro = macro;
    }
}
```

**为什么 Universe Selection 最重要**：

1. Alpha/BL 只能在已有股票池内优化，无法引入池外的机会
2. 宏观风险高时，即使 Alpha 选出好股，整个板块可能系统性下跌
3. Universe 层的宏观感知是**最粗粒度但最有效**的风控——直接排除风险板块
4. Serenity 发现的新机会（路径A）本质上也是 Universe 层的扩展

**策略使用方式**：

```csharp
// 策略 A: 使用宏观感知 Universe
var macroUniverse = new MacroAwareUniverseSelectionModel(
    fullSymbols, defensiveSymbols, crisisSymbols);
SetUniverseSelection(macroUniverse);

// 策略 B: 不使用，保持原有 ManualUniverse
SetUniverseSelection(new ManualUniverseSelectionModel(symbols));
```

### 4.2 第二层：MacroSignalAlphaModel

**文件**: `Algorithm.CSharp/MacroSignalAlphaModel.cs`

与 V2 相同，只产**板块级 Insight**（5-10个），不产个股级。但定位变了：不再是"V4 的第二 Alpha"，而是"任何策略都可以组合的独立宏观 Alpha"。

```csharp
/// <summary>
/// 宏观信号 Alpha Model — 产出板块级 Insight。
/// 独立于任何具体策略，任何使用 CompositeAlphaModel 的策略都可以组合。
/// </summary>
public class MacroSignalAlphaModel : AlphaModel
{
    private MacroIndicatorData _latestMacro;

    // 板块 → 代表性标的映射 (每板块 1-2 只龙头)
    private readonly Dictionary<string, string[]> _sectorProxies = new()
    {
        { "银行",     new[] { "601398", "600036" } },
        { "房地产",   new[] { "000002", "001979" } },
        { "电子",     new[] { "002475", "300308" } },
        { "通信",     new[] { "600050", "002281" } },
        { "计算机",   new[] { "002230", "300454" } },
        { "电力设备", new[] { "300750", "601012" } },
        { "医药生物", new[] { "600276", "000538" } },
        { "国防军工", new[] { "600760", "002179" } },
        // ... 可扩展到全部 SW 一级行业
    };

    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        var insights = new List<Insight>();
        if (_latestMacro == null) return insights;

        var confidence = _latestMacro.SignalConfidence;

        // 板块级 Insight: 每个板块用 1-2 只代表标的
        foreach (var kvp in _latestMacro.SectorWeightAdjustments)
        {
            var sector = kvp.Key;
            var weightAdj = kvp.Value;

            if (!_sectorProxies.TryGetValue(sector, out var tickers)) continue;

            foreach (var ticker in tickers)
            {
                var symbol = Symbol.Create(ticker, SecurityType.Equity, Market.USA);
                if (!algorithm.Securities.ContainsKey(symbol)) continue;

                var direction = weightAdj > 0
                    ? InsightDirection.Up : InsightDirection.Down;
                var magnitude = Math.Abs(weightAdj) * 0.12;

                insights.Add(new Insight(
                    symbol, TimeSpan.FromDays(20), direction,
                    magnitude, confidence,
                    SourceModelName: "MacroSignal"));
            }
        }

        // 极端风险: 对所有持仓产出强减仓 Insight
        if (_latestMacro.CompositeRiskLevel == "critical")
        {
            foreach (var holding in algorithm.Portfolio.Values.Where(h => h.Invested))
            {
                insights.Add(new Insight(
                    holding.Symbol, TimeSpan.FromDays(5),
                    InsightDirection.Down, 0.20, confidence,
                    SourceModelName: "MacroSignal"));
            }
        }

        return insights;
    }

    /// <summary>
    /// 更新宏观信号（由策略 OnData 调用）
    /// </summary>
    public void UpdateMacroSignal(MacroIndicatorData macro)
    {
        _latestMacro = macro;
    }
}
```

**策略组合方式**：

```csharp
// 策略 A: Barra + 宏观双 Alpha
SetAlpha(new CompositeAlphaModel(
    new AShareBarraCNE5V4AlphaModel(...),   // 定量
    _macroAlphaModel                         // 定性
));

// 策略 B: 纯宏观 Alpha（如防御策略）
SetAlpha(_macroAlphaModel);

// 策略 C: 不用宏观 Alpha
SetAlpha(new AShareBarraCNE5V4AlphaModel(...));
```

### 4.3 第三层：MacroAwareBLPortfolioConstructionModel

**文件**: `Algorithm.CSharp/MacroAwareBLPortfolioConstructionModel.cs`

**不修改** FixedBlackLittermanPortfolioConstructionModel，而是新建一个继承类，覆盖 tau/delta 的获取逻辑：

```csharp
/// <summary>
/// 宏观感知的 Black-Litterman 组合构建模型。
/// 继承 FixedBlackLitterman，根据宏观风险等级动态调整 tau/delta。
/// 不修改父类任何代码。
/// </summary>
public class MacroAwareBLPortfolioConstructionModel
    : FixedBlackLittermanPortfolioConstructionModel
{
    private MacroIndicatorData _latestMacro;

    // 宏观风险 → BL 参数映射
    private static readonly Dictionary<string, (double tauMult, double deltaOver)> RiskParamMap = new()
    {
        { "low",      (0.6, 1.8) },   // 宏观顺风，宏观 view 权重更高
        { "medium",   (1.0, 2.2) },   // 默认
        { "high",     (2.0, 3.5) },   // 宏观逆风，更保守
        { "critical", (4.0, 5.0) },   // 极端风险，几乎纯宏观驱动
    };

    public MacroAwareBLPortfolioConstructionModel(
        double delta, double tau, double riskFreeRate)
        : base(delta, tau, riskFreeRate) { }

    /// <summary>
    /// 更新宏观信号（由策略 OnData 调用）
    /// </summary>
    public void UpdateMacroSignal(MacroIndicatorData macro)
    {
        _latestMacro = macro;
    }

    // 覆盖 tau/delta 的获取，注入宏观调整
    protected override double GetEffectiveTau()
    {
        var baseTau = base.GetEffectiveTau();
        if (_latestMacro == null) return baseTau;

        var (tauMult, _) = RiskParamMap.GetValueOrDefault(
            _latestMacro.CompositeRiskLevel, (1.0, 2.2));
        return baseTau * tauMult;
    }

    protected override double GetEffectiveDelta()
    {
        var baseDelta = base.GetEffectiveDelta();
        if (_latestMacro == null) return baseDelta;

        var (_, deltaOver) = RiskParamMap.GetValueOrDefault(
            _latestMacro.CompositeRiskLevel, (1.0, 2.2));
        return deltaOver;
    }
}
```

**策略使用方式**：

```csharp
// 策略 A: 使用宏观感知 BL
var macroBL = new MacroAwareBLPortfolioConstructionModel(
    delta: 2.2, tau: 0.05, riskFreeRate: 0.03);
SetPortfolioConstruction(macroBL);

// 策略 B: 使用原生 FixedBL（不感知宏观）
SetPortfolioConstruction(new FixedBlackLittermanPortfolioConstructionModel(...));

// 策略 C: 使用 LEAN 原生 BL
SetPortfolioConstruction(new BlackLittermanOptimizationPortfolioConstructionModel(...));
```

### 4.4 第四层：MacroAwareRiskManagementModel

**文件**: `Algorithm.CSharp/MacroAwareRiskManagementModel.cs`

独立模块，任何策略都可以使用。不继承也不修改任何已有风控模型。

```csharp
/// <summary>
/// 宏观感知风控模型 — 独立模块，任何策略都可以使用。
/// 不继承也不修改任何已有风控模型。
/// 功能：仓位缩放 + 板块暴露约束 + 受影响板块过滤
/// </summary>
public class MacroAwareRiskManagementModel : RiskManagementModel
{
    private MacroIndicatorData _latestMacro;

    // 宏观风险 → 全局仓位乘数
    private readonly Dictionary<string, double> _exposureMap = new()
    {
        { "low",      1.10 },  // 宏观顺风 → 可略微加仓
        { "medium",   1.00 },  // 正常
        { "high",     0.70 },  // 宏观逆风 → 降仓30%
        { "critical", 0.40 },  // 极端风险 → 降仓60%
    };

    // 宏观风险 → 单板块暴露上限
    private readonly Dictionary<string, double> _sectorCapMap = new()
    {
        { "low",      0.25 },
        { "medium",   0.20 },
        { "high",     0.15 },
        { "critical", 0.10 },
    };

    public override IEnumerable<IPortfolioTarget> ManageRisk(
        QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        if (_latestMacro == null) return targets;

        var riskLevel = _latestMacro.CompositeRiskLevel;
        var exposureMult = _exposureMap.GetValueOrDefault(riskLevel, 1.0);
        var sectorCap = _sectorCapMap.GetValueOrDefault(riskLevel, 0.20);
        var adjusted = new List<IPortfolioTarget>();

        // 1. 全局仓位缩放
        foreach (var target in targets)
        {
            var scaledQty = (decimal)((double)target.Quantity * exposureMult);
            adjusted.Add(new PortfolioTarget(target.Symbol, scaledQty));
        }

        // 2. 板块暴露约束
        var sectorGroups = GroupBySector(algorithm, adjusted);
        foreach (var group in sectorGroups)
        {
            var totalWeight = group.Value.Sum(t =>
                Math.Abs(t.Quantity) / algorithm.Portfolio.TotalPortfolioValue);
            if (totalWeight > (decimal)sectorCap)
            {
                var scale = (decimal)sectorCap / totalWeight;
                foreach (var t in group.Value)
                {
                    adjusted.Add(new PortfolioTarget(t.Symbol, t.Quantity * scale));
                }
            }
        }

        // 3. 受影响板块过滤 (政策信号 bearish + 风险等级高)
        if (riskLevel == "high" || riskLevel == "critical")
        {
            foreach (var sector in _latestMacro.AffectedSectors)
            {
                if (_latestMacro.PolicySignals?.GetValueOrDefault(sector) == "bearish")
                {
                    foreach (var t in adjusted.ToList())
                    {
                        if (IsInSector(algorithm, t.Symbol, sector))
                        {
                            adjusted.Add(new PortfolioTarget(t.Symbol, 0));
                        }
                    }
                }
            }
        }

        return adjusted;
    }

    /// <summary>
    /// 更新宏观信号（由策略 OnData 调用）
    /// </summary>
    public void UpdateMacroSignal(MacroIndicatorData macro)
    {
        _latestMacro = macro;
    }
}
```

**策略组合方式**：

```csharp
// 策略 A: 定量风控 + 宏观风控叠加
SetRiskManagement(new CompositeRiskManagementModel(
    new AShareBarraCNE5V4RiskManagementModel(...),  // 定量止损/波动率
    _macroRiskModel                                  // 宏观仓位/板块约束
));

// 策略 B: 只用宏观风控
SetRiskManagement(_macroRiskModel);

// 策略 C: 只用定量风控
SetRiskManagement(new AShareBarraCNE5V4RiskManagementModel(...));
```

### 4.5 第五层：MacroAwareExecutionModel

**文件**: `Algorithm.CSharp/MacroAwareExecutionModel.cs`

极端风险时降低执行速度、拆单；正常时可以更激进：

```csharp
/// <summary>
/// 宏观感知执行模型 — 根据宏观风险等级调整执行策略。
/// - low/medium: 正常执行（ImmediateExecutionModel 行为）
/// - high: 拆单执行（降低市场冲击）
/// - critical: 延迟执行（等待更优价格，避免恐慌性成交）
/// </summary>
public class MacroAwareExecutionModel : ExecutionModel
{
    private MacroIndicatorData _latestMacro;
    private readonly IExecutionModel _innerModel;

    // 宏观风险 → 最大单笔订单占日均成交量比例
    private readonly Dictionary<string, double> _volumeCapMap = new()
    {
        { "low",      0.30 },  // 正常：单笔可达日均30%
        { "medium",   0.20 },  // 略保守
        { "high",     0.10 },  // 拆单：单笔不超过日均10%
        { "critical", 0.05 },  // 极端：单笔不超过日均5%
    };

    public MacroAwareExecutionModel()
    {
        _innerModel = new ImmediateExecutionModel();
    }

    public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        if (_latestMacro == null ||
            _latestMacro.CompositeRiskLevel is "low" or "medium")
        {
            // 正常环境：直接执行
            _innerModel.Execute(algorithm, targets);
            return;
        }

        // 高风险环境：拆单执行
        var volumeCap = _volumeCapMap.GetValueOrDefault(
            _latestMacro.CompositeRiskLevel, 0.20);

        foreach (var target in targets)
        {
            var security = algorithm.Securities[target.Symbol];
            var dailyVolume = security.Volume;  // 近期日均成交量

            var maxOrderSize = (decimal)(dailyVolume * volumeCap);
            var remaining = target.Quantity;

            while (Math.Abs(remaining) > 0)
            {
                var orderSize = Math.Min(Math.Abs(remaining), maxOrderSize);
                var sign = remaining > 0 ? 1m : -1m;

                algorithm.MarketOrder(target.Symbol, sign * orderSize);
                remaining -= sign * orderSize;
            }
        }
    }

    public override void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent)
    {
        _innerModel.OnOrderEvent(algorithm, orderEvent);
    }

    /// <summary>
    /// 更新宏观信号（由策略 OnData 调用）
    /// </summary>
    public void UpdateMacroSignal(MacroIndicatorData macro)
    {
        _latestMacro = macro;
    }
}
```

**策略使用方式**：

```csharp
// 策略 A: 宏观感知执行
SetExecution(_macroExecutionModel);

// 策略 B: A股手数执行（不感知宏观）
SetExecution(new AShareLotSizeExecutionModel());
```

## 5. MacroIndicatorData — 统一信号数据源

**文件**: `Algorithm.CSharp/MacroIndicatorData.cs`

所有五层模块共享同一个信号源。信号由 `macro_signal_service.py` 预计算，策略直接读。

```csharp
/// <summary>
/// 宏观指标自定义数据源 — 所有宏观感知模块共享的信号源。
/// 不按策略拆分，所有策略读同一个 macro_signal_latest.json。
/// </summary>
public class MacroIndicatorData : BaseData
{
    // === 板块信号 ===
    // 板块权重调整: key=SW行业名, value=调整幅度 (+0.1=超配10%, -0.2=低配20%)
    public Dictionary<string, double> SectorWeightAdjustments { get; set; }

    // === 风险信号 ===
    // 综合风险等级 (low / medium / high / critical)
    public string CompositeRiskLevel { get; set; }
    // 定量宏观环境 z-score (-1 收紧 ~ +1 宽松)
    public double MacroStance { get; set; }

    // === BL 参数 (由 service 预计算) ===
    public double TauMultiplier { get; set; }
    public double DeltaOverride { get; set; }

    // === Universe 参数 (由 service 预计算) ===
    // 防御性板块列表 (风险 high 时收缩到此)
    public List<string> DefensiveSectors { get; set; }
    // 危机安全板块列表 (风险 critical 时收缩到此)
    public List<string> CrisisSafeSectors { get; set; }

    // === 风控参数 (由 service 预计算) ===
    public double SectorExposureCap { get; set; }
    public double GlobalExposureMultiplier { get; set; }

    // === 执行参数 (由 service 预计算) ===
    public double MaxOrderVolumeRatio { get; set; }

    // === 政策信号 ===
    public Dictionary<string, string> PolicySignals { get; set; }
    public List<string> AffectedSectors { get; set; }

    // === 元数据 ===
    public double SignalConfidence { get; set; }
    public DateTime SignalGeneratedAt { get; set; }

    public override SubscriptionDataSource GetSource(
        SubscriptionDataConfig config, DateTime date, bool isLiveMode)
    {
        var source = isLiveMode
            ? "Results/soloquant/macro-signals/macro_signal_latest.json"
            : $"Results/soloquant/macro-signals/macro_signal_{date:yyyyMMdd}.json";
        return new SubscriptionDataSource(source,
            SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
    }

    public override BaseData Reader(
        SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
    {
        // 解析 JSON 行为 MacroIndicatorData
        // ...
        return this;
    }
}
```

**与 V1/V2 的区别**：

| 字段 | V1 | V2 | V3 |
|------|----|----|-----|
| SectorWeightAdjustments | ✅ | ✅ | ✅ |
| CompositeRiskLevel | ✅ | ✅ | ✅ |
| TauMultiplier / DeltaOverride | ❌ | ✅ | ✅ |
| SectorExposureCap | ❌ | ✅ | ✅ |
| **DefensiveSectors** | ❌ | ❌ | ✅ (Universe 层) |
| **CrisisSafeSectors** | ❌ | ❌ | ✅ (Universe 层) |
| **GlobalExposureMultiplier** | ❌ | ❌ | ✅ (Risk 层) |
| **MaxOrderVolumeRatio** | ❌ | ❌ | ✅ (Execution 层) |
| MacroStance | ❌ | ✅ | ✅ |
| PolicySignals / AffectedSectors | ✅ | ✅ | ✅ |

## 6. 宏观信号基础设施服务

### 6.1 macro_signal_service.py（常驻服务，非 bridge）

**文件**: `Scripts/macro_signal_service.py`

V1/V2 叫 `macro_signal_bridge.py`，暗示它是"桥接"两个系统。V3 改名为 `service`，强调它是独立运行的基础设施。

```python
"""
宏观信号基础设施服务 — 独立于任何策略的常驻服务。

职责：
1. 读 Serenity scorecard JSON → 提取结构化信号
2. 拉取 tushare macro → 计算 PMI/M2/CPI z-score
3. 融合定性+定量 → 生成 macro_signal_latest.json
4. 写入 InfluxDB (serenity_* measurements)
5. 路径A: 检测新机会 → 写 local-strategies JSON

运行模式: daemon (每4小时) / event-driven / once
"""

def run_service(config, once=False):
    """融合 Serenity scorecard JSON + tushare macro → macro_signal_latest.json"""

    # 1. 读最新 Serenity scorecard JSON
    scorecard = load_latest_scorecard()

    # 2. 拉取 tushare 宏观数据
    macro_df = fetch_macro_indicators()  # PMI, M2_yoy, CPI_yoy

    # 3. 计算定量宏观 z-score
    pmi_z = zscore(macro_df['PMI010000'], 12)
    m2_z = zscore(macro_df['m2_yoy'], 12)
    cpi_z = zscore(macro_df['nt_yoy'], 12)
    macro_stance = pmi_z * 0.4 + m2_z * 0.3 - cpi_z * 0.3

    # 4. 综合风险等级 (取 Serenity 和定量中更保守的)
    serenity_risk = scorecard.get('composite_risk_level', 'medium')
    quant_risk = classify_risk(macro_stance)
    final_risk = max_risk(serenity_risk, quant_risk)

    # 5. 板块权重融合
    sector_weights = fuse_sector_weights(
        serenity_sector_weights=extract_sector_weights(scorecard),
        macro_stance=macro_stance)

    # 6. 计算五层参数 (策略直接读，不在策略中计算)
    tau_mult, delta_over = RISK_PARAM_MAP[final_risk]
    sector_cap = SECTOR_CAP_MAP[final_risk]
    exposure_mult = EXPOSURE_MAP[final_risk]
    volume_ratio = VOLUME_CAP_MAP[final_risk]

    # 7. Universe 层参数
    defensive_sectors = DEFENSIVE_SECTORS  # 常量
    crisis_safe_sectors = CRISIS_SAFE_SECTORS  # 常量

    # 8. 输出统一信号文件
    signal = {
        'sector_weight_adjustments': sector_weights,
        'composite_risk_level': final_risk,
        'macro_stance': macro_stance,
        'tau_multiplier': tau_mult,
        'delta_override': delta_over,
        'sector_exposure_cap': sector_cap,
        'global_exposure_multiplier': exposure_mult,
        'max_order_volume_ratio': volume_ratio,
        'defensive_sectors': defensive_sectors,
        'crisis_safe_sectors': crisis_safe_sectors,
        'affected_sectors': scorecard.get('affected_sectors', []),
        'policy_signals': extract_policy_signals(scorecard),
        'signal_confidence': scorecard.get('signal_confidence', 0.5),
        'signal_generated_at': datetime.utcnow().isoformat(),
    }

    write_json('Results/soloquant/macro-signals/macro_signal_latest.json', signal)
    write_influx(signal)

    # 路径A: 检测新机会 → 写 local-strategies JSON
    maybe_write_opportunity(scorecard, signal)
```

**参数映射表**（由 service 预计算，写入 JSON）：

| composite_risk_level | tau_mult | delta_over | sector_cap | exposure_mult | volume_ratio |
|---------------------|----------|------------|------------|---------------|-------------|
| low | 0.6 | 1.8 | 0.25 | 1.10 | 0.30 |
| medium | 1.0 | 2.2 | 0.20 | 1.00 | 0.20 |
| high | 2.0 | 3.5 | 0.15 | 0.70 | 0.10 |
| critical | 4.0 | 5.0 | 0.10 | 0.40 | 0.05 |

### 6.2 冲突解决规则

| 场景 | 定量 (tushare) | 定性 (Serenity) | 解决 |
|------|---------------|----------------|------|
| 板块方向一致 | M2扩张→利好银行 | 银行稀缺层排名高 | **叠加增强** |
| 板块方向冲突 | PMI扩张→利好周期 | 出口管制→看空电子 | **定性优先**（政策是硬约束） |
| 置信度低 | 宏观数据正常 | confidence<0.3 | **定量主导**，tau 回退 1.0 |
| 风险等级冲突 | 宏观z-score正常 | Serenity 风险等级高 | **取更保守值** |

## 7. 路径A：机会发现（跨策略）

与 V2 相同，但强调这是**跨策略**的能力，不是某个策略的附属功能。

```
Serenity 分析完成 (scorecard JSON 产出)
    ↓
macro_signal_service.py 检测到新 scorecard
    ↓
提取: theme + scarce_layers + affected_sectors + ticker_signals
    ↓
判断是否为"新机会" (之前 SoloQuant 未覆盖的主题)
    ↓ 是新机会
写入: Results/soloquant/local-strategies/serenity-<theme>-<date>.json
    ↓
SoloQuant pipeline 的 ingest_local_strategies 阶段自动拾取
    ↓
进入正常 pipeline: screen → reproduce → generate → compile → backtest → live-paper
```

**无需修改任何 pipeline 代码**——现有的 local-strategies JSON 通道已经支持。

### 7.1 机会注入 JSON 格式

```json
{
  "source": "serenity",
  "theme": "inp-photonics-substrate",
  "generated_at": "2026-06-16T10:00:00Z",
  "core_idea": "InP衬底是光通信产业链稀缺节点，仅Sumitomo/Wafer Tech两家主力供应商，18个月认证周期，数据中心800G需求驱动供给紧张",
  "signals": [
    {
      "name": "substrate_scarcity",
      "direction": "bullish",
      "target_sectors": ["通信", "电子"],
      "confidence": 0.85,
      "evidence": [
        "Sumitomo市占率50-60%，HHI>2500",
        "7N铟价在SMM从1500涨至2500元/kg",
        "中国铟出口管制收紧"
      ]
    },
    {
      "name": "export_control_risk",
      "direction": "bearish",
      "target_sectors": ["通信"],
      "affected_tickers": ["002281", "300308"],
      "confidence": 0.7
    }
  ],
  "suggested_universe": {
    "sectors": ["通信", "电子"],
    "keywords": ["磷化铟", "InP", "光芯片", "光通信"]
  }
}
```

## 8. 策略组合示例

### 8.1 全感知策略（五层全用宏观）

```csharp
public class FullMacroAwareAlgorithm : QCAlgorithm
{
    private MacroAwareUniverseSelectionModel _macroUniverse;
    private MacroSignalAlphaModel _macroAlpha;
    private MacroAwareBLPortfolioConstructionModel _macroBL;
    private MacroAwareRiskManagementModel _macroRisk;
    private MacroAwareExecutionModel _macroExecution;

    public override void Initialize()
    {
        // 1. Universe: 宏观感知选股池
        _macroUniverse = new MacroAwareUniverseSelectionModel(
            fullSymbols, defensiveSymbols, crisisSymbols);
        SetUniverseSelection(_macroUniverse);

        // 2. Alpha: Barra + 宏观双 Alpha
        _macroAlpha = new MacroSignalAlphaModel();
        SetAlpha(new CompositeAlphaModel(
            new AShareBarraCNE5V4AlphaModel(...),
            _macroAlpha));

        // 3. Portfolio: 宏观感知 BL
        _macroBL = new MacroAwareBLPortfolioConstructionModel(
            delta: 2.2, tau: 0.05, riskFreeRate: 0.03);
        SetPortfolioConstruction(_macroBL);

        // 4. Risk: 定量 + 宏观双层风控
        _macroRisk = new MacroAwareRiskManagementModel();
        SetRiskManagement(new CompositeRiskManagementModel(
            new AShareBarraCNE5V4RiskManagementModel(...),
            _macroRisk));

        // 5. Execution: 宏观感知执行
        _macroExecution = new MacroAwareExecutionModel();
        SetExecution(_macroExecution);

        // 6. 订阅宏观信号数据源
        AddData<MacroIndicatorData>("MACRO", Resolution.Daily);
    }

    public override void OnData(Slice data)
    {
        // 统一更新所有宏观感知模块
        if (data.TryGetValue<MacroIndicatorData>("MACRO", out var macro))
        {
            _macroUniverse.UpdateMacroSignal(macro);
            _macroAlpha.UpdateMacroSignal(macro);
            _macroBL.UpdateMacroSignal(macro);
            _macroRisk.UpdateMacroSignal(macro);
            _macroExecution.UpdateMacroSignal(macro);

            Log($"Macro: risk={macro.CompositeRiskLevel}, " +
                $"tau={macro.TauMultiplier:F1}x, delta={macro.DeltaOverride:F1}, " +
                $"exposure={macro.GlobalExposureMultiplier:F1}x, " +
                $"sectorCap={macro.SectorExposureCap:P0}");
        }
    }
}
```

### 8.2 轻度感知策略（只用 Alpha + Risk）

```csharp
public class LightMacroAwareAlgorithm : QCAlgorithm
{
    private MacroSignalAlphaModel _macroAlpha;
    private MacroAwareRiskManagementModel _macroRisk;

    public override void Initialize()
    {
        // 1. Universe: 不用宏观感知，保持原有
        SetUniverseSelection(new ManualUniverseSelectionModel(symbols));

        // 2. Alpha: Barra + 宏观
        _macroAlpha = new MacroSignalAlphaModel();
        SetAlpha(new CompositeAlphaModel(
            new AShareBarraCNE5V4AlphaModel(...),
            _macroAlpha));

        // 3. Portfolio: 不用宏观感知 BL
        SetPortfolioConstruction(new FixedBlackLittermanPortfolioConstructionModel(...));

        // 4. Risk: 定量 + 宏观
        _macroRisk = new MacroAwareRiskManagementModel();
        SetRiskManagement(new CompositeRiskManagementModel(
            new AShareBarraCNE5V4RiskManagementModel(...),
            _macroRisk));

        // 5. Execution: 不用宏观感知
        SetExecution(new AShareLotSizeExecutionModel());

        // 6. 订阅宏观信号
        AddData<MacroIndicatorData>("MACRO", Resolution.Daily);
    }

    public override void OnData(Slice data)
    {
        if (data.TryGetValue<MacroIndicatorData>("MACRO", out var macro))
        {
            _macroAlpha.UpdateMacroSignal(macro);
            _macroRisk.UpdateMacroSignal(macro);
        }
    }
}
```

### 8.3 纯定量策略（不订阅宏观信号）

```csharp
public class PureQuantAlgorithm : QCAlgorithm
{
    public override void Initialize()
    {
        // 完全不使用宏观信号，与现有 V4 策略完全一致
        SetUniverseSelection(new ManualUniverseSelectionModel(symbols));
        SetAlpha(new AShareBarraCNE5V4AlphaModel(...));
        SetPortfolioConstruction(new FixedBlackLittermanPortfolioConstructionModel(...));
        SetRiskManagement(new AShareBarraCNE5V4RiskManagementModel(...));
        SetExecution(new AShareLotSizeExecutionModel());

        // 不调用 AddData<MacroIndicatorData>()
    }
}
```

## 9. 数据流全图

```
┌─────────────────────── 定性发现 ───────────────────────┐
│                                                         │
│  Serenity 五层分析 (手动/定时触发)                        │
│    ↓ scorecard.json + Markdown                          │
│    ↓                                                    │
│  ┌────────── 路径A: 机会发现 ──────────┐                 │
│  │  service 提取 theme+sectors+signals │                 │
│  │  → 写 local-strategies/ JSON        │                 │
│  │  → SoloQuant pipeline 自动拾取      │                 │
│  │  → 生成新策略 → 回测 → live-paper   │                 │
│  └─────────────────────────────────────┘                 │
│                                                         │
│  ┌────────── 路径B: 信号服务 ──────────┐                 │
│  │  scorecard.json                      │                 │
│  │  + tushare macro (PMI/M2/CPI)       │                 │
│  │  → macro_signal_service.py           │                 │
│  │  → macro_signal_latest.json          │                 │
│  │  → InfluxDB (3个 measurements)       │                 │
│  └──────────────┬──────────────────────┘                 │
│                  ↓                                      │
└──────────────────┼──────────────────────────────────────┘
                   ↓
┌──────────────────┼──────────────────────────────────────┐
│  LEAN 策略执行层  ↓                                      │
│                                                         │
│  MacroIndicatorData 读 macro_signal_latest.json          │
│    ↓                                                    │
│  OnData() 每日更新:                                      │
│    各层模块.UpdateMacroSignal(macro)                      │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 1: MacroAwareUniverseSelectionModel       │    │
│  │  → 根据风险等级选择股票池 (全量/防御/危机)        │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 2: MacroSignalAlphaModel                  │    │
│  │  → 板块级 Insight (SourceModel="MacroSignal")    │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 3: MacroAwareBLPortfolioConstructionModel │    │
│  │  → BL 双视图融合 + tau/delta 动态调整            │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 4: MacroAwareRiskManagementModel          │    │
│  │  → 仓位缩放 + 板块约束 + 受影响板块过滤          │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Layer 5: MacroAwareExecutionModel               │    │
│  │  → 高风险拆单 / 极端延迟执行                     │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         ↓                               │
│                    最终订单                              │
└─────────────────────────────────────────────────────────┘
```

## 10. InfluxDB 新增 Measurements

遵循"不修改已有 measurement"规则，新建三个：

### 10.1 serenity_sector_weight
```
tags: market, theme, sector
fields: weight_adjustment(float), source(string), confidence(float)
```

### 10.2 serenity_risk_signal
```
tags: market, theme
fields: composite_risk_level(string), risk_score(float), macro_stance(float),
  tau_multiplier(float), delta_override(float), sector_exposure_cap(float),
  global_exposure_multiplier(float), max_order_volume_ratio(float),
  policy_monetary(string), policy_regulatory(string), policy_geopolitical(string),
  signal_confidence(float)
```

### 10.3 serenity_ticker_signal
```
tags: ticker, market, theme
fields: demand_inflection(int), chokepoint_severity(int), supplier_concentration(int),
  evidence_quality(int), valuation_disconnect(int), pe_ttm(float), pb(float),
  roe_latest(float), pledge_ratio(float)
```

## 11. 实施阶段

| Phase | 任务 | 天数 |
|-------|------|------|
| 0 | 基础设施：macro_signal_service.py + InfluxDB + Grafana + 信号目录 | 2 |
| 1 | LEAN 自定义数据源：MacroIndicatorData.cs + 测试 | 2 |
| 2 | Universe 层：MacroAwareUniverseSelectionModel + 测试 | 2 |
| 3 | Alpha 层：MacroSignalAlphaModel + BL 调参 + 测试 | 3 |
| 4 | Risk 层：MacroAwareRiskManagementModel + 测试 | 2 |
| 5 | Execution 层：MacroAwareExecutionModel + 测试 | 1 |
| 6 | 全感知策略 + 回测：FullMacroAwareAlgorithm + 对比测试 | 3 |
| 7 | 机会发现 + 管线集成：路径A + sqctl + pipeline stage | 2 |
| **合计** | | **17天** |

## 12. 新增文件清单

| 文件 | 类型 | 行数估计 | 说明 |
|------|------|---------|------|
| `Algorithm.CSharp/MacroIndicatorData.cs` | C# | ~150 | 宏观指标自定义数据源（五层共享） |
| `Algorithm.CSharp/MacroAwareUniverseSelectionModel.cs` | C# | ~80 | 宏观感知 Universe Selection |
| `Algorithm.CSharp/MacroSignalAlphaModel.cs` | C# | ~80 | 板块级宏观 Alpha |
| `Algorithm.CSharp/MacroAwareBLPortfolioConstructionModel.cs` | C# | ~60 | 宏观感知 BL 组合构建 |
| `Algorithm.CSharp/MacroAwareRiskManagementModel.cs` | C# | ~100 | 宏观感知风控 |
| `Algorithm.CSharp/MacroAwareExecutionModel.cs` | C# | ~80 | 宏观感知执行 |
| `Algorithm.CSharp/FullMacroAwareAlgorithm.cs` | C# | ~80 | 全感知融合策略 |
| `Scripts/macro_signal_service.py` | Python | ~400 | 宏观信号基础设施服务 |
| `Launcher/config/config-full-macro-live-paper.json` | JSON | ~80 | Live-paper 配置 |
| `monitoring/grafana/dashboards/lean/macro-signal-dashboard.json` | JSON | ~200 | Grafana 仪表板 |

**修改文件：0 处**（遵循 never-modify-existing-features 规则）

## 13. V1 → V2 → V3 演进对比

| 维度 | V1 | V2 | V3 |
|------|----|----|-----|
| **定位** | V4 策略的宏观插件 | V4 策略的宏观插件（精简版） | **所有策略共享的基础设施** |
| **注入层数** | 2层 (Alpha + Risk) | 2层 (Alpha + BL调参) | **5层全感知** |
| **Universe 层** | ❌ | ❌ | ✅ **最重要注入点** |
| **Execution 层** | ❌ | ❌ | ✅ 拆单/延迟执行 |
| **Portfolio 层** | BL 双视图 | BL tau/delta 调参 | 继承 BL + 宏观感知覆盖 |
| **Risk 层** | 新建 335 行模型 | 复用 MaximumSectorExposure | 独立 MacroAwareRiskModel |
| **信号服务** | bridge (桥接) | bridge (桥接) | **service (基础设施)** |
| **策略可用性** | 只有 V4 能用 | 只有 V4 能用 | **任何策略按需组合** |
| **修改已有文件** | 0 | 1处 (FixedBL 5行) | **0** |
| **新增 C# 代码** | ~800 行 | ~200 行 | ~550 行 (5层模块) |
| **信号数据字段** | 12 | 9 | **14** (含 Universe/Execution 参数) |

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Serenity 分析频率低 | service 同时拉取 tushare 宏观数据做定量补充 |
| LLM 宏观判断可能有误 | 低置信度时自动降权（tau 回退 1.0） |
| 宏观信号过时 | 恒定持续 + service 每4小时刷新 + 信号时间戳日志 |
| 回测无历史宏观信号 | 从 Serenity 历史 scorecard 反向生成历史信号 |
| 板块映射不匹配 | 统一映射表写在 service 中，策略不关心映射 |
| Universe 动态切换导致频繁调仓 | Universe 切换加冷却期（同一风险等级维持至少 5 个交易日） |
| Execution 拆单影响成交 | 仅 high/critical 时拆单，low/medium 正常执行 |
| 五层全用过于复杂 | 策略按需组合，不必全用；提供 Full/Light/Pure 三种模板 |
