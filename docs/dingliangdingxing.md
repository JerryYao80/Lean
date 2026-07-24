# 定量+定性融合方案：SoloQuant × Serenity

## 1. 问题定义

当前 SoloQuant 管线是纯定量系统：

- **微观量化**：基于 tushare_data 交易数据 → LEAN 框架策略编写 → 回测 → live-paper → 生命周期管理
- **宏观消息**：Serenity 五层分析框架产出板块/标的增减建议、风险信号、政策冲击预判

两者目前完全独立：Serenity 产出 Markdown 报告供人阅读，SoloQuant 策略运行时不感知任何宏观信号。

**目标**：将 Serenity 的定性判断（板块权重、风险等级、政策冲击）以结构化信号形式注入 SoloQuant 的策略执行层，实现"定量选股+定性择时择风控"。

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                    Serenity 定性分析层                            │
│  五层透镜 → 板块权重信号 + 风险等级信号 + 标的增减信号              │
│  输出：serenity_sector_weight / serenity_risk_signal              │
│        serenity_ticker_signal (InfluxDB)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 定时写入 InfluxDB
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    宏观信号桥接层                                  │
│  macro_signal_bridge.py:                                         │
│  1. 解析 Serenity scorecard JSON + Markdown 结果                 │
│  2. 写入 InfluxDB (serenity_* measurements)                     │
│  3. 生成 macro_signal.json (LEAN 自定义数据源)                    │
│  4. 定时触发（cron: 每4小时 / 事件驱动）                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ macro_signal.json
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LEAN 策略执行层                                 │
│  MacroIndicatorData (自定义数据源) ← macro_signal.json            │
│       ↓                                                          │
│  MacroSignalAlphaModel (宏观 Alpha 模型)                          │
│    → Insight: 板块方向 + 预期收益 + SourceModel="MacroSignal"     │
│       ↓                                                          │
│  FixedBlackLittermanPortfolioConstruction                        │
│    → 双视图融合: BarraCNE5V4 view + MacroSignal view             │
│       ↓                                                          │
│  MacroAwareRiskManagementModel (宏观感知风控)                     │
│    → composite_risk_level 触发: 仓位缩放/板块过滤/止损收紧        │
│       ↓                                                          │
│  Execution → Orders                                              │
└─────────────────────────────────────────────────────────────────┘
```

## 3. 核心设计原则

### 3.1 不修改现有功能

遵循 `never-modify-existing-features` 规则：
- BarraCNE5V4 AlphaModel **不动**，保持原有 Barra 因子评分逻辑
- FixedBlackLitterman **不动**，保持原有 BL 融合公式
- V4 RiskManagementModel **不动**，保持原有止损/波动率目标逻辑
- 新增的宏观信号作为 **第二视图 (view)** 和 **风控乘数** 叠加，不侵入原有代码

### 3.2 LEAN 原生优先

所有宏观信号的消费必须走 LEAN 原生数据流：
- 宏观数据作为 `BaseData` 自定义数据源注册
- 宏观 Alpha 作为 `IAlphaModel` 实现，产出标准 `Insight`
- 宏观风控作为 `IRiskManagementModel` 实现，产出标准 `IPortfolioTarget`
- 不在 `OnData()` 中硬编码宏观逻辑

### 3.3 定量为主，定性为辅

权重分配：
- Barra 因子评分（定量）：**70% 权重**，决定选股和基础仓位
- Serenity 宏观信号（定性）：**30% 权重**，调整板块暴露和风控参数
- 在极端宏观风险下（composite_risk_level = "critical"），定性信号可 **覆盖** 定量结果（强制降仓）

## 4. 新增组件详细设计

### 4.1 MacroIndicatorData — 宏观指标自定义数据源

**文件**: `Algorithm.CSharp/MacroIndicatorData.cs`

参考现有 `AShareBarraCNE5V2FactorData.cs` 的模式：

```csharp
public class MacroIndicatorData : BaseData
{
    // 板块权重信号 (来自 Serenity 五层分析)
    public Dictionary<string, double> SectorWeightAdjustments { get; set; }
    
