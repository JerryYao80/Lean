# 拥挤度因子(crowding)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在因子动物园中新增拥挤度(crowding)因子,接通"数据下载→计算→注册→回测→检验"全 pipeline,作为独立 alpha 因子供重构闭环挑选,并在 G3-Real prompt 登记。

**Architecture:** 因子动物园原生 Precomputed+InjectValue 模式。`CrowdingFactor` stub 已存在且**已在 `FactorRegistry.cs:42` 注册**(spec §1.1 的"加一行 Register"实为已完成——本计划修正为"验证已注册")。真正缺口:builder(五表 join+复权+composite 落盘)、AlphaModel(mirror ChipPeakFactorZooAlphaModel)、Strategy(mirror OptionVolArbFactorZooStrategy + CSI300 universe)、config、IC 检验、G3-Real prompt 登记。

**Tech Stack:** C# .NET 10(LEAN)、Python 3(pandas/pythonnet/requests)、tushare parquet、InfluxDB line protocol。

**Spec:** `docs/superpowers/specs/2026-07-22-crowding-factor-design.md`

---

## 关键代码事实(实施参照)

- `CrowdingFactor` 已注册:`Common/Factors/Core/FactorRegistry.cs:42` `Register(new QuantConnect.Factors.Sentiment.CrowdingFactor());`。stub 在 `Common/Factors/Sentiment/CrowdingFactor.cs:10-32`(`Id="crowding"`, Precomputed, `InjectValue(Symbol, decimal)`)。
- `CrowdingFactors.py:42-230`:`composite_crowding(row, params)` 三维度(trading 0.35+fund 0.35+chip 0.30)。期望字段:`davol20, volume_ratio, net_mf_ratio, margin_growth, hk_hold_ratio, cost_5pct_adj, cost_95pct_adj, weight_avg_adj, winner_rate`。
- `ChipPeakFactorZooAlphaModel.cs`:mirror 模板。构造器 `FactorRegistry.Initialize(); _compositeFactor = FactorRegistry.Get("...")`。Update:`ClearInjectedValues` → 遍历 `ActiveSecurities` → pythonnet 调 loader 取 row → `composite_score` → `InjectValue` → `Take(topN)` → `Insight.Price(sym, 7days, Up, magnitude, null, Name)`。`SymbolToTsCode`:6/51→.SH else .SZ。
- `OptionVolArbFactorZooStrategy.cs`:mirror。Initialize:SetStartDate/EndDate/SetCash/AddEquity/SetAlpha/EqualWeightPortfolioModel/MaxDrawdownRiskModel/SetBenchmark/SetWarmUp。
- `barra_cne5_data_loader.py:132` `load_index_constituents(asof_date, index_code="000300.SH")` 读 `index_weight` parquet → CSI300 成分股列表。universe model 复用此。
- `ToolBox/ChipDataLoader.py:15,34`:`cyq_perf/ts_code={ts_code}/data.parquet`;cost 字段(cost_5pct 等)需 `× adj_factor` 复权成 `_adj` 后缀。**注意**:ChipDataLoader 注释说 moneyflow_hsgt parquet 空——但 fund_crowding 用 `hk_hold_ratio` 来自 `hsgt_top10`(非 moneyflow_hsgt),hsgt_top10 在 api_registry enabled。
- InfluxDB 写:`Scripts/export_forward_factors.py:283` `write_influx(lines, url, org, bucket, token)` line protocol。
- tushare `api_registry.py:439` cyq_perf `enabled=False`(需特殊 ts_code+range)。`incremental_update.py` active 集含 daily_basic/moneyflow/margin(line 34/52/54)。

---

## 文件结构

| 文件 | 责任 | 新建/改 |
|---|---|---|
| `data-source/tushare/api_registry.py:439` | cyq_perf enabled=True + ts_code 迭代策略 | 改 |
| `data-source/tushare/incremental_update.py` | cyq_perf 加入 active 下载循环 | 改 |
| `data-source/tushare/crowding_factor_builder.py` | 五表 join + 复权 + composite_crowding → parquet + InfluxDB | 新建 |
| `Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs` | mirror ChipPeakFactorZooAlphaModel | 新建 |
| `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs` | CSI300 成分股(复用 barra_cne5_data_loader.load_index_constituents) | 新建 |
| `Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs` | mirror OptionVolArbFactorZooStrategy + CSI300 universe + 月度调仓 | 新建 |
| `Launcher/config/config-crowding-factorzoo-backtest.json` | mirror config-option-vol-arb-factorzoo-backtest.json | 新建 |
| `Scripts/gold2_closed_loop/g3_real_llm_client.py` | build_prompt 加可选因子登记节 | 改(跨特性) |
| `docs/crowding-backtest.md` | IC + 分层 + IR + 换手检验报告 | 新建 |
| `Tests/Factors/test_crowding_factor.py` | 测试矩阵 | 新建 |

