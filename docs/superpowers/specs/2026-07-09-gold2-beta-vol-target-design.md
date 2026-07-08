# Gold2 Beta+波动率目标策略设计

**日期:** 2026-07-09
**状态:** Draft → 待用户审核
**作者:** brainstorming session 2026-07-09
**协议来源:** `docs/gold2-change.md`(交易逻辑) + `docs/gold2-bodong.md`(波动率目标量化策略)
**硬约束:** 三层管线 + 三个立足 + 五层架构(见 `docs/superpowers/specs/2026-07-05-auto-optimize-quant-strategy-design.md` §1.1-1.3)

## 0. 目标与范围

IC 验证已证明"预测隔夜到日内的修正"在日频是死的(见 CCF 分析:`docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md` Gate 2 EFFECTIVENESS_FAIL, OOS IC=-0.065)。gold2 不再找 alpha,转做 **beta 暴露 + 风险叠加**:收益来源是金价长期趋势(beta),alpha 层工作只剩动态调节仓位大小,目标是拿到金价上涨的大部分收益,同时在下跌或波动加剧时主动减仓、压低自身回撤。

gold2 是**第二条独立的黄金 ETF 量化策略**,与 `GoldOvernightPremiumAlgorithm` 并存,不替换、不共享仓位。

**首期交付范围:** 完整五层 Framework 策略 + FRED 宏观数据管线 + 数据 Gate + 回测对比验证。

## 1. 硬约束(最高优先级)

### 1.1 三个立足

1. **满足 A 股市场实际要求** — 真实 A 股标的 518880(SSE 华安黄金 ETF)、Asia/Shanghai 时区、100 股整手规则、不可做空。
2. **使用 tushare 真实数据** — 518880 与 AU.SHF 日线必须来自 tushare(已回填于 `Data/equity/sse/daily/518880.csv` 与 tushare_data_v2);FRED 作为宏观补充数据源允许使用(VIX VIXCLS、DFII10 实际利率),但不替代 tushare 价格主体。
3. **使用 LEAN 原生接口** — 完整实现五层 `I*Model` 接口,复用 LEAN Portfolio/Statistics/Indicator,价格因子在 C# 内实时算,不自算组合价值或统计。

### 1.2 三层管线

```
Factor Zoo (Common/Factors/Forward/)
  Gold2TrendFactor.cs          ← MA20/MA120 sign + AU 同向确认
  Gold2VolRegimeFactor.cs      ← EWMA σ(λ=0.94) + 平滑 w_smooth
  Gold2ExtremeRiskFactor.cs    ← VIX P95 OR RVol P95 触发标志
  Gold2RealRateCapFactor.cs    ← 包装 GoldRealRateRegimeFactor → cap
       ↓ 消费
Model Zoo (Algorithm.CSharp/Models/Gold2/)
  Gold2TrendAlphaModel.cs
  Gold2VolTargetPortfolioModel.cs
  Gold2ExtremeRiskModel.cs
  Gold2RealRateCapModel.cs
       ↓ 消费
Strategy (Algorithm.CSharp/)
  Gold2BetaVolTargetStrategy.cs
```

### 1.3 五层架构(源自 `docs/LEANarch.md`)

```
L1 Universe → L2 Alpha → L3 Portfolio → L4 Risk → L5 Execution
```

完整实现五个 `I*Model`,对齐 V4 Barra 的框架深度。

### 1.4 零侵入边界

- 不改 `GoldOvernightPremiumAlgorithm` 及其因子/config/custom data(并存独立)。
- 新增文件仅 gold2 命名空间(`Gold2*`)+ `FredMacroData.cs` + `fetch_fred_macro.py` + `backfill_au_lean.py` + `gold2_data_gates.py`。
- 复用已有 `GoldRealRateRegimeFactor`、`AShareLotSizeExecutionModel`、A 股 Fee/Fill/BuyingPower/Settlement 模型。

## 2. 决策汇总