    // 风险等级 (low / medium / high / critical)
    public string CompositeRiskLevel { get; set; }
    
    // 政策冲击信号
    public Dictionary<string, string> PolicySignals { get; set; }
    // key: "monetary_policy" / "regulatory" / "geopolitical"
    // value: "bullish" / "neutral" / "bearish"
    
    // 受影响板块
    public List<string> AffectedSectors { get; set; }
    
    // 标的增减信号 (来自 Serenity 公司排名)
    public Dictionary<string, double> TickerAlphaAdjustments { get; set; }
    // key: ticker (e.g., "600519"), value: alpha adjustment (+/- %)
    
    // 信号置信度 (0.0-1.0)
    public double SignalConfidence { get; set; }
    
    // 信号时间戳
    public DateTime SignalGeneratedAt { get; set; }
    
    public override SubscriptionDataSource GetSource(
        SubscriptionDataConfig config, DateTime date, bool isLiveMode)
    {
        // Live mode: 读 macro_signal.json (由 bridge 更新)
        // Backtest mode: 读历史信号文件
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
        ...
    }
}
```

### 4.2 MacroSignalAlphaModel — 宏观 Alpha 模型

**文件**: `Algorithm.CSharp/MacroSignalAlphaModel.cs`

这是融合的关键：宏观信号作为 **第二个 Alpha 模型**，产出独立 Insight，由 BL 的多视图机制自动融合。

```csharp
public class MacroSignalAlphaModel : AlphaModel
{
    private MacroIndicatorData _latestMacroSignal;
    
    public override IEnumerable<Insight> Update(
        QCAlgorithm algorithm, Slice data)
    {
        var insights = new List<Insight>();
        if (_latestMacroSignal == null) return insights;
        
        var confidence = _latestMacroSignal.SignalConfidence;
        var riskLevel = _latestMacroSignal.CompositeRiskLevel;
        
        // 1. 板块权重调整 → 产出板块级 Insight
        foreach (var kvp in _latestMacroSignal.SectorWeightAdjustments)
        {
            var sector = kvp.Key;
            var weightAdj = kvp.Value;  // +0.1 = 超配10%, -0.2 = 低配20%
            
            // 找到该板块内的所有持仓标的
            var sectorSymbols = GetSymbolsInSector(algorithm, sector);
            foreach (var symbol in sectorSymbols)
            {
                var direction = weightAdj > 0 
                    ? InsightDirection.Up : InsightDirection.Down;
                var magnitude = Math.Abs(weightAdj) * 0.15;  // 缩放到年化收益
                var period = TimeSpan.FromDays(20);  // 宏观信号有效期约20个交易日
                
                insights.Add(new Insight(
                    symbol, period, direction, magnitude, confidence, 
                    SourceModelName: "MacroSignal"));  // BL 识别为第二视图
            }
        }
        
        // 2. 标的级 Alpha 调整 (Serenity 优先研究名单)
        foreach (var kvp in _latestMacroSignal.TickerAlphaAdjustments)
        {
            var ticker = kvp.Key;
            var alphaAdj = kvp.Value;
            var symbol = Symbol.Create(ticker, SecurityType.Equity, Market.USA);
            
            if (algorithm.Securities.ContainsKey(symbol))
            {
                var direction = alphaAdj > 0 
                    ? InsightDirection.Up : InsightDirection.Down;
                insights.Add(new Insight(
                    symbol, TimeSpan.FromDays(20), direction, 
                    Math.Abs(alphaAdj), confidence * 0.8,
                    SourceModelName: "MacroSignal"));
            }
        }
        
        // 3. 极端风险 → 全局降仓 Insight
        if (riskLevel == "critical")
        {
            foreach (var holding in algorithm.Portfolio.Values.Where(h => h.Invested))
            {
                insights.Add(new Insight(
                    holding.Symbol, TimeSpan.FromDays(5), 
                    InsightDirection.Down, 0.15, confidence,
                    SourceModelName: "MacroSignal"));  // 强烈减仓
            }
        }
        
        return insights;
    }
}
```

**为什么这能工作**：LEAN 的 `FixedBlackLittermanPortfolioConstructionModel` 会按 `SourceModel` 分组 Insight，BarraCNE5V4 产出一个 view，MacroSignal 产出另一个 view，BL 公式自动融合两个 view 的预期收益。`tau` 参数控制 view 置信度权重，Serenity 的 `SignalConfidence` 可以映射到 tau 的调整。

### 4.3 MacroAwareRiskManagementModel — 宏观感知风控

**文件**: `Algorithm.CSharp/MacroAwareRiskManagementModel.cs`

**不继承也不修改** V4 RiskManagementModel，而是作为 **第二层风控** 叠加：

```csharp
public class MacroAwareRiskManagementModel : RiskManagementModel
{
    private MacroIndicatorData _latestMacroSignal;
    
