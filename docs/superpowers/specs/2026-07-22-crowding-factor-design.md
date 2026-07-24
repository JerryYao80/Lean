# 拥挤度因子(crowding)— 因子动物园新增 + 计算 + 回测 + 架构适配 设计规格

> **目标**:在因子动物园中新增拥挤度(crowding)因子,接通"数据下载→计算→注册→回测→检验"全 pipeline,作为独立 alpha 因子进 `FactorRegistry`,供"量化策略构建/优化/回测/复盘/重构"闭环的重构环节(G3-Real / SoloQuant inspiration)从动物园中挑选;同时在 G3-Real LLM prompt 里登记为可选 risk-layer 输入。
>
> **日期**:2026-07-22
> **分支**:待定(新特性,独立于 G3-Real PR #2)
> **范围档**:沪深300 横截面、三维度融合拥挤度、纯多头分层回测、进 G3-Real prompt 登记
>
> **硬线**:`never-modify-lean-native`(不动 LEAN 核心 C#)、`never-modify-existing-features`(只新增 + FactorRegistry 加一行 Register,G3-Real prompt 加一节,默认中性)、反 p-hacking(月度调仓频率预注册,不事后调参,IC 报告诚实)。

---

## 0. 背景与已确认范围

用户在"量化策略构建、量化策略优化、回测、回测、复盘、量化策略重构"闭环环境中,要求重构环节优先从因子动物园选因子。本次新增拥挤度因子。

5 个澄清已确认:
1. **接入点**:crowding 作为独立 alpha 因子进因子动物园(不作为 Barra 风险因子),供重构环节挑选。用户原话:"重构的量化策略优先从因子动物园中选择"。
2. **标的范围**:沪深300 成分股横截面(拥挤度是横截面因子,单标的无意义;全市场太重)。
3. **拥挤度定义**:三维度融合(trading 0.35 + fund 0.35 + chip 0.30),即现有 `CrowdingFactors.composite_crowding`。
4. **cyq_perf 数据**:启用 cyq_perf 下载(加入 tushare incremental_update active 集,按 ts_code 迭代沪深300成分股)。
5. **回测形态**:纯多头分层(月度调仓,选低拥挤 30% 等权,不做空——符合 A股 T+1/无融券)。
6. **与重构闭环**:进 G3-Real prompt 登记(让重构 LLM 知道动物园有 crowding 可挑)。

---

## 1. 架构 — 因子动物园原生模式(Precomputed + InjectValue)

crowding 沿用既有 `ChipPeakFactorZooAlphaModel` / `VarFactorZooAlphaModel` 的因子动物园模式。**零 LEAN 核心改动**,**零既有特性行为改动**(只新增文件 + FactorRegistry 加一行 Register + G3-Real prompt 加一节)。

```
tushare 五表 (daily_basic/moneyflow/margin/hsgt_top10/cyq_perf)
        ↓ (incremental_update active 集 + cyq_perf 启用)
crowding_factor_builder.py  ← mirror barra_cne5v2_factor_builder
        ↓ composite_crowding(row) (复用 CrowdingFactors.py)
按日落盘 parquet + InfluxDB measurement lean_ashare_crowding_factor
        ↓ (回测时按日注入)
AShareCrowdingFactorZooStrategy.Initialize
  → FactorRegistry.Initialize() + Get("crowding")
  → CrowdingFactorZooAlphaModel.Update: Compute(sym) → 低拥挤组选股 → Insight.Price
        ↓
LEAN 原生回测 (A股 lot/T+1 Execution) → IC/分层/IR 报告
```

### 1.1 三处与既有架构的衔接点

1. `FactorRegistry.Initialize()`(`Common/Factors/Core/FactorRegistry.cs:20-71`)加 `Register(new CrowdingFactor());`。stub `CrowdingFactor.cs` 已存在(`Common/Factors/Sentiment/CrowdingFactor.cs:10-32`,Precomputed+InjectValue),只注册不改行为。
2. `CrowdingFactorZooAlphaModel` mirror `ChipPeakFactorZooAlphaModel.cs:137,155`(InjectValue + Compute + Insight)。
3. `Scripts/gold2_closed_loop/g3_real_llm_client.py` 的 `build_prompt` 加一节可选因子登记(跨 G3-Real 特性,诚实标注)。

### 1.2 关键代码事实(file:line)

- `IFactor` 接口(`Common/Factors/Core/IFactor.cs:8-20`):`FactorResult Compute(Symbol, DateTime, IEnumerable<BaseData>)` + `FactorRankResult ComputeRank(IEnumerable<Symbol>, DateTime)` + `bool IsAvailable(Symbol, DateTime)`。
- `FactorMetadata`(`Common/Factors/Core/FactorMetadata.cs:5-17`):Id/Name/Category/Scope/ComputeMode/DataSource/Parameters/Dependencies/CsvPath。
- `FactorCategory` 枚举(`Common/Factors/Core/FactorCategory.cs:10-13`):Trend/Value/Volatility/Quality/Sentiment/Liquidity/Chip/Forward。
- `CrowdingFactor` stub(`Common/Factors/Sentiment/CrowdingFactor.cs:10-32`):`Id="crowding"`, `Category=Sentiment`, `ComputeMode=Precomputed`, `DataSource="tushare_moneyflow"`, `InjectValue(Symbol, decimal)`。
- `CrowdingFactors.py`(`Algorithm.Python/CrowdingFactors.py:42-230`):`composite_crowding(row, params)` 三维度加权(trading 0.35 + fund 0.35 + chip 0.30),纯静态。
- `ChipPeakFactorZooAlphaModel.cs:137,155`:InjectValue + Compute + Insight.Price 参考。
- `OptionVolArbFactorZooStrategy.cs:33`:SetAlpha + AddEquity(Market.SSE) 参考。
- A股 lot 100(`Common/Securities/Equity/AShareStock.cs:56`);T+1(`Common/Orders/Fills/AShareStockFillModel.cs:27`);ST 拒买(`AShareStockFillModel.cs:43`)。
- tushare api_registry:`daily_basic`(185)、`moneyflow`(570)、`moneyflow_hsgt`(577)、`hsgt_top10`(202)、`margin`(542)、`cyq_perf`(439,enabled=False 需启用)。

---

## 2. 组件(6 新文件 + 2 处衔接)

| 组件 | 文件 | 职责 | 新建/改 |
|---|---|---|---|
| 数据下载启用 | `data-source/tushare/incremental_update.py` + `api_registry.py` | cyq_perf 加入 active 集(按 ts_code 迭代沪深300成分股);daily_basic/moneyflow/margin/hsgt_top10 已 active 不动 | 改 |
| 因子计算 builder | `data-source/tushare/crowding_factor_builder.py` | 五表 join 成 crowding row → `CrowdingFactors.composite_crowding` → 按日落盘 parquet + InfluxDB | 新建 |
| C# 因子注册 | `Common/Factors/Core/FactorRegistry.cs:20-71` | 加一行 `Register(new CrowdingFactor());` | 改(+1行) |
| AlphaModel | `Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs` | mirror `ChipPeakFactorZooAlphaModel`:Initialize `FactorRegistry.Get("crowding")`,Update 按日 InjectValue + Compute → 低拥挤 30% 选股 → `Insight.Price` | 新建 |
| 回测策略 | `Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs` | mirror `OptionVolArbFactorZooStrategy`:AddEquity 沪深300成分股 + `SetAlpha` + A股 lot/T+1 Execution + 月度调仓 | 新建 |
| universe 选择 | `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs` | 沪深300成分股(动态,按调仓日 tushare index_weight;若既有 CSI300 universe model 存在则复用) | 新建或复用 |
| 配置 | `Launcher/config/config-crowding-factorzoo-backtest.json` | mirror `config-option-vol-arb-factorzoo-backtest.json` | 新建 |
| 检验报告 | `docs/crowding-backtest.md` | IC + 分层 decile + IR + 换手 | 新建 |
| G3-Real prompt 登记 | `Scripts/gold2_closed_loop/g3_real_llm_client.py` | `build_prompt` 加"可选因子:动物园 crowding(三维度 Precomputed)"一节 | 改(跨特性) |

### 2.1 AlphaModel 核心逻辑(纯多头分层,月度调仓)

```
每月调仓日 OnData:
  1. 从 parquet 读当日全沪深300 的 composite_crowding
  2. InjectValue 给 CrowdingFactor(每只 ts_code)
  3. 排序,选最低 30%(低拥挤组),等权
  4. 对每只选中的发 Insight.Price(权重 = 1/组大小,方向 up)
  5. 未选中的不发 insight(LEAN 自动平仓)
非调仓日: 不发新 insight(持有)
```

---

## 3. 数据流

### 3.1 离线(每日,incremental_update 调度)

```
tushare daily_basic (换手率/量比/davol20)
tushare moneyflow   (net_mf_ratio 净流入率)
tushare margin      (margin_growth 融资余额增长)
tushare hsgt_top10  (hk_hold_ratio 北向持股比例)
tushare cyq_perf    (cost_5/95pct_adj, weight_avg_adj, winner_rate 筹码)  ← 新启用
        ↓ crowding_factor_builder.join_five_tables(date, ts_code)
   → crowding_row (pd.Series, 9 字段)
        ↓ CrowdingFactors.composite_crowding(row)  (三维度 0.35/0.35/0.30)
   → composite score (0-1)
        ↓
   parquet: result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet
   InfluxDB: lean_ashare_crowding_factor (tags: ts_code; fields: composite, trading, fund, chip, degraded)
```

### 3.2 回测时(LEAN 运行)

```
AShareCrowdingFactorZooStrategy.Initialize:
  AddEquity(沪深300成分股, Daily, SSE/SZSE) + A股 Fee/Fill/BuyingPower/Settlement
  FactorRegistry.Initialize()  → CrowdingFactor 已 Register
  SetAlpha(CrowdingFactorZooAlphaModel)
  SetExecution(AShareLotSizeExecutionModel)

每月调仓日 OnData:
  AlphaModel.Update:
    for ts_code in universe:
      row = read parquet result/crowding-factor/<algo.Time>/<ts_code>  ← 读当日
      _crowdingFactor.InjectValue(sym, composite)
    scores = {sym: _crowdingFactor.Compute(sym, time).Value}
    selected = bottom 30% by score  (低拥挤)
    for sym in selected: Emit Insight.Price(sym, 1/|selected|, up)
    (未选中 → 无 insight → LEAN 平仓)

LEAN TransactionHandler → AShareStockFillModel (lot 100, T+1) → OrderEvent
BacktestingResultHandler → packet (LEAN 原生统计)
```

### 3.3 注入时机(implementation 阶段对齐 ChipPeakFactorZooAlphaModel)

`ChipPeakFactorZooAlphaModel` 的 InjectValue 时机(Initialize 一次注入全历史 vs Update 按日注入)需 implementation 阶段读 `ChipPeakFactorZooAlphaModel.cs:137` 确认后对齐。本规格假设按日注入(回测某日只能读当日及之前的 parquet,反前瞻)。

### 3.4 反 p-hacking / 诚实边界

- crowding parquet 按日切片,回测某日只能读**当日及之前**数据(builder 按日落盘,不前瞻)。
- 月度调仓频率预注册(不事后调参)。
- 检验报告诚实:IC 可能 ≈ 0 或负(拥挤度无预测力),都如实报。

---

## 4. 错误处理

| 错误 | 处理 |
|---|---|
| cyq_perf 下载缺某 ts_code 某日 | builder 该 ts_code 当日 composite = 降级两维度(交易+资金,权重 0.5/0.5);parquet 写 `composite` + `degraded=true`;**不伪造** cyq 字段 |
| cyq_perf 整体下载失败(tushare 频控/限流) | builder 跳过当日,记 `result/crowding-factor/<date>/FAILED.txt` + 错误日志;回测读到缺失日 → universe 全体降级两维度;**不阻塞**回测 |
| 某 ts_code 五表里任一表缺当日 | builder 该 ts_code 当日标 `Missing`,composite 不算;AlphaModel 遇 Missing 跳过该股(不选入低拥挤组)——**不补零**(补零会让 score=0 被误选为"最低拥挤") |
| parquet 文件不存在(回测日早于 builder 覆盖范围) | AlphaModel 抛 `InvalidOperationException`("crowding data missing for <date>; run crowding_factor_builder first")——**大声失败**,不静默跑空回测 |
| tushare hsgt_top10 北向数据停更(历史断点) | builder 该字段按"最近可得日"前值填充 + `hk_hold_stale=true` 标记;**不静默丢弃**该维度 |
| InfluxDB 写失败 | builder 不阻塞落盘 parquet(回测不依赖 InfluxDB);记日志继续 |
| 低拥挤组为空(全 universe Missing/降级) | AlphaModel 当月不发任何 insight(持有现金),报告标注"该月无选股" |
| ST/停牌股 | universe model 已排除(AShareStockFillModel 拒 ST);crowding 不额外处理 |

### 4.1 边界(硬线)

- **不动 LEAN 核心 C#**(`never-modify-lean-native`):Common/Securities、Engine 等不动。
- **不改既有 feature 行为**(`never-modify-existing-features`):FactorRegistry 只 +1 行 Register(默认中性);CrowdingFactor stub 只注册不改;G3-Real prompt 加一节不改既有结构(跨特性,诚实标注)。
- **反 p-hacking**:月度调仓频率预注册;不事后调参;IC 报告诚实。

---

## 5. 测试矩阵

| 测试 | 覆盖 | 断言 |
|---|---|---|
| `test_crowding_factor_registered` | §1 注册 | `FactorRegistry.Initialize()` 后 `Get("crowding")` 非空;Category=Sentiment, ComputeMode=Precomputed, DataSource=tushare_moneyflow |
| `test_crowding_factor_inject_compute` | §1 C# 因子 | `InjectValue(sym, 0.8)` → `Compute(sym).Value == 0.8`;未注入 → `Quality == Missing` |
| `test_composite_crowding_three_axes` | §2 builder 计算 | 合成 row(三维度字段)→ composite = trading*0.35+fund*0.35+chip*0.30;高拥挤 row(全 1)> 低拥挤 row(全 0) |
| `test_crowding_degraded_when_cyq_missing` | §4 降级 | row 无 cyq 字段 → composite = (trading+fund)/2 降级两维度;parquet `degraded=true` |
| `test_crowding_missing_ts_code_skipped` | §4 缺失 | 某 ts_code 五表缺当日 → builder 标 Missing,不算 composite;AlphaModel 跳过该股(不补零) |
| `test_crowding_parquet_missing_raises` | §4 大声失败 | AlphaModel 读不存在的 parquet 日 → raise InvalidOperationException |
| `test_crowding_alpha_selects_low_30pct` | §2 选股 | 10 只 universe,crowding 分数已知 → AlphaModel 选最低 3 只,等权 1/3,方向 up;未选中无 insight |
| `test_crowding_alpha_monthly_rebalance` | §2 调仓 | 非调仓日不重发 insight(持有);调仓日重选 |
| `test_crowding_backtest_runs_lean_native` | §3 回测 | `dotnet run` config-crowding-factorzoo-backtest 跑通,packet 含 LEAN 原生统计(sharpe/drawdown/换手),无自算 |
| `test_crowding_ic_report_honest` | §3 检验 | IC/分层 decile/IR 报告生成;若 IC≈0 或负,报告如实(不 spin) |
| `test_g3real_prompt_lists_crowding` | §2 G3-Real 登记 | `build_prompt` 输出含 "crowding" + "可选因子";零盲年泄漏仍守(G3-Real Task 8 回归不破) |
| `test_no_lean_core_modified` | §6 边界 | `git diff` 确认 Common/Securities、Engine 等未改;FactorRegistry 仅 +1 行 |

`test_crowding_backtest_runs_lean_native` 是真起 dotnet LEAN(慢,~分钟级),其余单测快。

---

## 6. 文件改动清单

| 文件 | 改动 | 目的 |
|---|---|---|
| `data-source/tushare/incremental_update.py` + `api_registry.py` | cyq_perf 加入 active 集 | §3.1 数据 |
| `data-source/tushare/crowding_factor_builder.py` | 新建 builder | §2 计算 |
| `Common/Factors/Core/FactorRegistry.cs:20-71` | +1 行 `Register(new CrowdingFactor())` | §1 注册 |
| `Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs` | 新建 AlphaModel | §2 选股 |
| `Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs` | 新建策略 | §2 回测 |
| `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs` | 新建或复用 universe | §2 标的范围 |
| `Launcher/config/config-crowding-factorzoo-backtest.json` | 新建配置 | §2 回测 |
| `docs/crowding-backtest.md` | 新建检验报告 | §3 检验 |
| `Scripts/gold2_closed_loop/g3_real_llm_client.py` | `build_prompt` 加可选因子登记节 | §2 G3-Real(跨特性) |
| `Tests/gold2_closed_loop/test_crowding_factor.py` 或 `Tests/Factors/test_crowding_factor.py` | 测试矩阵 §5 | 全部 |

**注**:`CrowdingFactor.cs` stub 与 `CrowdingFactors.py` 已存在,不改;只注册 + 接 pipeline。

---

*本规格所有断言均可在主仓 `/home/project/hope/Lean` 内以 file:line 追溯。实施按 writing-plans 产出的计划执行,最终以真起 LEAN 回测(§3.2)+ IC 检验报告(§3)验证。跨 G3-Real 特性的 prompt 登记在 §2/§4.1 诚实标注。*
