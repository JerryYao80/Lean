# 定量+定性融合方案 V2：SoloQuant × Serenity

> V1 的完整 BL 双视图方案可行，但存在链路过长、Alpha Insight 过多、风控模型冗余、缺少机会发现等问题。V2 针对这些问题做了精简和增强。

## 1. 问题定义

当前 SoloQuant 管线是纯定量系统：

- **微观量化**：tushare_data 交易数据 → LEAN 策略编写 → 回测 → live-paper → 生命周期管理
- **宏观消息**：Serenity 五层分析框架产出板块/标的增减建议、风险信号、政策冲击预判

两者完全独立。Serenity 产出 Markdown 报告供人阅读，SoloQuant 策略运行时不感知任何宏观信号。

**目标**：将 Serenity 定性判断注入 SoloQuant，实现：

1. **机会发现**（路径A）：Serenity 发现产业链卡点 → 触发 SoloQuant 生成新策略
2. **信号融合**（路径B）：宏观信号注入 LEAN 执行层 → 调整仓位和板块暴露

**核心原则**：定量选股为主（70%），定性择时风控为辅（30%）。极端宏观风险下定性可覆盖定量。

## 2. V1 问题与 V2 改进

| 问题 | V1 做法 | V2 改进 |
|------|---------|---------|
| 信号链路太脆 | Serenity Markdown → bridge 解析 → JSON → LEAN (4层) | 只用 scorecard JSON → bridge 融合 → LEAN (2层)，Markdown 留给人看 |
| Alpha Insight 过多 | 每个板块每个持仓都产 Insight (几十个) | 只产**板块级 Insight** (5-10个)，BL 更稳定 |
| 风控模型冗余 | 新建 MacroAwareRiskManagementModel | 不建新模型，用 BL tau/delta 调参 + 已有 MaximumSectorExposureRiskManagementModel |
| tau 调参缺失 | 未给出具体映射 | 明确 risk_level → tau/delta 映射表 |
| 缺少机会发现 | 只做已有策略的宏观调整 | 新增路径A：Serenity 发现机会 → SoloQuant 生成新策略 |
| 信号频率错配 | 未明确 | 恒定持续：宏观 Insight 在下次更新前保持不变 |

## 3. 双路径架构总览

```
┌────────────────────────────────────────────────────────────────────┐
│  Serenity 定性分析                                                  │
│  产出: scorecard JSON (结构化) + Markdown (人阅读)                   │
│    ↓                                                                │
│    ┌─────────── 路径A: 机会发现 ───────────┐                        │
│    │  产业链卡点/政策冲击 → 触发 SoloQuant    │                        │
│    │  crawl → generate 新策略                 │                        │
│    └────────────────────────────────────────┘                        │
│    ↓                                                                │
│    ┌─────────── 路径B: 信号融合 ───────────┐                        │
│    │  scorecard JSON + tushare macro z-score │                        │
│    │  → macro_signal_bridge.py (JSON→JSON)   │                        │
│    │  → macro_signal_latest.json             │                        │
│    │  → LEAN MacroIndicatorData              │                        │
│    │  → MacroSignalAlphaModel (板块级)        │                        │
│    │  → BL 双视图融合 (tau/delta 动态调整)    │                        │
│    │  → MaximumSectorExposure (板块上限调整)   │                        │
│    └────────────────────────────────────────┘                        │
└────────────────────────────────────────────────────────────────────┘
```

## 4. 路径A：机会发现（Serenity → SoloQuant 新策略生成）

### 4.1 为什么比纯风控价值更大

风控只能减少损失，但机会发现能创造收益。Serenity 的五层透镜能发现定量系统看不到的东西：

- **产业链卡点**：InP 衬底供应商只有 2-3 家 → 供应链约束 → 供给端 alpha
- **政策冲击**：出口管制收紧 → 国产替代加速 → 替代供应商 alpha
- **技术拐点**：硅光芯片良率突破 → 传统光模块承压 → 多空 alpha
- **博弈推演**：龙头供应商可能提价 → 下游成本压力 → 产业链利润重分配