---

## Task 1: 验证 CrowdingFactor 已注册 + builder 骨架 + 三维度计算单测

**Files:**
- Verify: `Common/Factors/Core/FactorRegistry.cs:42`(已注册,不改)
- Create: `data-source/tushare/crowding_factor_builder.py`
- Test: `Tests/Factors/test_crowding_factor.py`

- [ ] **Step 1: 写失败测试**

`Tests/Factors/test_crowding_factor.py`:
```python
"""Crowding factor: registration + 3-axis composite computation."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def test_crowding_factor_already_registered():
    """spec §1.1 修正: CrowdingFactor 已在 FactorRegistry.cs:42 注册."""
    src = (ROOT / "Common/Factors/Core/FactorRegistry.cs").read_text()
    assert "Register(new QuantConnect.Factors.Sentiment.CrowdingFactor())" in src

def test_composite_crowding_three_axes():
    from Algorithm.Python.CrowdingFactors import CrowdingFactors, DEFAULT_PARAMS
    high = {"davol20": 0.4, "volume_ratio": 2.0, "net_mf_ratio": 0.3,
            "margin_growth": 0.5, "hk_hold_ratio": 10.0,
            "cost_5pct_adj": 9.0, "cost_95pct_adj": 11.0, "weight_avg_adj": 10.0,
            "winner_rate": 90.0}
    low = {"davol20": 0.0, "volume_ratio": 1.0, "net_mf_ratio": 0.0,
           "margin_growth": 0.0, "hk_hold_ratio": 0.0,
           "cost_5pct_adj": 5.0, "cost_95pct_adj": 15.0, "weight_avg_adj": 10.0,
           "winner_rate": 10.0}
    h = CrowdingFactors.composite_crowding(high, DEFAULT_PARAMS)
    l = CrowdingFactors.composite_crowding(low, DEFAULT_PARAMS)
    assert 0 <= l < h <= 1.0

def test_builder_join_five_tables_and_composite(tmp_path, monkeypatch):
    """builder join 五表 + 复权 + composite → parquet. Mock tushare parquet reads."""
    from data_source.tushare import crowding_factor_builder as b
    # mock 五表读取(合成 df), 调 build_day(date, ts_codes) → 校验 parquet 产出 + composite 值
    # 详见 builder 接口; 此处断言 parquet 存在 + 含 composite/trading/fund/chip/degraded 列
    ...
```

- [ ] **Step 2: 运行确认失败** → `pytest Tests/Factors/test_crowding_factor.py -v` FAIL(builder 不存在)

- [ ] **Step 3: 实现 builder**

`data-source/tushare/crowding_factor_builder.py`:
- `join_five_tables(date, ts_codes)`:读 daily_basic/moneyflow/margin/hsgt_top10/cyq_perf 五表 parquet(按 ts_code),merge 成 crowding_row(9 字段)。cyq cost 字段 `× adj_factor` 生成 `_adj` 后缀。
- `build_day(date, ts_codes)`:`CrowdingFactors.composite_crowding(row)` 每只 → 落盘 `result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet`(列:ts_code, composite, trading, fund, chip, degraded, hk_hold_stale)。
- cyq_perf 缺某 ts_code → 降级两维度(trading+fund 权重 0.5/0.5),`degraded=true`。
- 五表任一缺某 ts_code 当日 → 标 Missing,不算 composite。
- `write_influx(measurement="lean_ashare_crowding_factor", ...)`:mirror export_forward_factors.py:283。
- hsgt_top10 断点 → 前值填充 + `hk_hold_stale=true`。