    // 宏观风控参数
    private readonly double _baseExposureMultiplier = 1.0;
    private readonly Dictionary<string, double> _riskExposureMap = new()
    {
        { "low", 1.10 },       // 宏观顺风 → 可略微加仓
        { "medium", 1.00 },    // 正常
        { "high", 0.70 },      // 宏观逆风 → 降仓30%
        { "critical", 0.40 },  // 极端风险 → 降仓60%
    };
    
    // 板块暴露上限 (宏观风险越高，单板块上限越低)
    private readonly Dictionary<string, double> _riskSectorCapMap = new()
    {
        { "low", 0.25 },       // 正常时单板块上限25%
        { "medium", 0.20 },
        { "high", 0.15 },      // 逆风时收紧到15%
        { "critical", 0.10 },  // 极端时收紧到10%
    };
    
    public override IEnumerable<IPortfolioTarget> ManageRisk(
        QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        if (_latestMacroSignal == null) return targets;
        
        var riskLevel = _latestMacroSignal.CompositeRiskLevel;
        var exposureMultiplier = _riskExposureMap.GetValueOrDefault(riskLevel, 1.0);
        var sectorCap = _riskSectorCapMap.GetValueOrDefault(riskLevel, 0.20);
        var adjustedTargets = new List<IPortfolioTarget>();
        
        // 1. 全局仓位缩放
        foreach (var target in targets)
        {
            var currentWeight = target.Quantity / algorithm.Portfolio.TotalPortfolioValue;
            var scaledQuantity = target.Quantity * exposureMultiplier;
            adjustedTargets.Add(
                new PortfolioTarget(target.Symbol, scaledQuantity));
        }
        
        // 2. 板块暴露约束
        var sectorHoldings = GroupTargetsBySector(algorithm, adjustedTargets);
        foreach (var sector in sectorHoldings)
        {
            var totalWeight = sector.Value.Sum(t => 
                t.Quantity / algorithm.Portfolio.TotalPortfolioValue);
            if (totalWeight > sectorCap)
            {
                var scaleFactor = sectorCap / totalWeight;
                foreach (var target in sector.Value)
                {
                    adjustedTargets.Add(new PortfolioTarget(
                        target.Symbol, target.Quantity * scaleFactor));
                }
            }
        }
        
        // 3. 受影响板块过滤 (当政策信号 bearish 且风险等级高)
        if (riskLevel == "high" || riskLevel == "critical")
        {
            foreach (var affectedSector in _latestMacroSignal.AffectedSectors)
            {
                if (_latestMacroSignal.PolicySignals.GetValueOrDefault(
                        affectedSector) == "bearish")
                {
                    // 清仓受影响板块
                    foreach (var target in adjustedTargets.ToList())
                    {
                        if (IsInSector(algorithm, target.Symbol, affectedSector))
                        {
                            adjustedTargets.Add(new PortfolioTarget(
                                target.Symbol, 0));
                        }
                    }
                }
            }
        }
        
        return adjustedTargets;
    }
}
```

### 4.4 macro_signal_bridge.py — 宏观信号桥接脚本

**文件**: `Scripts/macro_signal_bridge.py`

将 Serenity 分析结果转化为 LEAN 可消费的信号文件 + InfluxDB 数据：

```python
# 输入：
#   - /root/.claude/skills/serenity-skill/result/*.md  (Serenity 分析结果)
#   - /root/.claude/skills/serenity-skill/result/*.json (scorecard JSON)
#   - tushare macro data (PMI, M2, CPI, SHIBOR)
#
# 输出：
#   - Results/soloquant/macro-signals/macro_signal_latest.json (LEAN 读)
#   - InfluxDB: serenity_sector_weight, serenity_risk_signal,
#               serenity_ticker_signal (Grafana 读)