这些信号 SoloQuant 的 Barra 因子模型无法捕获——因子只看历史价量，不看未来供给结构。

### 4.2 机会发现工作流

```
Serenity 分析完成 (scorecard JSON 产出)
    ↓
macro_signal_bridge.py 检测到新 scorecard
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

### 4.3 机会注入 JSON 格式

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

## 5. 路径B：信号融合（宏观信号注入 LEAN 执行层）

### 5.1 信号链路（2 层，非 4 层）

```
Serenity scorecard.json (结构化 JSON，非 Markdown)
    +
tushare macro data (PMI/M2/CPI z-score)
    ↓
macro_signal_bridge.py
  - 读 scorecard JSON → 提取板块权重/风险等级/标的信号
  - 拉取 tushare macro → 计算定量宏观 z-score
  - 融合定性+定量 → 输出 macro_signal_latest.json
  - 写入 InfluxDB (3个新 measurements)
    ↓
macro_signal_latest.json (一个 JSON 文件)
    ↓
LEAN MacroIndicatorData 读取该 JSON
```

**不再解析 Markdown**：scorecard JSON 包含所有结构化字段（8个 factor 评分、8个 penalty 评分、quantitative_metrics、evidence 数组）。Markdown 留给人看。

### 5.2 MacroIndicatorData — 自定义数据源

**文件**: `Algorithm.CSharp/MacroIndicatorData.cs`

参考 `AShareBarraCNE5V2FactorData.cs` 模式：

```csharp
public class MacroIndicatorData : BaseData
{
    // 板块权重调整: key=SW行业名, value=调整幅度
    public Dictionary<string, double> SectorWeightAdjustments { get; set; }
    
    // 综合风险等级
    public string CompositeRiskLevel { get; set; }
    
    // BL tau 乘数 (由 bridge 预计算)
    public double TauMultiplier { get; set; }
    
    // BL delta 覆盖值 (由 bridge 预计算)
    public double DeltaOverride { get; set; }
    
    // 单板块暴露上限 (由 bridge 预计算)
    public double SectorExposureCap { get; set; }
    
    // 受影响板块
    public List<string> AffectedSectors { get; set; }
    
    // 政策信号
    public Dictionary<string, string> PolicySignals { get; set; }
    
    // 信号置信度
    public double SignalConfidence { get; set; }
    
    // 定量宏观环境 z-score
    public double MacroStance { get; set; }
    
    // 信号生成时间
    public DateTime SignalGeneratedAt { get; set; }
}
```

**与 V1 的区别**：新增 `TauMultiplier`、`DeltaOverride`、`SectorExposureCap`，由 bridge 预计算，策略直接读。

### 5.3 MacroSignalAlphaModel — 板块级宏观 Alpha

**文件**: `Algorithm.CSharp/MacroSignalAlphaModel.cs`

**关键简化**：只产**板块级 Insight**，不产个股级。

```csharp
public class MacroSignalAlphaModel : AlphaModel
{
    private MacroIndicatorData _latestMacro;
    
    // 板块 → 代表性标的映射 (每板块 1-2 只龙头)
    private readonly Dictionary<string, string[]> _sectorProxies = new()
    {
        { "银行", new[] { "601398", "600036" } },
        { "房地产", new[] { "000002", "001979" } },
        { "电子", new[] { "002475", "300308" } },
        { "通信", new[] { "600050", "002281" } },
        { "计算机", new[] { "002230", "300454" } },
        { "电力设备", new[] { "300750", "601012" } },
        { "医药生物", new[] { "600276", "000538" } },
        { "国防军工", new[] { "600760", "002179" } },
        // ... 可扩展到全部 SW 一级行业
    };
    