- [ ] **Step 4: 运行确认通过** → `pytest Tests/Factors/test_crowding_factor.py -v` PASS

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/crowding_factor_builder.py Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): builder joins 5 tushare tables -> composite_crowding parquet + InfluxDB (3-axis, cyq adj, degrade-on-missing)"
```

---

## Task 2: 启用 cyq_perf 下载

**Files:**
- Modify: `data-source/tushare/api_registry.py:439`
- Modify: `data-source/tushare/incremental_update.py`

- [ ] **Step 1: 写失败测试**

追加 `test_crowding_factor.py`:
```python
def test_cyq_perf_enabled_in_registry():
    from data_source.tushare.api_registry import STOCK_SPECIAL_APIS
    cyq = [a for a in STOCK_SPECIAL_APIS if a.api_name == "cyq_perf"]
    assert cyq and cyq[0].enabled is True

def test_cyq_perf_in_active_download_set():
    src = (ROOT / "data-source/tushare/incremental_update.py").read_text()
    assert "cyq_perf" in src
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 改 api_registry + incremental_update**

`api_registry.py:439`:cyq_perf `enabled=False` → `enabled=True`(保留 `ChunkStrategy.STOCK` ts_code 迭代 + 注释说明 ts_code+range 处理)。`incremental_update.py`:cyq_perf 加入 active 下载循环(按 ts_code 迭代,沪深300成分股优先;mirror margin 的 ts_code 迭代模式)。

- [ ] **Step 4: 运行确认通过** + `python -m data_source.tushare.incremental_update --dry-run cyq_perf`(若 CLI 支持)确认不抛

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/api_registry.py data-source/tushare/incremental_update.py Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): enable cyq_perf download (ts_code iteration over CSI300)"
```

---

## Task 3: AShareCSI300UniverseSelectionModel

**Files:**
- Create: `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs`
- Test: `Tests/Factors/test_crowding_factor.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_csi300_universe_model_loads_constituents():
    """universe model via pythonnet calls barra_cne5_data_loader.load_index_constituents."""
    src = (ROOT / "Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs").read_text()
    assert "AShareCSI300UniverseSelectionModel" in src
    assert "000300.SH" in src
    assert "load_index_constituents" in src  # 复用 barra loader
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现 universe model**

`AShareCSI300UniverseSelectionModel.cs`:`UniverseSelectionModel` 子类。`CreateUniverses`:pythonnet 调 `barra_cne5_data_loader.load_index_constituents(asof_date, "000300.SH")` → 转 Symbol 列表 → `Universe.Create(...)` 或返回 `ManualUniverseSelectionModel` 包装。镜像 ChipPeakFactorZooAlphaModel 的 pythonnet 初始化模式(`using Python.Runtime`)。月度重选(调仓日刷新成分股)。

- [ ] **Step 4: 运行确认通过** + `dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj` 0 errors

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): AShareCSI300UniverseSelectionModel (reuses barra_cne5_data_loader.load_index_constituents)"
```

---

## Task 4: CrowdingFactorZooAlphaModel

**Files:**
- Create: `Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs`
- Test: `Tests/Factors/test_crowding_factor.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
TRIVIAL_CROWDING_CSV = ...  # 合成 parquet: 10 只 ts_code, composite 0.1..0.9

def test_crowding_alpha_selects_low_30pct(tmp_path, monkeypatch):
    """10 只 universe → AlphaModel 选最低 30%(3 只), 等权, 方向 up."""
    # mock parquet 读取 (tmp_path 下放 10 只合成 parquet)
    # 调 AlphaModel.Update → 断言 insight 数=3, 每个权重 1/3, 方向 Up
    ...

def test_crowding_alpha_monthly_rebalance():
    """非调仓日不重发 insight; 调仓日重选."""
    ...

def test_crowding_parquet_missing_raises():
    """parquet 缺 → InvalidOperationException (大声失败)."""
    ...
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现 AlphaModel**