# 核心逻辑：
# 1. 解析最新 Serenity scorecard JSON → 提取板块权重/风险等级/标的信号
# 2. 解析最新 Serenity Markdown → 提取稀缺层排名/受影响板块
# 3. 拉取 tushare 宏观指标 → 计算 PMI/M2/CPI 的 z-score
# 4. 融合定性(Serenity) + 定量(tushare) → 生成 macro_signal_latest.json
# 5. 写入 InfluxDB 三个 measurements
# 6. 运行模式：--daemon (每4小时) / --once / --event-driven
```

**信号融合逻辑**：

```python
def fuse_signals(serenity_result, tushare_macro):
    """融合 Serenity 定性 + tushare 定量宏观信号"""
    
    # 1. 计算宏观环境评分 (quantitative)
    pmi_zscore = zscore(tushare_macro['pmi'], window=12)
    m2_zscore = zscore(tushare_macro['m2_yoy'], window=12)
    cpi_zscore = zscore(tushare_macro['cpi_yoy'], window=12)
    
    # 综合宏观环境：PMI 扩张 + M2 增长 = 宽松，反之收紧
    macro_stance = (pmi_zscore * 0.4 + m2_zscore * 0.3 - cpi_zscore * 0.3)
    # macro_stance > 0 = 宽松, < 0 = 收紧
    
    # 2. 融合 Serenity 定性判断
    serenity_risk = serenity_result['composite_risk_level']  # low/medium/high/critical
    serenity_sector_weights = serenity_result['sector_weight_adjustments']
    
    # 3. 冲突处理：定量和定性不一致时
    # 规则：定性信号置信度高时以定性为准，否则以定量为准
    confidence = serenity_result['signal_confidence']
    if confidence > 0.7:
        # 高置信度 → 以 Serenity 为主，tushare 为辅
        final_sector_weights = {
            k: v * 0.7 + get_quant_adjustment(k, macro_stance) * 0.3
            for k, v in serenity_sector_weights.items()
        }
    else:
        # 低置信度 → 以 tushare 宏观为主，Serenity 为辅
        final_sector_weights = {
            k: v * 0.3 + get_quant_adjustment(k, macro_stance) * 0.7
            for k, v in serenity_sector_weights.items()
        }
    
    # 4. 风险等级：取定量和定性中更保守的
    quant_risk = classify_risk_from_macro(macro_stance)
    final_risk = max_risk_level(quant_risk, serenity_risk)  
    # max_risk_level: 取两者中风险更高的
    
    return {
        'sector_weight_adjustments': final_sector_weights,
        'composite_risk_level': final_risk,
        'policy_signals': serenity_result['policy_signals'],
        'affected_sectors': serenity_result['affected_sectors'],
        'ticker_alpha_adjustments': serenity_result['ticker_signals'],
        'signal_confidence': confidence,
        'macro_stance': macro_stance,  # 定量宏观环境评分
        'generated_at': datetime.utcnow().isoformat(),
    }