    public override IEnumerable<Insight> Update(
        QCAlgorithm algorithm, Slice data)
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
            foreach (var holding in algorithm.Portfolio.Values
                .Where(h => h.Invested))
            {
                insights.Add(new Insight(
                    holding.Symbol, TimeSpan.FromDays(5),
                    InsightDirection.Down, 0.20, confidence,
                    SourceModelName: "MacroSignal"));
            }
        }
        
        return insights;
    }
}
```

**为什么板块级而非个股级**：

1. Serenity 的分析粒度是产业链层级，硬映射到个股会引入噪声
2. 板块级 Insight 数量少（5-10个），BL 优化稳定
3. 个股选择交给 Barra 因子（定量的强项），宏观只调板块暴露

### 5.4 BL 参数动态调整（唯一修改点）

**文件**: `Algorithm.CSharp/FixedBlackLittermanPortfolioConstructionModel.cs`

唯一需要修改的现有文件，新增一个方法：

```csharp
public void SetMacroAdjustments(double tauMultiplier, double deltaOverride)
{
    _tauMultiplier = tauMultiplier;
    _deltaOverride = deltaOverride;
}

// DetermineTargetPercent() 中:
// 原: var tau = _tau;
// 改: var tau = _tau * _tauMultiplier;
// 原: var delta = _delta;
// 改: var delta = _deltaOverride > 0 ? _deltaOverride : _delta;
```

**tau/delta 映射表**（由 bridge 预计算，写入 macro_signal_latest.json）：

| composite_risk_level | tau_multiplier | delta_override | 含义 |
|---------------------|---------------|----------------|------|
| low | 0.6 | 1.8 | 宏观顺风，宏观 view 权重更高，风险偏好略升 |
| medium | 1.0 | 2.2 | 默认，BL 参数不变 |
| high | 2.0 | 3.5 | 宏观逆风，宏观 view 权重大幅提高，更保守 |
| critical | 4.0 | 5.0 | 极端风险，几乎纯宏观驱动，极度保守 |

### 5.5 板块暴露上限动态调整

不需要新模型。在策略的 `OnData()` 中根据宏观信号调整已有 `MaximumSectorExposureRiskManagementModel` 参数：

```csharp
public override void OnData(Slice data)
{
    if (data.TryGetValue<MacroIndicatorData>("MACRO", out var macroData))
    {
        _sectorExposureModel.SetMaximumSectorExposure(
            (decimal)macroData.SectorExposureCap);
        _blModel.SetMacroAdjustments(
            macroData.TauMultiplier, macroData.DeltaOverride);
    }
}
```

### 5.6 macro_signal_bridge.py — 信号桥接

**文件**: `Scripts/macro_signal_bridge.py`

只读 JSON，不解析 Markdown：

```python
def run_bridge(config, once=False):
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
    
    # 6. 计算 tau/delta/cap (策略直接读)
    tau_mult, delta_over = RISK_MAP[final_risk]
    sector_cap = SECTOR_CAP_MAP[final_risk]
    
    # 7. 输出
    signal = {
        'sector_weight_adjustments': sector_weights,
        'composite_risk_level': final_risk,
        'tau_multiplier': tau_mult,
        'delta_override': delta_over,
        'sector_exposure_cap': sector_cap,
        'affected_sectors': scorecard.get('affected_sectors', []),
        'policy_signals': extract_policy_signals(scorecard),
        'signal_confidence': scorecard.get('signal_confidence', 0.5),
        'macro_stance': macro_stance,
        'signal_generated_at': datetime.utcnow().isoformat(),
    }
    
    write_json('Results/soloquant/macro-signals/macro_signal_latest.json', signal)
    write_influx(signal)
    
    # 路径A: 检测新机会 → 写 local-strategies JSON
    maybe_write_opportunity(scorecard, signal)
```

**冲突解决规则**：

| 场景 | 定量 (tushare) | 定性 (Serenity) | 解决 |
|------|---------------|----------------|------|
| 板块方向一致 | M2扩张→利好银行 | 银行稀缺层排名高 | **叠加增强** |
| 板块方向冲突 | PMI扩张→利好周期 | 出口管制→看空电子 | **定性优先**（政策是硬约束） |
| 置信度低 | 宏观数据正常 | confidence<0.3 | **定量主导**，tau 回退 1.0 |
| 风险等级冲突 | 宏观z-score正常 | Serenity 风险等级高 | **取更保守值** |

## 6. 融合策略算法

**文件**: `Algorithm.CSharp/DingLiangDingXingAlgorithm.cs`

```csharp
public class DingLiangDingXingAlgorithm : QCAlgorithm
{
    private FixedBlackLittermanPortfolioConstructionModel _blModel;
    private MaximumSectorExposureRiskManagementModel _sectorExposureModel;
    