`CrowdingFactorZooAlphaModel.cs`:mirror `ChipPeakFactorZooAlphaModel`。
- 构造器:`FactorRegistry.Initialize(); _crowdingFactor = FactorRegistry.Get("crowding") as CrowdingFactor;`。参数:`dataFolder, lowQuantile=0.30, rebalanceMonths=1`。
- Update:`ClearInjectedValues` → 遍历 `ActiveSecurities` → 读 `result/crowding-factor/<algo.Time>/<ts_code>.parquet`(当日)→ `InjectValue(sym, composite)` → 排序选最低 30% → `Insight.Price(sym, 30days, Up, 1.0/|selected|, null, Name)`。
- 月度调仓:非调仓日 return 空 insights(持有)。
- parquet 缺 → `throw InvalidOperationException`。
- 用 pythonnet 读 parquet(mirror ChipPeak 的 pythonnet 模式)或直接 C# 读 parquet(若 LEAN 有 DataFrame 支持)——implementer 选,优先 pythonnet 一致性。

- [ ] **Step 4: 运行确认通过** + `dotnet build` 0 errors

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): CrowdingFactorZooAlphaModel (low-crowding 30% long-only, monthly rebalance, loud-fail on missing parquet)"
```

---

## Task 5: AShareCrowdingFactorZooStrategy + config

**Files:**
- Create: `Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs`
- Create: `Launcher/config/config-crowding-factorzoo-backtest.json`
- Test: `Tests/Factors/test_crowding_factor.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_strategy_wires_alpha_universe_execution():
    src = (ROOT / "Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs").read_text()
    assert "AShareCSI300UniverseSelectionModel" in src
    assert "CrowdingFactorZooAlphaModel" in src
    assert "AShareLotSizeExecutionModel" in src or "SetExecution" in src
    assert "EqualWeightPortfolioModel" in src

def test_config_points_to_strategy():
    cfg = json.loads((ROOT / "Launcher/config/config-crowding-factorzoo-backtest.json").read_text())
    assert cfg["algorithm-type-name"] == "AShareCrowdingFactorZooStrategy"
    assert cfg["algorithm-language"] == "CSharp"
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现 strategy + config**

`AShareCrowdingFactorZooStrategy.cs`:mirror `OptionVolArbFactorZooStrategy`。
- `SetUniverseSelection(new AShareCSI300UniverseSelectionModel())`(CSI300 成分股)。
- `SetAlpha(new CrowdingFactorZooAlphaModel(...))`。
- `SetPortfolioConstruction(new EqualWeightPortfolioModel())`。
- `SetRiskManagement(new MaxDrawdownRiskModel(0.20m))`(可选)。
- `SetExecution(new AShareLotSizeExecutionModel())`(A股 lot/T+1)。
- A股 Fee/Fill/BuyingPower/Settlement:universe model 里每只 AddEquity 时设(或 SetDefault 模式)。

`config-crowding-factorzoo-backtest.json`:mirror option-vol-arb config。algorithm-type-name=AShareCrowdingFactorZooStrategy, history-provider=FallbackTushareHistoryProvider, parameters 含 dataFolder/lowQuantile。

- [ ] **Step 4: 运行确认通过** + `dotnet build` 0 errors

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/AShareCrowdingFactorZooStrategy.cs Launcher/config/config-crowding-factorzoo-backtest.json Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): AShareCrowdingFactorZooStrategy + backtest config (CSI300, A-share lot/T+1)"
```

---

## Task 6: G3-Real prompt 登记可选因子

**Files:**
- Modify: `Scripts/gold2_closed_loop/g3_real_llm_client.py`(build_prompt 加一节)
- Test: `Tests/Factors/test_crowding_factor.py`(追加)+ G3-Real Task 8 回归不破

- [ ] **Step 1: 写失败测试**

```python
def test_g3real_prompt_lists_crowding():
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    p = build_prompt({"layer_attribution": {}}, instrument="518880")
    assert "crowding" in p.lower()
    assert "可选因子" in p or "factor zoo" in p.lower()

def test_g3real_prompt_still_zero_blind_leakage():
    """Task 8 回归: 加 crowding 节后 prompt 仍零盲年."""
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    p = build_prompt({"layer_attribution": {}}, instrument="518880")
    for tok in ("blind", "2022", "2023", "2024", "2025"):
        assert tok not in p.lower()
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 改 build_prompt**

在 `g3_real_llm_client.py` 的 `build_prompt` 里加一节(在 OUTPUT contract 之前):
```
可选因子(因子动物园,重构 BuildRiskModels 时可读取作为 regime 判断输入):
- crowding(拥挤度,三维度融合 trading/fund/chip,Precomputed+InjectValue,
  FactorRegistry.Get("crowding").Compute(sym, time).Value ∈ [0,1];
  低拥挤→趋势可持续,高拥挤→回调风险). 仅在 TRAIN 期可得.
```
**注意**:零盲年——不提具体盲年,只说"TRAIN 期可得"。确认 G3-Real Task 8 回归测试(test_no_p_hacking_regression 的 build_prompt 检查)仍通过。