```

### 4.5 融合策略算法 — DingLiangDingXingAlgorithm

**文件**: `Algorithm.CSharp/DingLiangDingXingAlgorithm.cs`

这是整合所有组件的顶层策略算法：

```csharp
public class DingLiangDingXingAlgorithm : QCAlgorithm
{
    public override void Initialize()
    {
        // 1. 注册 A 股股票池 (同 V4)
        SetUniverseSelection(new AShareBarraCNE5V4UniverseSelectionModel(...));
        
        // 2. 双 Alpha 模型：定量 + 定性
        SetAlpha(new CompositeAlphaModel(
            new AShareBarraCNE5V4AlphaModel(...),     // 定量 Barra 因子
            new MacroSignalAlphaModel()                // 定性宏观信号
        ));
        
        // 3. BL 组合构建 (自动融合双视图)
        SetPortfolioConstruction(
            new FixedBlackLittermanPortfolioConstructionModel(
                delta: 2.2, tau: 0.05, riskFreeRate: 0.03));
        
        // 4. 双层风控：定量风控 + 宏观感知风控
        SetRiskManagement(new CompositeRiskManagementModel(
            new AShareBarraCNE5V4RiskManagementModel(...),  // 定量止损/波动率
            new MacroAwareRiskManagementModel()             // 宏观仓位/板块约束
        ));
        
        // 5. 注册宏观指标自定义数据源
        AddData<MacroIndicatorData>("MACRO", Resolution.Daily);
    }
    
    public override void OnData(Slice data)
    {
        // 宏观信号通过自定义数据源自动流入 Alpha 和 Risk 模型
        // 不需要在这里手动处理
        if (data.TryGetValue<MacroIndicatorData>("MACRO", out var macroData))
        {
            // 可选：日志记录宏观信号变化
            Log($"Macro signal: risk={macroData.CompositeRiskLevel}, " +
                $"sectors={string.Join(",", macroData.AffectedSectors)}");
        }
    }
}
```

## 5. InfluxDB 新增 Measurements

遵循"不修改已有 measurement"规则，新建三个：

### 5.1 serenity_sector_weight

```
measurement: serenity_sector_weight
tags: market, theme, layer_name, scarcity_rank
fields: 
  weight_adjustment (float, +0.1=超配, -0.2=低配)
  hhi_estimate (float)
  supplier_count (int)
  expansion_difficulty (float, 1-5)
  evidence_strength (float, 1-5)
  source (string, "serenity" / "tushare_macro" / "fused")
```

### 5.2 serenity_risk_signal

```
measurement: serenity_risk_signal
tags: market, theme
fields:
  composite_risk_level (string, "low"/"medium"/"high"/"critical")
  risk_score (float, 0-1)
  macro_stance (float, -1 to +1, 收紧到宽松)
  monetary_policy_signal (string, "bullish"/"neutral"/"bearish")
  regulatory_signal (string)
  geopolitical_signal (string)
  affected_sectors (string, comma-separated)
  signal_confidence (float, 0-1)
```

### 5.3 serenity_ticker_signal

```
measurement: serenity_ticker_signal
tags: ticker, market, theme
fields:
  alpha_adjustment (float, 预期收益调整)
  demand_inflection (int, 0-5, Serenity scorecard)
  architecture_coupling (int, 0-5)
  chokepoint_severity (int, 0-5)
  supplier_concentration (int, 0-5)
  expansion_difficulty (int, 0-5)
  evidence_quality (int, 0-5)
  valuation_disconnect (int, 0-5)
  pe_ttm (float)
  pb (float)
  roe_latest (float)
  gross_margin_latest (float)
  net_mf_amount_30d (float)
  pledge_ratio (float)
```

## 6. 数据流详解

### 6.1 信号生产流 (Serenity → InfluxDB + JSON)

```
Serenity Skill 执行分析
  ├── 五层透镜产出 Markdown 结果
  ├── scorecard.json 产出结构化评分
  └── tushare_analysis.py 产出定量数据
        ↓