| 维度 | 选择 | 理由 |
|---|---|---|
| 策略范式 | beta 暴露 + 风险叠加 | IC 已证伪日频 alpha,beta 路线更可验证 |
| 价格主信号 | 518880 自身 | 可交易标的,与持仓 P&L 一致 |
| 趋势辅助确认 | AU.SHF MA 同向 | change.md 第一层要求,CFTC 不可得退路 |
| 波动率估计 | EWMA λ=0.94 | bodong.md 第一步,vol clustering 适配 |
| 仓位平滑 | α=0.25 + 5% dead-zone | bodong.md 第三步,降频 |
| 调仓节奏 | 日频计算 + 5% 阈值触发 | 平衡响应性与成本 |
| 极端风险开关 | VIX P95 OR RVol P95 → cap 0.30 | bodong.md 第五步,次日硬执行 |
| 实际利率帽子 | DFII10 RISING_FAST → cap 0.60 | change.md 第四层 |
| CFTC 确认 | 跳过,用 AU 同向弱代理 | tushare 无 COMEX COT,cftc.gov 国内不稳 |
| GPR | RVol P95 代理 | FRED 无 GPR,波动率同步代理 |
| 宏观数据源 | FRED(VIX VIXCLS + DFII10) | 用户已提供 FRED key |
| 架构深度 | 完整五层 Framework | 对齐 LEANarch.md V4 模式 |
| 与 overnight 关系 | 并存独立 | 第二条策略,不互斥(实盘需调度层) |

## 3. 五层架构映射

| 层 | LEAN 接口 | gold2 实现 | 职责 |
|---|---|---|---|
| L1 Universe | `IUniverseSelectionModel` | `Gold2UniverseSelectionModel : ManualUniverseSelectionModel` | 固定 518880(SSE),AddEquity Daily |
| L2 Alpha | `IAlphaModel` | `Gold2TrendAlphaModel : AlphaModel` | 消费 `Gold2TrendFactor` → Insight(Up/Flat);空头降 20% 底仓 |
| L3 Portfolio | `IPortfolioConstructionModel` | `Gold2VolTargetPortfolioModel : PortfolioConstructionModel` | 消费 `Gold2VolRegimeFactor` → w_smooth × dirCoef + 5% dead-zone |
| L4 Risk | `IRiskManagementModel` | `Gold2ExtremeRiskModel` + `Gold2RealRateCapModel` | 极端开关 cap 0.30 / 实际利率 cap 0.60,串联取 min |
| L5 Execution | `IExecutionModel` | `AShareLotSizeExecutionModel`(复用) | 100 股整手向下取整 |

## 4. 因子数学