- [ ] **Step 4: 运行确认通过** + `pytest Tests/gold2_closed_loop/test_g3_real_p4_wiring.py -v`(Task 8 回归不破)

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold2_closed_loop/g3_real_llm_client.py Tests/Factors/test_crowding_factor.py
git commit -m "feat(crowding): register crowding as selectable factor in G3-Real LLM prompt (cross-feature, zero blind leakage preserved)"
```

---

## Task 7: 端到端回测 + IC 检验报告

**Files:**
- Create: `docs/crowding-backtest.md`

- [ ] **Step 1: 跑 builder 落盘历史数据**

Run: `python -m data_source.tushare.crowding_factor_builder --start 2020-01-01 --end 2025-12-31 --universe csi300`
Expected:`result/crowding-factor/<date>/<ts_code>.parquet` 全量落盘 + InfluxDB 写入。cyq_perf 缺失日降级标注。

- [ ] **Step 2: 真起 LEAN 回测**

Run: `dotnet run --project Launcher --config Launcher/config/config-crowding-factorzoo-backtest.json`
Expected:packet 含 LEAN 原生统计(sharpe/drawdown/换手/年化)。

- [ ] **Step 3: IC + 分层 decile + IR 检验**

Run: `python -m data_source.tushare.crowding_factor_builder --ic-report`(builder 加 IC 检验子命令,或独立 `Scripts/crowding_ic_report.py`)。计算:每日 IC(composite vs 未来 N 日收益)→ IC 均值/IR;5 分组 decile 累计收益曲线;换手。

- [ ] **Step 4: 写诚实报告**

`docs/crowding-backtest.md`:回测 metrics + IC/IR/分层 + 换手 + 诚实结论(拥挤度是否有预测力:IC 显著正/负/≈0 都如实;低拥挤组是否跑赢沪深300)。标注降级日比例(cyq_perf 缺失)。

- [ ] **Step 5: Commit**

```bash
git add docs/crowding-backtest.md result/crowding-factor/  # 若 parquet 不太大;否则只加报告
git commit -m "docs(crowding): backtest + IC/decile/IR honest report (CSI300 low-crowding 30%)"
```

---

## Self-Review

**1. Spec coverage:** §1(注册已存在→Task 1 验证)、§2(builder Task 1/universe Task 3/alpha Task 4/strategy+config Task 5/prompt Task 6)、§3(数据流 Task 1+2+7)、§4(错误处理:降级 Task 1/大声失败 Task 4)、§5(测试矩阵散布 Task 1-6)、§6(IC 报告 Task 7)。全覆盖。

**2. Placeholder 扫描:** Task 1 test_builder_join_five_tables_and_composite 有 `...` 占位——implementer 补全 mock 细节(这是测试内部 mock 框架,非计划占位)。Task 4 parquet 读取方式(pythonnet vs C# DataFrame)给 implementer 选——合理。无 TBD。

**3. Type 一致:** `CrowdingFactor.InjectValue(Symbol, decimal)` 一致;`composite_crowding(row, params)` 返回 float,AlphaModel 注入时 `(decimal)score` 转 decimal(mirror ChipPeak)。`SymbolToTsCode` 复用 ChipPeak 的。

**4. 风险点(诚实标注):** cyq_perf 下载量大(300只×6年)可能受 tushare 频控——Task 2 若卡,分批+重试。CSI300 universe 用 pythonnet 调 barra loader——若 pythonnet 环境问题,fallback 预生成成分股 csv(Task 3 注释)。IC 报告可能 IC≈0(拥挤度无预测力)——诚实报,不 spin。

**5. spec 修正:** spec §1.1 说"FactorRegistry 加一行 Register",实际已注册(`FactorRegistry.cs:42`)。Task 1 改为"验证已注册"。spec 文件不回改(计划是实施依据,已在此修正)。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-22-crowding-factor.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review.

**2. Inline Execution** — batch with checkpoints.

Which approach?