macro_signal_bridge.py (每4小时 / 事件驱动)
  ├── 1. 解析最新 serenity result Markdown → 提取板块权重/排名
  ├── 2. 读取最新 scorecard.json → 提取评分/标的信号
  ├── 3. 拉取 tushare macro → 计算 PMI/M2/CPI z-score
  ├── 4. 融合定性+定量 → 生成 fused signals
  ├── 5. 写入 InfluxDB (3个新 measurements)
  └── 6. 生成 macro_signal_latest.json → LEAN 数据目录
```

### 6.2 信号消费流 (LEAN 策略运行时)

```
LEAN Algorithm.OnFrameworkData()
  │
  ├── AShareBarraCNE5V2FactorData (定量因子数据)
  │     → AShareBarraCNE5V4AlphaModel.Update()
  │       → Insight(SourceModel="BarraCNE5V4", magnitude=因子评分)
  │
  ├── MacroIndicatorData (宏观信号数据)
  │     → MacroSignalAlphaModel.Update()
  │       → Insight(SourceModel="MacroSignal", magnitude=板块/标的调整)
  │
  └── FixedBlackLitterman.DetermineTargetPercent()
        ├── View 1: BarraCNE5V4 (因子评分预期收益)
        ├── View 2: MacroSignal (宏观调整预期收益)
        └── BL 融合 → 最优组合权重
              │
              ├── AShareBarraCNE5V4RiskManagement (定量风控)
              │     → 止损/波动率目标/Sharpe缩放
              │
              └── MacroAwareRiskManagementModel (定性风控)
                    → 仓位缩放/板块约束/受影响板块清仓
                          │
                          └── Execution → Orders