    public override void Initialize()
    {
        // 1. 股票池 (同 V4)
        SetUniverseSelection(new AShareBarraCNE5V4UniverseSelectionModel(...));
        
        // 2. 双 Alpha: 定量 + 定性
        SetAlpha(new CompositeAlphaModel(
            new AShareBarraCNE5V4AlphaModel(...),
            new MacroSignalAlphaModel()
        ));
        
        // 3. BL 组合构建 (自动融合双视图)
        _blModel = new FixedBlackLittermanPortfolioConstructionModel(
            delta: 2.2, tau: 0.05, riskFreeRate: 0.03);
        SetPortfolioConstruction(_blModel);
        
        // 4. 双层风控 (不新建模型)
        _sectorExposureModel = new MaximumSectorExposureRiskManagementModel(
            maximumSectorExposure: 0.25m);
        SetRiskManagement(new CompositeRiskManagementModel(
            new AShareBarraCNE5V4RiskManagementModel(...),
            _sectorExposureModel
        ));
        
        // 5. 注册宏观指标自定义数据源
        AddData<MacroIndicatorData>("MACRO", Resolution.Daily);
    }
    
    public override void OnData(Slice data)
    {
        if (data.TryGetValue<MacroIndicatorData>("MACRO", out var macro))
        {
            _blModel.SetMacroAdjustments(macro.TauMultiplier, macro.DeltaOverride);
            _sectorExposureModel.SetMaximumSectorExposure(
                (decimal)macro.SectorExposureCap);
            
            Log($"Macro: risk={macro.CompositeRiskLevel}, " +
                $"tau={macro.TauMultiplier:F1}x, delta={macro.DeltaOverride:F1}, " +
                $"sectorCap={macro.SectorExposureCap:P0}");
        }
    }
}
```

**与 V1 的代码量对比**：

| 组件 | V1 | V2 |
|------|----|----|
| MacroIndicatorData | 12 个字段 | 9 个字段 |
| MacroSignalAlphaModel | 产个股+板块 Insight (50+) | 只产板块 Insight (5-10) |
| MacroAwareRiskManagementModel | 新建 335 行 | **不需要** |
| FixedBlackLitterman 修改 | 无 | 新增 SetMacroAdjustments() (5行) |
| 新增 C# 代码总量 | ~800 行 | ~200 行 |

## 7. InfluxDB 新增 Measurements

### 7.1 serenity_sector_weight
```
tags: market, theme, sector
fields: weight_adjustment(float), source(string), confidence(float)
```

### 7.2 serenity_risk_signal
```
tags: market, theme
fields: composite_risk_level(string), risk_score(float), macro_stance(float),
  tau_multiplier(float), delta_override(float), sector_exposure_cap(float),
  policy_monetary(string), policy_regulatory(string), policy_geopolitical(string),
  signal_confidence(float)
```

### 7.3 serenity_ticker_signal
```
tags: ticker, market, theme
fields: demand_inflection(int), chokepoint_severity(int), supplier_concentration(int),
  evidence_quality(int), valuation_disconnect(int), pe_ttm(float), pb(float),
  roe_latest(float), pledge_ratio(float)