对齐 `gold2-bodong.md` 五步公式 + `gold2-change.md` 四层逻辑。所有因子实现 `IFactor`(沿用 overnight 接口形态,值在 C# 内实时算)。

### 4.1 Gold2TrendFactor(趋势方向,L2 输入)

```
MA20_t   = SMA(518880 close, 20)
MA120_t  = SMA(518880 close, 120)
MA20_au  = SMA(AU.SHF close, 20)
MA120_au = SMA(AU.SHF close, 120)

Trend_518880 = sign(MA20 - MA120)   ∈ {-1, 0, +1}
Trend_au     = sign(MA20_au - MA120_au)

Confirm = (Trend_518880 == Trend_au 且 同号) ? 1 : 0.5
TrendSignal_t = Trend_518880 * Confirm
```

- `Compute()` 返回 `FactorValue{ Value=(decimal)Trend_518880, RawValue=(decimal)Confirm }`。
- L2 读 `Value` 得方向,`RawValue` 得确认系数。
- **冷启动**: MA120 需 120 交易日,2020-01-01 起算则 2020-06 后才有信号;冷启动期 `TrendSignal=0`(Flat)。

### 4.2 Gold2VolRegimeFactor(波动率目标,L3 输入)

```
r_t = ln(P_t / P_{t-1})
σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}      ← EWMA, λ=0.94
σ_annual_t = σ_t · √252
σ_0 = std(前 60 日 r)                     ← 冷启动种子

w_t = min(w_max=1.0, σ_target / σ_annual_t)    ← σ_target=0.11
w_smooth_t = α·w_t + (1-α)·w_smooth_{t-1}      ← α=0.25
```

- `Compute()` 返回 `FactorValue{ Value=(decimal)w_smooth_t, RawValue=(decimal)σ_annual_t }`。
- 状态: 因子持有 `σ_prev`、`w_smooth_prev`。
- **冷启动**: 前 60 日累积收益,第 60 日算简单方差作种子,之后切 EWMA;冷启动期 `w=0`。

### 4.3 Gold2ExtremeRiskFactor(极端开关,L4 输入)

```
VIX_P95    = percentile(VIX 历史, 95)          ← 滚动 1260 交易日(5y)
RVol_60d   = std(518880 日收益, 60) · √252
RVol_P95   = percentile(RVol_60d 序列, 95)     ← GPR 代理
Trigger_t  = (VIX_t > VIX_P95) OR (RVol_60d_t > RVol_P95)
```

- `Compute()` 返回 `FactorValue{ Value=Trigger?1m:0m, RawValue=(decimal)RVol_60d_t }`。
- L4 读 `Value`: 1 → cap 0.30;0 → 放行。
- **5 年窗口**: VIX 序列不足时窗口自适应缩短,但不少于 252 日。
- **VIX 缺失日**: 因子用 `lastVix` 前值填充(LEAN custom data 缺日默认跳过)。

### 4.4 Gold2RealRateCapFactor(实际利率帽子,L4 输入)

复用已有 `GoldRealRateRegimeFactor`(从 DFII10 算 regime)。

```
Regime = GoldRealRateRegimeFactor.Compute(...)
Cap = (Regime == RISING_FAST) ? 0.6m : 1.0m
```

- 包装类,`Compute()` 返回 `FactorValue{ Value=Cap }`。
- L4 与 ExtremeRisk 串联: `final_cap = min(extreme_cap, realrate_cap)`。
- **DFII10 不可用**: regime=UNAVAILABLE → Cap=1.0(不限制,不阻断)。

### 4.5 参数汇总(全部 `GetParameter`,可被 Layer A 扫描)

| 参数 | 默认 | 范围 | 来源 |
|---|---|---|---|
| `trend-ma-short` | 20 | [10,40] | bodong 第四步 |
| `trend-ma-long` | 120 | [60,200] | bodong 第四步 |
| `ewma-lambda` | 0.94 | [0.85,0.99] | bodong 第一步 |
| `vol-target` | 0.11 | [0.08,0.15] | bodong 第二步 |
| `vol-warmup` | 60 | [40,120] | bodong 初始化 |
| `smooth-alpha` | 0.25 | [0.1,0.5] | bodong 第三步 |
| `rebalance-threshold` | 0.05 | [0.02,0.10] | bodong dead-zone |
| `extreme-vol-cap` | 0.30 | [0.20,0.50] | bodong 第五步 |
| `realrate-cap` | 0.60 | [0.40,0.80] | change 第四层 |
| `trend-floor` | 0.20 | [0.0,0.30] | change 第一层底仓 |

## 5. 五层 Model 实现

目录 `Algorithm.CSharp/Models/Gold2/`。

### 5.1 L1 Gold2UniverseSelectionModel

```csharp
public class Gold2UniverseSelectionModel : ManualUniverseSelectionModel
{
    public override IEnumerable<Symbol> CreateUniverseSelection() =>
        new[] { Symbol.Create("518880", SecurityType.Equity, Market.SSE) };
}
```

AU.SHF 不进 universe(非可交易),只在因子内取值(见 §6.4)。

### 5.2 L2 Gold2TrendAlphaModel

```csharp
public class Gold2TrendAlphaModel : AlphaModel
{
    private readonly Gold2TrendFactor _trend;
    private readonly decimal _floor;          // trend-floor=0.20

    public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
    {
        var v = _trend.Compute(_gold, algorithm.Time);
        int dir = (int)v.Value;
        decimal confirm = v.RawValue;

        InsightDirection insightDir;
        decimal weight;
        if (dir > 0)      { insightDir = InsightDirection.Up;   weight = 1.0m * confirm; }
        else if (dir < 0) { insightDir = InsightDirection.Flat; weight = _floor; }
        else              { insightDir = InsightDirection.Flat; weight = _floor; }

        yield return new Insight(_gold, algorithm.Time, TimeSpan.FromDays(1),
            InsightType.Price, insightDir, weight, "gold2-trend");
    }
}
```

`weight` 作为"方向系数"传入 L3,非直接持仓权重。周期 1 日,Daily 数据每日 Update 一次。

### 5.3 L3 Gold2VolTargetPortfolioModel(核心)

```csharp
public class Gold2VolTargetPortfolioModel : PortfolioConstructionModel
{
    private readonly Gold2VolRegimeFactor _vol;
    private readonly decimal _rebalThreshold;  // 0.05
    private decimal _lastActualWeight;

    public override IEnumerable<PortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
    {
        if (insights.Length == 0) yield break;
        var ins = insights[0];
        decimal wSmooth = _vol.Compute(_gold, algorithm.Time).Value;
        decimal dirCoef = ins.Weight;

        decimal targetWeight = wSmooth * dirCoef;

        if (Math.Abs(targetWeight - _lastActualWeight) < _rebalThreshold)
            targetWeight = _lastActualWeight;  // dead-zone 维持
        else
            _lastActualWeight = targetWeight;

        yield return new PortfolioTarget(_gold, targetWeight);
    }
}
```

`w_smooth × 方向系数`。多头+确认 → `w_smooth × 1.0`;空头/中性 → `w_smooth × floor`。dead-zone 在模型层实现。L4 会进一步 cap。

### 5.4 L4 Gold2ExtremeRiskModel + Gold2RealRateCapModel

```csharp
public class Gold2ExtremeRiskModel : RiskManagementModel
{
    private readonly Gold2ExtremeRiskFactor _ext;
    private readonly decimal _extremeCap;      // 0.30

    public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IEnumerable<IPortfolioTarget> targets)
    {
        foreach (var t in targets)
        {
            var triggered = (int)_ext.Compute(_gold, algorithm.Time).Value == 1;
            decimal cap = triggered ? _extremeCap : 1.0m;
            decimal w = Math.Min(TargetToWeight(t), cap);
            yield return WeightToTarget(t.Symbol, w);
        }
    }
}
```

`Gold2RealRateCapModel` 同构,cap=0.6(RISING_FAST)或 1.0。两者在 Strategy `AddRiskManagement` 顺序添加,LEAN Composite 依次应用 → `final = min(extreme, realrate, target)`。**硬执行不参与 dead-zone**(bodong 第五步)。

### 5.5 L5 AShareLotSizeExecutionModel(复用)

直接复用 `LEANarch.md` 列出的 `AShareLotSizeExecutionModel`,目标持仓向下取整 100 股。无新增代码。

### 5.6 Strategy 拼装

```csharp
public class Gold2BetaVolTargetStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
{
    private Symbol _gold;
    private Gold2TrendFactor _trend;
    private Gold2VolRegimeFactor _vol;
    private Gold2ExtremeRiskFactor _ext;
    private Gold2RealRateCapFactor _realrate;

    public override void Initialize()
    {
        SetAccountCurrency(Currencies.CNY);
        SetCash(GetDecimalParameter("initial-capital", 1_000_000m));
        SetStartDate(GetDateParameter("start-date", new DateTime(2020,1,1)));
        SetEndDate(GetDateParameter("end-date", new DateTime(2026,6,23)));
        SetBenchmark("518880");

        var eq = AddEquity("518880", Resolution.Daily, Market.SSE);
        eq.FeeModel = new AShareStockFeeModel();
        eq.FillModel = new AShareStockFillModel();
        eq.BuyingPowerModel = new AShareStockBuyingPowerModel();
        eq.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
        _gold = eq.Symbol;

        AddData<FredMacroData>("VIX", Resolution.Daily, TimeZones.Utc);
        AddData<FredMacroData>("DFII10", Resolution.Daily, TimeZones.Utc);

        _trend = new Gold2TrendFactor(GetIntParameter("trend-ma-short",20), GetIntParameter("trend-ma-long",120));
        _vol = new Gold2VolRegimeFactor(GetDecimalParameter("ewma-lambda",0.94m),
            GetDecimalParameter("vol-target",0.11m), GetIntParameter("vol-warmup",60),
            GetDecimalParameter("smooth-alpha",0.25m));
        _ext = new Gold2ExtremeRiskFactor();
        _realrate = new Gold2RealRateCapFactor(GetDecimalParameter("realrate-cap",0.6m));

        SetUniverseSelection(new Gold2UniverseSelectionModel());
        SetAlpha(new Gold2TrendAlphaModel(_trend, GetDecimalParameter("trend-floor",0.2m)));
        SetPortfolioConstruction(new Gold2VolTargetPortfolioModel(_vol, GetDecimalParameter("rebalance-threshold",0.05m)));
        AddRiskManagement(new Gold2ExtremeRiskModel(_ext, GetDecimalParameter("extreme-vol-cap",0.3m)));
        AddRiskManagement(new Gold2RealRateCapModel(_realrate));
        SetExecution(new AShareLotSizeExecutionModel());
    }

    public override void OnData(Slice data) { /* 喂因子:EWMA 状态、MA、滚动分位 */ }
}
```

## 6. 数据注入

### 6.1 FRED 宏观 custom BaseData

`Common/Data/Custom/Gold/FredMacroData.cs`,沿用 `GoldOvernightSignal` 模式:

```csharp
public class FredMacroData : BaseData
{
    public decimal MacroValue { get; set; }    // VIX 值 或 DFII10 实际利率(%)
    public override DateTime EndTime => Time;

    public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
    {
        if (string.IsNullOrWhiteSpace(line) || line.StartsWith("date")) return null;
        var csv = line.Split(',');
        if (csv.Length < 2) return null;
        if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d)) return null;
        if (!decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var v)) return null;
        return new FredMacroData { Symbol = config.Symbol, Time = d, MacroValue = v, Value = v };
    }

    public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
    {
        var fname = config.Symbol.Value.ToLower() + ".csv";
        var path = Path.Combine(Globals.DataFolder, "macro", "fred", fname);
        return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
    }
}
```

文件: `Data/macro/fred/vix.csv`、`Data/macro/fred/dfii10.csv`,列 `date,value`,格式合成示例 `2020-01-02,16.34`(合成示例值,非生产数据)。时区 UTC 日级对齐(日频,不做精细换算)。

### 6.2 FRED 离线下载脚本

`Scripts/fetch_fred_macro.py`:

```python
import os, requests, pandas as pd
FRED_KEY = os.environ["FRED_API_KEY"]
SERIES = {"VIX": "VIXCLS", "DFII10": "DFII10"}
OUT = "/home/project/hope/Lean/Data/macro/fred"
os.makedirs(OUT, exist_ok=True)
for name, sid in SERIES.items():
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={FRED_KEY}&file_type=json&observation_start=2015-01-01"
    r = requests.get(url, timeout=60).json()
    df = pd.DataFrame(r["observations"])[["date","value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df.to_csv(f"{OUT}/{name.lower()}.csv", index=False)
    print(f"{name}: {len(df)} rows")
```

范围 2015-01-01 起(VIX P95 需 5 年预热)。刷新挂 cron 每日 06:00。tushare 仍是真实数据主体,FRED 补宏观。

### 6.3 因子状态推进

因子有状态,在 `OnData` 按 518880 日线推进:

**TrendFactor(无状态查询式)**: 持有 LEAN `SimpleMovingAverage(20/120)` Indicator,`OnData` 推 close,`Compute(t)` 读 Current。AU.SHF 的 SMA 同理。

**VolRegimeFactor(有状态 EWMA)**:

```csharp
private decimal _sigma2Prev, _wSmoothPrev, _prevClose;
private int _warmupCount;
private readonly Queue<decimal> _warmupReturns = new();

public void Update(decimal close, DateTime time)
{
    if (_prevClose > 0)
    {
        var r = (decimal)Math.Log((double)(close / _prevClose));
        _warmupReturns.Enqueue(r);
        if (_warmupCount < _warmup)
        {
            if (_warmupReturns.Count == _warmup) _sigma2Prev = Variance(_warmupReturns);
        }
        else
        {
            _sigma2Prev = _lambda * _sigma2Prev + (1-_lambda) * r * r;
            var sigmaAnn = (decimal)Math.Sqrt((double)_sigma2Prev) * (decimal)Math.Sqrt(252);
            var w = Math.Min(1.0m, _volTarget / sigmaAnn);
            _wSmoothPrev = _alpha * w + (1-_alpha) * _wSmoothPrev;
        }
        _warmupCount++;
    }
    _prevClose = close;
}
```

冷启动前 60 日 `w=0`。IFactor 的 `Compute` 是查询接口,另开 `Update()` 供 OnData 喂数据(与 overnight 的 `InjectValue` 对偶)。

**ExtremeRiskFactor(滚动分位)**: 持 `RollingWindow<decimal>(1260)` 存 VIX 历史 + RVol_60d 序列。`Update(vix, rvol)` 推进;`Compute(t)` 窗口满 252+ 才算 P95。VIX 缺失日用 `lastVix` 兜底。

### 6.4 AU.SHF 数据注入(辅助确认,不入 universe)

选 **tushare 历史回填成 LEAN 可读 CSV**(方案 4.4-A):`Scripts/backfill_au_lean.py` 把 `tushare_data_v2/fut_daily/ts_code=AU.SHF/data.parquet` 转成 `Data/future/shf/daily/AU.SHF.csv`(LEAN future 格式: `date,open,high,low,close,volume,open_interest`,合成示例 `2020-01-02,345.0,348.0,344.0,347.5,12345,67890`)。因子通过 `History` 读,不交易。与 overnight `backfill_gold_au.py` + `518880.csv` 模式一致。

### 6.5 518880 历史数据

已就绪: `Data/equity/sse/daily/518880.csv`(1942 行,2018-2025)。回测窗口 2020-01-01 ~ 2026-06-23 覆盖完整,无需新增。

### 6.6 数据完整性 Gate

`Scripts/gold2_data_gates.py`:
- `gate0_tushare_coverage`: 518880 daily 覆盖 2020-2026 无断点;AU.SHF 覆盖同期。
- `gate0_fred_coverage`: VIX/DFII10 2015-2026 连续。
- `gate0_alignment`: 518880/AU/VIX/DFII10 日期对齐(交易日 inner join ≥ 1500 日)。
- 任一不过 → 阻止回测,报缺失区间。

## 7. 测试与验证

### 7.1 单元测试(NUnit)

`Tests/Algorithm/Gold2BetaVolTargetStrategyTests.cs`,纯逻辑不跑全回测。

**Trend/Alpha**:
- `Trend_BothUp_Confirmed_UpInsight` — MA20>MA120 且 AU 同向 → Up, weight=1.0
- `Trend_Divergent_HalfConfirm` — 518880 多但 AU 空 → Up, weight=0.5
- `Trend_Bearish_FloorOnly` — MA20<MA120 → Flat, weight=floor

**波动率目标**:
- `VolTarget_Warmup_ReturnsZero` — 前 60 日 w=0
- `VolTarget_Steady_ConvergesToTarget` — σ_annual=0.11 恒定 → w≈1.0
- `VolTarget_VolSpike_HalvesWeight` — σ 翻倍 → w 减半
- `Portfolio_DeadZone_NoTrade` — |Δ|<5% 维持 last

**风控**:
- `ExtremeRisk_Triggered_CapsTo03` — VIX>P95 或 RVol>P95 → cap 0.30
- `RealRate_RisingFast_CapsTo06` — RISING_FAST → 0.60;否则 1.0
- `BothRisks_TakeMin` — extreme=0.30 且 realrate=0.60 → 0.30
- `ExtremeRisk_OverridesDeadZone` — 极端触发绕过 dead-zone

预期 ~10 测试。

### 7.2 回测验证(bodong §验证方式)

三条净值曲线对比(写 `Results/gold2-betavol/` + InfluxDB):
1. `baseline`: 满仓持有 518880(SetBenchmark)
2. `vol_only`: 纯波动率目标(趋势层禁用,dirCoef 恒=1.0)
3. `full`: 完整模型

核心指标(bodong 明确"看回撤改善"):
- 最大回撤(baseline vs full)—— 主指标,期望 full < baseline×0.85
- 回撤持续时间(峰值到恢复天数)
- 年化收益、Sharpe(不劣于 baseline 即可)

对比表 `Results/gold2-betavol/comparison.csv`:模式 | 年化收益 | 最大回撤 | 回撤持续 | Sharpe | 调仓次数/年。

**判定线**(有效性 Gate):
- `full` 最大回撤 < `baseline`×0.85 → **EFFECTIVE**
- 否则 → `EFFECTIVENESS_FAIL`,记录诊断,不强制 kill(beta 策略回撤改善是硬指标,失败需返工)。

### 7.3 数据 Gate

§6.6 三个 gate,回测前置。

### 7.4 因果性/无前瞻

- TrendFactor: MA_t 只用 `≤ t` close(SMA 天然因果)
- VolRegimeFactor: EWMA `σ²_t` 用 `r_{t-1}`,不含当日 —— 因果
- ExtremeRiskFactor: P95 用 `≤ t-1` 历史(不含当日 VIX)
- RealRateCap: DFII10 regime 用 `≤ t-1`(FRED 发布滞后用前值)

测试 `NoLookahead_*` 2-3 个。

## 8. 已知局限与边界

1. **CFTC 确认过滤器跳过** — change.md 第一层要求 CFTC 非商业净多头确认,FRED/tushare 无 COMEX COT,cftc.gov 国内不稳。用 AU.SHF MA 同向确认作弱代理。语义偏差:SHFE 持仓 ≠ COMEX 非商业,但同向性有相关性。**升级路径**: 若接入真 COT,`Gold2TrendFactor.Confirm` 升级,接口不变。

2. **GPR 用 RVol P95 代理** — bodong 第五步允许"VIX 或 GPR",选 RVol P95 作代理(地缘冲击同步体现为已实现波动率跳升)。**风险**: RVol 同步非前瞻;VIX 是主前瞻,RVol 补充触发,OR 关系已涵盖。

3. **DFII10 不可用降级** — regime=UNAVAILABLE → RealRateCap=1.0,不阻断,失去长周期帽子。`gate0_fred_coverage` 预警。

4. **AU.SHF 连续合约展期跳空** — 连续主力映射展期日有跳空。MA20/120 长均线不敏感;`gate0_alignment` 检查 `fut_mapping` 展期点,跳空>3% 标记(沿用 overnight Gate 0 映射对齐)。

5. **冷启动期不建仓** — 前 120 日(MA120)+ 60 日(波动种子)取 max=120 交易日(约 6 个月)。2020-01-01 起算,2020-06 后有完整信号。回测净值前 6 个月空仓,对比 baseline 时对齐起算点(从 2020-07 起)。

6. **518880 不可做空** — 空头趋势降底仓 20%。InsightDirection.Flat + weight=floor,不输出 Short。long-only 约束在 BuyingPowerModel 已有。

7. **与 overnight 并存的 518880 仓位冲突** — 两条策略若同时实盘跑 518880,仓位叠加。**实盘上线需 SoloQuant 调度层互斥**。本设计不实现互斥,仅 manifest 标注 `universe_conflict: [GoldOvernightPremiumAlgorithm]`。回测各自独立无冲突。

8. **调仓 dead-zone 与极端开关交互** — dead-zone 在 L3,极端开关在 L4。极端触发 L4 直接 cap 绕过 dead-zone(测试 `ExtremeRisk_OverridesDeadZone` 覆盖)。极端解除后若 |target-last|<5%,因 dead-zone 不回补 —— 设计如此(避免频繁进出),可接受。

## 9. 文件清单

**新增**:
- `Common/Factors/Forward/Gold2TrendFactor.cs`
- `Common/Factors/Forward/Gold2VolRegimeFactor.cs`
- `Common/Factors/Forward/Gold2ExtremeRiskFactor.cs`
- `Common/Factors/Forward/Gold2RealRateCapFactor.cs`
- `Common/Data/Custom/Gold/FredMacroData.cs`
- `Algorithm.CSharp/Models/Gold2/Gold2UniverseSelectionModel.cs`
- `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs`
- `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs`
- `Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs`
- `Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs`
- `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`
- `Launcher/config/config-gold2-beta-vol-target-backtest.json`
- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`
- `Scripts/fetch_fred_macro.py`
- `Scripts/backfill_au_lean.py`
- `Scripts/gold2_data_gates.py`
- `Tests/Algorithm/Gold2BetaVolTargetStrategyTests.cs`

**复用(不改)**:
- `Common/Factors/Forward/GoldRealRateRegimeFactor.cs`
- `Common/Factors/Forward/GoldRegime.cs`
- `AShareLotSizeExecutionModel` / `AShareStockFeeModel` / `AShareStockFillModel` / `AShareStockBuyingPowerModel` / `DelayedSettlementModel`
- `Data/equity/sse/daily/518880.csv`

## 10. 相关文档

- `docs/gold2-change.md` — 交易逻辑(beta+风险叠加)
- `docs/gold2-bodong.md` — 波动率目标仓位模型(五步公式)
- `docs/LEANarch.md` — 五层架构定义
- `docs/superpowers/specs/2026-07-05-auto-optimize-quant-strategy-design.md` — 三层管线 + 三个立足
- `docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md` — overnight 策略(IC 失效证据,gold2 的动机来源)
- FRED API: https://fred.stlouisfed.org/docs/api/fred/