```

### 6.3 冲突解决规则

| 场景 | 定量 (Barra) | 定性 (Serenity) | 解决规则 |
|------|-------------|----------------|---------|
| 板块方向一致 | 超配半导体 | 半导体稀缺层排名高 | **叠加增强**，权重放大 |
| 板块方向冲突 | 因子评分看好银行 | 利率上行看空银行 | **定性优先**（利率是银行核心驱动），降低银行权重 |
| 标的方向冲突 | 因子评分选入某股 | Serenity 标记治理风险 | **排除该股**，从 Insight 中移除 |
| 风险等级 | Sharpe 正常 | 政策风险 critical | **取更保守值**，强制降仓 60% |
| 置信度低 | 因子评分强 | Serenity 置信度 < 0.3 | **定量主导**，宏观信号权重降至 10% |

## 7. 实施阶段

### Phase 0: 基础设施 (1-2天)

| 任务 | 产出 |
|------|------|
| 创建 `macro_signal_bridge.py` 骨架 | 能解析 scorecard.json 并写 InfluxDB |
| 新建 InfluxDB measurements | `serenity_sector_weight`, `serenity_risk_signal`, `serenity_ticker_signal` |
| 新建 Grafana 面板 | 宏观信号面板（独立于已有面板） |
| 配置 cron | `macro-signal-bridge-cron: "0 */4 * * *"` (每4小时) |

### Phase 1: 自定义数据源 (2-3天)

| 任务 | 产出 |
|------|------|
| 创建 `MacroIndicatorData.cs` | LEAN 能订阅和读取宏观信号 JSON |
| 生成历史信号文件 | 回测时可用（从 Serenity 历史 result 反向生成） |
| 集成测试 | 策略能 `AddData<MacroIndicatorData>()` 并在 OnData 收到数据 |

### Phase 2: 宏观 Alpha 模型 (3-5天)

| 任务 | 产出 |
|------|------|
| 创建 `MacroSignalAlphaModel.cs` | 产出 Insight(SourceModel="MacroSignal") |
| BL 双视图测试 | 验证 Barra + Macro 双 view 在 BL 中正确融合 |
| 权重调参 | tau/delta 在不同宏观环境下的最优参数 |

### Phase 3: 宏观风控模型 (2-3天)

| 任务 | 产出 |
|------|------|
| 创建 `MacroAwareRiskManagementModel.cs` | 仓位缩放 + 板块约束 + 受影响板块过滤 |
| CompositeRiskManagement 集成 | 与 V4 风控模型叠加 |
| 极端场景测试 | critical 风险下的降仓行为 |

### Phase 4: 融合策略 + 回测 (3-5天)

| 任务 | 产出 |
|------|------|
| 创建 `DingLiangDingXingAlgorithm.cs` | 整合所有组件的顶层策略 |
| 回测对比 | Barra-only vs. Barra+Macro 的 Sharpe/最大回撤对比 |
| 参数敏感性分析 | 宏观信号权重 0%/10%/20%/30% 的效果 |
| Live-paper 启动 | 独立 config，不影响现有策略 |

### Phase 5: 管线集成 (2-3天)

| 任务 | 产出 |
|------|------|
| sqctl.sh 新增 macro-signal 组 | `./sqctl.sh start macro-signal` |
| SoloQuant pipeline 新增 stage | `build_macro_signal` 在 `build_event_signals` 之后 |
| Live bridge 新增 | `macro_signal_live_bridge.py` 导出宏观信号到 Grafana |
| 完整 Grafana 仪表板 | 叠加宏观信号面板到现有策略仪表板 |

**总估时：13-21天**

## 8. 关键文件清单

| 新增文件 | 类型 | 说明 |
|---------|------|------|
| `Algorithm.CSharp/MacroIndicatorData.cs` | C# | 宏观指标自定义数据源 |
| `Algorithm.CSharp/MacroSignalAlphaModel.cs` | C# | 宏观信号 Alpha 模型 |
| `Algorithm.CSharp/MacroAwareRiskManagementModel.cs` | C# | 宏观感知风控模型 |
| `Algorithm.CSharp/DingLiangDingXingAlgorithm.cs` | C# | 融合策略主算法 |
| `Scripts/macro_signal_bridge.py` | Python | Serenity→LEAN 信号桥接 |
| `Scripts/macro_signal_live_bridge.py` | Python | 宏观信号 InfluxDB 导出 |
| `Launcher/config/config-dingliangdingxing-live-paper.json` | JSON | Live-paper 配置 |
| `monitoring/grafana/dashboards/lean/macro-signal-dashboard.json` | JSON | Grafana 仪表板 |

## 9. 风险与约束

| 风险 | 缓解 |
|------|------|
| Serenity 分析频率低（人工触发）vs 策略需要实时信号 | macro_signal_bridge 同时拉取 tushare 宏观数据作为定量补充，确保即使 Serenity 未更新也有信号 |
| LLM 生成的宏观判断可能有误 | 置信度机制：低置信度时自动降权；取定量和定性中更保守的判断 |
| 宏观信号过时 | 信号有效期 20 个交易日；过期自动降权；bridge 每4小时刷新 |
| 回测无历史宏观信号 | Phase 1 中从 Serenity 历史 result 反向生成历史信号文件 |
| 板块分类不匹配 (SW vs Serenity 自定义) | 统一映射表：Serenity 产业链层级 → 申万一级行业分类 |

## 10. 与现有系统的关系

```
现有系统 (不动)                          新增系统
─────────────────                    ─────────────────
BarraCNE5V4AlphaModel               MacroSignalAlphaModel
      ↓                                    ↓
FixedBlackLittermanPC ←──── 双视图融合 ────┘
      ↓
AShareBarraCNE5V4RM                 MacroAwareRiskManagementModel
      ↓                                    ↓
      └──── CompositeRM 叠加 ──────────────┘
                       ↓
                  Execution

sqctl.sh groups:
  core / prefect / livepaper / bridge  → 不动
  macro-signal → 新增组 (bridge + signal update)

SoloQuant pipeline stages 1-14 → 不动
  新增 stage 7.5: build_macro_signal (在 build_event_signals 之后)
```