```

## 8. 数据流全图

```
┌─────────────────────── 定性发现 ───────────────────────┐
│                                                         │
│  Serenity 五层分析 (手动/定时触发)                        │
│    ↓ scorecard.json + Markdown                          │
│    ↓                                                    │
│  ┌────────── 路径A: 机会发现 ──────────┐                 │
│  │  bridge 提取 theme+sectors+signals  │                 │
│  │  → 写 local-strategies/ JSON        │                 │
│  │  → SoloQuant pipeline 自动拾取      │                 │
│  │  → 生成新策略 → 回测 → live-paper   │                 │
│  └─────────────────────────────────────┘                 │
│                                                         │
│  ┌────────── 路径B: 信号融合 ──────────┐                 │
│  │  scorecard.json                      │                 │
│  │  + tushare macro (PMI/M2/CPI)       │                 │
│  │  → macro_signal_bridge.py            │                 │
│  │  → macro_signal_latest.json          │                 │
│  │  → InfluxDB (3个新 measurements)     │                 │
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
│    - BL.SetMacroAdjustments(tau, delta)                  │
│    - SectorExposure.SetMax(cap)                          │
│                                                         │
│  双 Alpha 产出 Insight:                                  │
│    ┌─────────────────────┐  ┌─────────────────────┐     │
│    │ BarraCNE5V4Alpha     │  │ MacroSignalAlpha    │     │
│    │ (定量, 个股级)        │  │ (定性, 板块级)       │     │
│    │ SourceModel="Barra"  │  │ SourceModel="Macro" │     │
│    └──────────┬──────────┘  └──────────┬──────────┘     │
│               ↓                         ↓                │
│         FixedBlackLitterman 双视图融合                    │
│           View1=Barra, View2=Macro                       │
│           tau/delta 由宏观信号动态调整                     │
│               ↓                                         │
│         组合权重输出                                      │
│               ↓                                         │
│  双层风控:                                               │
│    V4RM: 止损/波动率/Sharpe (定量)                        │
│    MaxSectorExposure: 板块上限 (宏观动态调整)              │
│               ↓                                         │
│         最终持仓目标                                      │
└─────────────────────────────────────────────────────────┘
```

## 9. 实施阶段

| Phase | 任务 | 天数 |
|-------|------|------|
| 0 | 基础设施：bridge.py + InfluxDB + Grafana + 信号目录 | 2 |
| 1 | LEAN 自定义数据源：MacroIndicatorData.cs + 测试 | 2 |
| 2 | 宏观 Alpha + BL 调参：MacroSignalAlphaModel + SetMacroAdjustments + 调参 | 3 |
| 3 | 板块暴露动态调整：OnData 中调整 SectorExposure | 1 |
| 4 | 融合策略 + 回测：DingLiangDingXingAlgorithm + 对比测试 | 3 |
| 5 | 机会发现 + 管线集成：路径A + sqctl + pipeline stage | 2 |
| **合计** | | **13天** |

## 10. 新增文件清单

| 文件 | 类型 | 行数估计 | 说明 |
|------|------|---------|------|
| `Algorithm.CSharp/MacroIndicatorData.cs` | C# | ~120 | 宏观指标自定义数据源 |
| `Algorithm.CSharp/MacroSignalAlphaModel.cs` | C# | ~80 | 板块级宏观 Alpha |
| `Algorithm.CSharp/DingLiangDingXingAlgorithm.cs` | C# | ~60 | 融合策略主算法 |
| `Scripts/macro_signal_bridge.py` | Python | ~400 | 信号桥接（含路径A+路径B） |
| `Launcher/config/config-dingliangdingxing-live-paper.json` | JSON | ~80 | Live-paper 配置 |
| `monitoring/grafana/dashboards/lean/macro-signal-dashboard.json` | JSON | ~200 | Grafana 仪表板 |

**修改文件（仅 1 处，5 行）**：

| 文件 | 修改 |
|------|------|
| `Algorithm.CSharp/FixedBlackLittermanPortfolioConstructionModel.cs` | 新增 `SetMacroAdjustments()` + 2 行变量替换 |

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Serenity 分析频率低 | bridge 同时拉取 tushare 宏观数据做定量补充 |
| LLM 宏观判断可能有误 | 低置信度时自动降权（tau 回退 1.0） |
| 宏观信号过时 | 恒定持续 + bridge 每4小时刷新 + 信号时间戳日志 |
| 回测无历史宏观信号 | 从 Serenity 历史 scorecard 反向生成历史信号 |
| 板块映射不匹配 | 统一映射表写在 bridge 中，策略不关心映射 |
| BL tau 调参不收敛 | 回测覆盖 2020-2026 全周期 |
| 板块代表标的不具代表性 | 每板块 2 只龙头，宏观只调方向不调具体权重 |
