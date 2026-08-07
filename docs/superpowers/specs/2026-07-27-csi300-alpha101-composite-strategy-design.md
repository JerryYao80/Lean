# CSI300 Alpha101 复合策略 — 走通量化策略开发环路

> **日期**: 2026-07-27
> **分支**: `feat/csi300-alpha101-composite` (off `feat/alpha101-factor-zoo`)
> **前置**: Alpha101 因子生产链路已完成（101 alpha + factor_worker supervisor LIVE + alpha101 fresh@20260724 + C# 读路径已接通 `FactorStore.Get("alphaNNN")` + 101 内涵说明进 catalog + hypothesize 提示注入）。本 spec 用这些因子为 CSI300 设计一条多 alpha 复合策略，并把"构建→策略→优化→回测→复盘→重构"6 阶段环路全部走通。
> **决策依据**: 5 代理并行深度验证（8 alpha 精选 / FactorStore C# 消费 / Bayes 优化接入 / 复盘 adapter ABC / 重构闭环），全部 spot-verified 对源码。

## 1. 目标

用 8 个跨族的 alpha101 因子，为 CSI300 宇宙设计一条**月度再平衡的多 alpha 复合做多策略**，作为：

1. **alpha101 拉取路径的首个真实消费者** —— 现有 A 股算法全用旧的 `FactorRegistry` 推送 API，FactorStore 拉 API 零算法消费。本策略是第一个经 `FactorStore.Get("alphaNNN")` 单点拉取因子的算法，验证拉取路径端到端可用。
2. **量化策略开发环路的首条 alpha101 走通实例** —— 6 阶段（构建/策略/优化/回测/复盘/重构）全部接通现有管线，不留断点。

**核心决策（brainstorming 已确认）**:
- **宇宙**: CSI300-only（CSI500 `000905.SH` 成分股数据在 `index_weight` 缺失，真 CSI800 不可用；退化为 CSI300 的 300 只先走通环路）。
- **因子**: 手选 8 个跨 8 族的 alpha（不 LLM 选、不全 101），回填成本可控、layer 归因清晰。
- **回填**: 2024-01-02 → 2026-06-29（与价格数据对齐；7 月价格数据缺所以卡 6-29），~2.5 年。
- **优化**: 接现有 `bayesian_optimizer.py` Track A，优化 8 个 alpha 权重 + zscore 阈值 + 再平衡天数。
- **复盘**: 新建 `csi300_alpha101` review adapter，per-alpha proportional-z 归因（和=总 PnL 严格成立）。
- **重构**: 接通 `hypothesize.py` + `on_gate3_pass.py` 闭环（hypothesize/on_gate3_pass 已 strategy-agnostic，只需 manifest 三块 + 自家 adapter）。
- **构建**: hypothesize 读 alpha101 catalog（含 intent/direction/family）生成设计假设 `.md`（走通"构建"= LLM 读 catalog 提假设）；代码 C# 手写（确定性、接现有 A 股模板、LEAN-native）。
- **零改既有功能**: `FactorStore`/`RParquetAdapter`/`bayesian_optimizer`/`reward`/`hypothesize`/`on_gate3_pass`/`gold2` 全部不动。

## 2. 八个 Alpha 精选与复合信号

### 2.1 选定 8 alpha（跨 8 族，均已验证 2026-07-24 parquet 存在）

| # | id | 族 | 方向 | 公式摘要（docs/101.md） | sign | 信号类型 |
|---|---|---|---|---|---|---|
| 1 | alpha001 | 极值反转 | 反向 | `rank(Ts_ArgMax(SignedPower((returns<0)?stddev(returns,20):close,2.),5))-0.5` | -1 | 反转 |
| 2 | alpha006 | 量价反转 | 反向 | `-1*correlation(open,volume,10)` | -1 | 量价反转 |
| 3 | alpha030 | 趋势条件 | 正向 | `(1-rank(sign(Δc1)+sign(Δc2)+sign(Δc3)))*sum(volume,5)/sum(volume,20)` | +1 | 动量/趋势 |
| 4 | alpha040 | 波动率 | 反向 | `(-1*rank(stddev(high,10)))*correlation(high,volume,10)` | -1 | 波动率 |
| 5 | alpha042 | VWAP反转 | 反向 | `rank(vwap-close)/rank(vwap+close)` | -1 | VWAP 均值回归(delay-0) |
| 6 | alpha055 | 量价相关 | 反向 | `-1*correlation(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))),rank(volume),6)` | -1 | 量价相关 |
| 7 | alpha058 | 行业中性 | 反向 | `-1*Ts_Rank(decay_linear(correlation(IndNeutralize(vwap,IndClass.sector),volume,3.93),7.89),5.50)` | -1 | 行业中性 VWAP-量 |
| 8 | alpha101 | 开收结构 | 正向 | `(close-open)/((high-low)+0.001)` | +1 | 日内动量(delay-1) |

**族覆盖**: 8/13（极值反转/量价反转/趋势条件/波动率/VWAP反转/量价相关/行业中性/开收结构）。
**方向分布**: 正向 2（alpha030, alpha101）/ 反向 6 / 中性 0。
**信号平衡**: 反转 5 + 动量 2 + 波动率 1；delay-0 1 + delay-1 7。
**alpha058 覆盖**: 用 `index_member_all` SW 行业数据（可用），2026-07-24 覆盖 218/300，缺的标 Missing 跳过（非阻塞）。

### 2.2 复合信号公式（CSI300 截面，月度）

```
对每个交易日 t、CSI300 成员 i、8 alpha k:
  z_k(i,t) = 截面 z-score(alpha_k 原值, 跳过 Quality==Missing)
  sign_k = +1 if 方向==正向 else -1   (反向翻 sign, 统一"高 composite→做多")
  composite(i,t) = Σ_k [ w_k × sign_k × z_k(i,t) ]    # w_k baseline=1/8, Bayes 学
做多 composite 排名前 10%（~30 只），等权
```

- 截面 z-score 先于混合，防止单 alpha 尺度主导（alpha058 ±1 饱和 vs alpha101 宽域拉平）。
- 反向 alpha 翻 sign 后"高 composite → 预期涨 → 做多"统一。
- 等权 (1/8) 是 baseline，优化阶段让 Bayes 学 8 个权重 `w_alphaNNN ∈ [0,1]`。

## 3. 组件清单（新增文件，零改既有功能）

| # | 文件 | 操作 | 职责 |
|---|---|---|---|
| 1 | `Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs` | 新建 | 五层算法主体（Initialize 装配 + GetParameter 读权重）|
| 2 | `Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs` | 新建 | **首个 FactorStore 消费者**：拉 8 alpha + z-score + 复合 + top10% Insight |
| 3 | `Launcher/config/config-csi300-alpha101-composite.json` | 新建 | LEAN 回测 config |
| 4 | `Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml` | 新建 | Bayes 优化 manifest（8 权重 + zscore-threshold + rebalance-days + review/feedback/inspiration 三块）|
| 5 | `Scripts/review/adapters/csi300_alpha101.py` | 新建 | 复盘 adapter（proportional-z 每 alpha 归因，和=总 PnL）|
| 6 | `Scripts/feedback/adapters/csi300_alpha101.py` | 新建 | 反馈 adapter（低贡献 alpha 触发 hypothesize，不碰 gold2）|
| 7 | `Scripts/factor_zoo/backfill_alpha101_csi300.py` | 新建 | 历史回填脚本（2024-01→2026-06，CSI300 宇宙，8 alpha）|

**复用既有组件（不新建、不修改）**:
- `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs` —— 已是 CSI300-only（`indexCode="000300.SH"`）+ 月刷新 + pythonnet loader 路径，完全匹配需求，直接 `SetUniverseSelection(new AShareCSI300UniverseSelectionModel(...))` 装配。
- `Common/Orders/Fees/AShareStockFeeModel.cs` / `AShareStockFillModel.cs` / `AShareStockBuyingPowerModel.cs` / `DelayedSettlementModel.cs` —— A 股本地化模型，经 `AShareStockSecurityInitializer` 装配（同 `AShareCrowdingFactorZooStrategy` 模板）。
- `Algorithm.CSharp/Models/Risk/MaxDrawdownRiskModel.cs` —— 20% 回撤风控，复用。
- `Algorithm.CSharp/Models/Portfolio/EqualWeightPortfolioModel.cs`（或 LEAN 原生 `EqualWeightingPortfolioConstructionModel`）—— 等权 PCM，复用。

**零改动既有文件**（已验证）:
- `Common/Factors/Store/FactorStore.cs` / `RParquetAdapter.cs` / `FactorStoreConfig.cs` / `Alpha101FactorRegistration.cs`
- `Scripts/auto_optimize/bayesian_optimizer.py` / `reward.py` / `lean_runner.py` / `manifest_loader.py`
- `Scripts/inspiration/hypothesize.py` / `on_gate3_pass.py` / `provenance.py` / `layer_state.py`
- `Scripts/review/adapters/gold2.py` / `Scripts/feedback/adapters/gold2.py`
- `Scripts/review/cli.py` / `base.py` / `artifacts.py`

`hypothesize.run` 与 `on_gate3_pass.on_pass` 已是 strategy-agnostic（读 `manifest_raw["inspiration"]` + catalog，无 gold2 硬编码），纯 config + 新 adapter 接入即可。

## 4. 架构与数据流

```
        ┌─ Stage 1 构建 ─────────────────────────────────────────┐
        │  hypothesize.py 读 alpha101 catalog(含 intent/         │
        │  direction/family) → 生成《CSI300 多alpha复合策略       │
        │  设计假设.md》→ Results/soloquant/local-strategies/      │
        │  （走通"构建"=LLM 读 catalog 提假设；代码 C# 手写）      │
        └──────────────────────────┬─────────────────────────────┘
                                   ▼
        ┌─ Stage 2 策略 ─────────────────────────────────────────┐
        │  C# 五层：                                              │
        │  CSI300Universe(月) → Alpha101CompositeAlphaModel       │
        │    (FactorStore.Get 拉 8 alpha/股/月, z-score, 复合,     │
        │     top10% Insight, Quality==Missing 跳过)              │
        │  → EqualWeightPCM(TimeSpan 月) → MaxDrawdownRiskModel   │
        │    (0.20) → AShareLotSizeExecutionModel                 │
        │  ← GetParameter("w_alphaNNN",0.125) 读 Bayes 注入权重   │
        │  （首个真实 FactorStore 消费者，验证拉取路径）           │
        └──────────────────────────┬─────────────────────────────┘
                                   ▼
        ┌─ Stage 3 优化 ─────────────────────────────────────────┐
        │  bayesian_optimizer.py 读 manifest.yaml                 │
        │  → TPE 采样 8 权重+zscore-threshold+rebalance-days     │
        │    (200 trial, layer=L2_Alpha/L3_Portfolio 元数据)      │
        │  → lean_runner 注入 parameters(str) → dotnet Launcher   │
        │  → reward.py 读 Results/...-summary.json "Sharpe Ratio"│
        │  → DSR gate 自动跑 (N=200, baseline=全 trial 均值)      │
        └──────────────────────────┬─────────────────────────────┘
                                   ▼
        ┌─ Stage 4 回测 ─────────────────────────────────────────┐
        │  dotnet Launcher.dll --config config-csi300-alpha101   │
        │  → Results/Csi300Alpha101CompositeStrategy-summary.json│
        │    + -order-events.json                                 │
        └──────────────────────────┬─────────────────────────────┘
                                   ▼
        ┌─ Stage 5 复盘 ─────────────────────────────────────────┐
        │  review/cli.py → adapters/csi300_alpha101.py            │
        │  per-alpha 归因:                                         │
        │    ρ_i = (w_i·z_i) / Σ_j(w_j·z_j)   (signed)           │
        │    C_i = ρ_i · trade.profit_loss                        │
        │    Σ C_i = P  (严格成立)                                 │
        │  → review.json (8 alpha 各自 pnl_pct_of_total)          │
        └──────────────────────────┬─────────────────────────────┘
                                   ▼
        ┌─ Stage 6 重构 ─────────────────────────────────────────┐
        │  若 alpha#X 贡献为负持续 N 代 →                          │
        │    hypothesize(strategy, "alpha_X", review, ...)        │
        │    LLM 读 catalog → 提替代 alpha#Y → .md                 │
        │    → 重新进构建→策略→... (ingest_local_strategies 1h)   │
        │  on_gate3_pass 验证 alpha_X 层 pnl_pct_of_total 改善    │
        │    → 标 redesigned, 退 alpha_X_contrib_penalty          │
        │  【关键契约】新策略 review 必须把替代 alpha 的 PnL 记在  │
        │   同一 inspired_layer 名下（否则 verify 假阴性）         │
        └──────────────────────────┘ 回到 Stage 1（闭环）
```

### 4.1 回填数据流（Stage 0，环路前置）

```
Scripts/factor_zoo/backfill_alpha101_csi300.py
  → 逐交易日(2024-01-02..2026-06-29) 调 alpha101.builder.build_day(date, csi300_ts_codes)
  → result/factor-zoo/alpha{001,006,030,040,042,055,058,101}/<date>/*.parquet
  CSI300 成分经 panel_loader.load_csi800_universe (CSI300-only 退化) 或直接 load_index_constituents("000300.SH")
```

### 4.2 GIL 成本

300 股 × 8 alpha × ~30 月度 = 72000 次 `FactorStore.Get` × ~3ms（1 GIL + 1 parquet read）≈ **3.6 分钟**全回测，可接受。Phase 1 直接用 `FactorStore.Get`，不引 `BatchedFactorStore`（YAGNI，Phase 2 视 profiling）。

## 5. 关键正确性点（已对源码验证）

### 5.1 FactorStore 构造与消费

- **构造一次**：`new FactorStore()` 在 `Alpha101CompositeAlphaModel` ctor（私有 readonly 字段）。ctor 自动调 `FactorStoreConfig.RegisterDefaults`（注册 122 adapter），读路径不可变（`Get` 只读，`_adapters` 在 lock 下快照），跨整个回测安全。不 per-Update 构造（会 30 次重注册 122 adapter，无意义）。
- **Missing 不抛**：`RParquetAdapter.TryGet` 文件不存在/空/NaN → 返 false → `FactorStore.Get` 返 `Quality=Missing, Value=0m`。AlphaModel 跳过该 alpha 该股；某 alpha 当日 <10 只有效 → 跳该 alpha；全部空 → hold 不崩。
- **路径解析**：`{resultRoot}/factor-zoo/alphaNNN/<date:yyyy-MM-dd>/<ts_code>.parquet`，与 builder 输出一致（`Alpha101FactorRegistration.cs:26` + `RParquetAdapter.cs:48-50`）。`SymbolToTsCode`：6/51 前缀→.SH else .SZ。

### 5.2 日期对齐（RParquetAdapter 不 forward-fill）

- **不 forward-fill**：`RParquetAdapter.cs:48-50` 用 `date.ToString("yyyy-MM-dd")` 精确路径，缺即 Missing。对比 `RBarraAdapter.cs:60` 的 `string.CompareOrdinal(d, want) <= 0` 才 forward-fill。
- **回溯**：月度再平衡当天若非交易日/未回填 → `ResolveFactorDate` 用 `SecurityExchangeHours.GetPreviousTradingDay` 回溯 ≤7 天（覆盖春节/国庆长假）。
- **区分"整日缺"vs"个股 Missing"**：`FactorStore.Get` 对两者都返 Missing（不可区分）。`ResolveFactorDate` 用 `DateFolderExists(alphaId, date)` 直接探 `result/factor-zoo/alphaNNN/<date>/` 目录：目录在 = 该日已回填（个股 Missing 是真 skip）；目录不在 = 回溯上一交易日。

### 5.3 权重注入与读取

- **注入**：`lean_runner._inject_params_into_config`（`lean_runner.py:30-33`）把参数 `str(v)` 合进 config `parameters` dict。
- **读取**：C# `GetParameter("w_alphaNNN", 0.125)` 的 **double 重载**（`QCAlgorithm.cs:890`，`CultureInfo.InvariantCulture` 解析），匹配 `str(v)` 强制。
- **归一化**：C# 内 `w_k/Σw`（全 0 退等权 1/8，避免 0 交易 → `reward.py:48-51` 返 -inf 让 GP 无信号）。不在 manifest 约束 sum=1（保留 TPE 探索 simplex 角点的样本效率）。

### 5.4 复盘归因和不变量

- **proportional-z**：`ρ_i = (w_i·z_i,t_e,s) / Σ_j(w_j·z_j,t_e,s)`（signed 分母，保留反向 alpha 负贡献），`C_i = ρ_i · trade.profit_loss`。
- **和不变量**：`Σ_i C_i = (Σ_i ρ_i)·P = 1·P = P = trade.profit_loss`（构造上严格成立）。
- **Decimal 漂移**：四舍五入 sub-ulp 漂移吸收到最大幅 alpha，`validated_layer_attribution`（`base.py:84-88`）不 raise。
- **残差路径**（无 state_trace / 零分母 / 部分 z 缺）：等分 `C_i = P/8`（最大熵先验，和仍=P），标 `attribution_method` 区分。
- **state_trace 契约**：`entry_bar`（`ts <= entry_time`，无前瞻）须带 `alpha_zscores`{alpha_id:float} + `alpha_weights`{alpha_id:float}（可选，默认 1/8）+ `tpv` + `composite_z`（可选）。

### 5.5 重构闭环契约

- **hypothesize.run strategy-agnostic**：读 `manifest_raw["inspiration"]["persistence"]["gap_threshold"]` + catalog，无 gold2 硬编码（`hypothesize.py:124-140`）。只需 manifest `inspiration:` 块。
- **on_gate3_pass.on_pass strategy-agnostic**：读新 manifest `provenance` 块，非 `review_inspiration` 来源 early-exit no-op（`on_gate3_pass.py:22-28`）。
- **shaping_term 命名**：`<layer>_contrib_penalty` 硬编码于 `hypothesize.build_prompt:57,61` + `on_gate3_pass.py:51` + `trigger.py:34-37`。manifest `feedback.shaping_term_map` 须遵循。
- **【最关键契约】**：新（替代）策略的 review.json 必须把替代 alpha 的 `pnl_pct_of_total` 记在**同一 `inspired_layer` 名**下（如父策略 alpha_X 失败 → 子策略的替代 alpha_Y 贡献也要记在 `alpha_X` 键下），否则 `provenance.verify_layer_improvement`（`provenance.py:31-33`）查 `inspired_layer` 返 0 → 假阴性"未改善"。

## 6. 测试计划

### 6.1 C# 侧

**`Tests/Common/Algorithm/Models/Alpha/Alpha101CompositeAlphaModelTests.cs`（新建）**:
- `Update_WithFakeReader_ProducesTop10PercentInsights`：注入 fake reader 返 8 alpha 值，验 z-score + sign + top10% Insight 数量。
- `Update_SkipsMissingAlpha`：某 alpha 全 Missing → 跳该 alpha，复合仍用其余。
- `Update_AllMissing_HoldsNotThrows`：全部 Missing → 返空 Insight，不崩。
- `Update_DateFallback_WalksBackToPreviousTradeDay`：当日无 parquet → 回溯到上一交易日。
- `ResolveFactorDate_DistinguishesDateMissingVsSymbolMissing`：目录探针区分。
- `[Explicit] Update_RealDisk_20260724_Smoke`：真实磁盘 1 日，验 alpha042≈已知值。

**`Tests/Common/Algorithm/AShareCSI300Alpha101CompositeStrategyTests.cs`（新建）**:
- `Initialize_ReadsAlphaWeightsFromParameters`：注入 parameters，验 8 权重读取 + 归一化。
- `Initialize_AllZeroWeights_FallsBackToEqualWeight`：全 0 → 等权 1/8。
- `FiveLayers_Assembled`：验 Universe/Alpha/PCM/Risk/Exec 五层装配。

### 6.2 Python 回填

**`Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py`（新建）**:
- `backfill_calls_builder_per_trade_day`：mock builder.build_day，验每日调用 + CSI300 ts_codes 传入。
- `backfill_writes_8_alpha_parquets`：验 8 alpha 目录各生成 parquet。
- `backfill_skips_non_trade_days`：验 trade_cal 过滤。

### 6.3 Python 优化

**`Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py`（新建）**:
- `manifest_parses_8_weight_params`：8 个 w_alphaNNN ParamSpec 解析为 float [0,1]。
- `lean_runner_injects_weights_into_config`：mock lean_runner，验 parameters 注入。
- `reward_parses_sharpe_ratio`：mock summary.json，验 Sharpe 解析。

### 6.4 Python 复盘

**`Tests/Python/test_review_csi300_alpha101_attribution.py`（新建）**:
- `proportional_z_sums_to_profit_loss`：8 alpha 归因和 = trade.profit_loss（Decimal 精确）。
- `residual_equal_split_when_no_state_trace`：无 entry_bar → 等分。
- `drift_absorbed_to_largest_alpha`：四舍五入漂移吸收。
- `layers_keys_match_LAYERS`：keys == set(LAYERS)。
- `negative_alpha_gets_negative_share`：反向投票 alpha 得负 ρ。

### 6.5 Python 反馈

**`Tests/Python/test_feedback_csi300_alpha101_trigger.py`（新建）**:
- `low_contribution_alpha_triggers_hypothesize`：mock review，某 alpha pnl_pct < 阈值持续 N 代 → 触发 hypothesize mock。
- `optimizing_layer_skipped`：status==optimizing 的层不触发。

### 6.6 端到端

**`[Explicit]` 手动**：跑 2024-Q1 三月真实回测 + 复盘 + 验 review.json 8 alpha 归因非空 + 跑一轮 Bayes（减 trial 到 5）验 manifest/reward/DSR 通路。

## 7. 阶段结果可视化（让优化/重构成果直观可见）

把每一轮回测的核心指标 + 每 alpha 归因按"阶段"落库，前端用独立 Grafana dashboard 可视化，让 baseline → optimize 各代 → refactor 各代的 Sharpe/回撤/收益曲线 + 每 alpha 贡献演化一目了然。同时落一份本地 CSV 供脚本/人读。

### 7.1 阶段定义与标识

每次回测产物（summary.json + review.json + manifest）归属一个阶段，由 manifest 的 `stage_meta` 块声明（Bayes optimizer 每次跑前注入 / refactor 候选生成时由 hypothesize 流程注入）：

| stage_type | 含义 | 何时产生 | variant_id |
|---|---|---|---|
| `baseline` | 等权 1/8 初始基线 | Stage 2 完成后首次回测 | `baseline` |
| `optimize` | Bayes 优化某 trial | Stage 3 每次 `lean_runner.run_backtest` | `opt_gen{G}_trial{T}` |
| `optimize_champion` | Bayes 最优 trial（DSR gate 后）| Stage 3 `optimize()` 收尾 | `opt_champion_gen{G}` |
| `refactor` | hypothesize 替换某 alpha 后的候选 | Stage 6 新变体回测 | `refactor_{inspired_layer}_gen{G}` |

`generation` = 优化代数（`generations.read_history` 计数），`strategy_id` = `csi300_alpha101_composite`。

### 7.2 InfluxDB measurements（新建，独立，不污染既有）

**measurement 1: `csi300_alpha101_stage`**（每阶段 1 行点，汇总指标）

| 字段 | 类型 | 来源 |
|---|---|---|
| tags: `strategy_id`, `stage_type`, `variant_id`, `generation` | string | manifest stage_meta |
| field: `sharpe` | float | summary.json "Sharpe Ratio" |
| field: `sortino` | float | "Sortino Ratio" |
| field: `drawdown` | float | "Drawdown" |
| field: `net_profit` | float | "Net Profit" |
| field: `compounding_annual_return` | float | "Compounding Annual Return" |
| field: `total_orders` | int | "Total Orders" |
| field: `win_rate` | float | "Win Rate" |
| field: `alpha_weight_<NNN>` | float ×8 | manifest parameters w_alphaNNN（归一化后）|
| field: `dsr_passed` | int (0/1) | DSR gate（仅 optimize_champion）|
| timestamp | ns | 回测完成时间 |

**measurement 2: `csi300_alpha101_alpha_attribution`**（每阶段 ×8 alpha = 8 行点）

| 字段 | 类型 | 来源 |
|---|---|---|
| tags: `strategy_id`, `alpha_id` (alpha_001..alpha_101), `stage_type`, `variant_id`, `generation` | string | review.json layer_attribution |
| field: `pnl_pct_of_total` | float | review.json layer_attribution[alpha_id].pnl_pct_of_total |
| field: `pnl_abs` | float | .pnl_abs |
| field: `n_trades` | int | .n_trades |
| field: `n_wins` | int | .n_wins |
| field: `weight` | float | 该阶段该 alpha 的归一化权重 |
| timestamp | ns | 回测完成时间 |

### 7.3 CSV（本地快照，CSV 是数据源 / dashboard 是可视化）

`Results/csi300_alpha101/stage_results.csv`（每阶段追加 1 行，扁平）：

```
stage_type,generation,variant_id,timestamp,sharpe,sortino,drawdown,net_profit,
total_orders,win_rate,dsr_passed,
alpha_001_contrib_pct,alpha_006_contrib_pct,alpha_030_contrib_pct,alpha_040_contrib_pct,
alpha_042_contrib_pct,alpha_055_contrib_pct,alpha_058_contrib_pct,alpha_101_contrib_pct,
w_alpha001,w_alpha006,w_alpha030,w_alpha040,w_alpha042,w_alpha055,w_alpha058,w_alpha101
```

### 7.4 Grafana dashboard（新建，独立）

`monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json`，数据源 InfluxDB（InfluxQL 模式，符合 [[grafana-influxql-fix]]）。面板：

1. **阶段 Sharpe 演化**（折线，按 stage_type 着色）—— baseline → 各 optimize trial → champion → refactor，直观看到优化爬坡 + 重构跃迁。
2. **阶段回撤 / 净收益**（折线双轴）。
3. **每 alpha 贡献热力图**（8 alpha × stage 矩阵，`pnl_pct_of_total` 着色）—— 哪个 alpha 持续拖后腿 → 触发 refactor 的依据。
4. **8 alpha 权重演化**（堆叠面积，optimize 各 trial 的 `w_alphaNNN`）—— 看 Bayes 学到的权重轨迹。
5. **DSR gate 状态**（状态面板，optimize_champion 行的 `dsr_passed`）。

### 7.5 Bridge 脚本（新建，独立）

`Scripts/csi300_alpha101/stage_bridge.py`：
- 输入：`--summary <path>` `--review <path>` `--manifest <path>`，从 manifest 读 `stage_meta`（stage_type/generation/variant_id）。
- 复用 `export_backtest_results_to_influx.py` 的 `parse_numeric_value` / `metric_key` / `InfluxPoint`（import，不改）。
- 写 2 个 InfluxDB measurement + 追加 CSV。
- `INFLUXDB_TOKEN="admin-token-leansystem"` org=lean bucket=quant（[[influxdb-token]]）。
- 不向任何既有 measurement 写（`lean_backtest_stat`/`soloquant_pipeline_funnel`/`barra_*` 全不动，[[never-modify-existing-features]]）。

### 7.6 接入点

- **Stage 2 baseline 回测后**：手动/脚本调 `stage_bridge.py`。
- **Stage 3 Bayes 每次 `lean_runner.run_backtest` 后**：manifest 标 `stage_type=optimize`，bridge 读 trial 结果。`bayesian_optimizer.py` 不改——在 manifest 的 `reward_config` 外侧用轻量 wrapper（新文件 `Scripts/auto_optimize/strategies/csi300_alpha101_composite/run_with_bridge.py`）包一层：调 `bayesian_optimizer.optimize` 前后 + 每 N trial 调 bridge。
- **Stage 6 refactor 候选回测后**：manifest 标 `stage_type=refactor`，bridge 落库。

## 8. 非目标

- 不下载 CSI500（退 CSI300-only，已确认）。
- 不改 `FactorStore`/`RParquetAdapter`/`bayesian_optimizer`/`reward`/`hypothesize`/`on_gate3_pass`/`gold2` 任何既有文件。
- 不引 `BatchedFactorStore`（Phase 2 视 profiling）。
- 不接 InfluxDB 读路径（parquet 直读够；InfluxDB 是监控用）。
- 不做 live-paper（本任务只走通回测+复盘环路；live-paper 单独 spec）。
- 不动 SoloQuant 管道 measurement/dashboard（独立文件，绝不污染 `soloquant_pipeline_funnel`）。
- 不做 alpha101 全 101 回填（只回填手选 8 个，控成本）。
- 不 LLM 生成策略代码（C# 手写，确定性 + 接现有模板）。
- 不向既有 InfluxDB measurement 写（`csi300_alpha101_*` 全新独立 measurement）。

## 9. 验收标准

1. `dotnet build QuantConnect.Lean.sln` 通过。
2. C# 测试全绿（`Alpha101CompositeAlphaModelTests` + `AShareCSI300Alpha101CompositeStrategyTests`）。
3. Python 测试全绿（回填 + manifest + 复盘归因 + 反馈触发 + stage_bridge 落库）。
4. 回填后 `result/factor-zoo/alpha{001,006,030,040,042,055,058,101}/` 各有 2024-01-02..2026-06-29 的交易日目录。
5. `dotnet Launcher.dll --config config-csi300-alpha101-composite.json` 跑通 2024-Q1 三月回测，生成 summary.json + order-events.json。
6. `review/cli.py` 生成 review.json，8 alpha 各有非空 `pnl_pct_of_total`，和≈1。
7. `bayesian_optimizer.py` 读 manifest 跑通（减 trial smoke），DSR gate 返回 `passed` bool。
8. hypothesize mock 验证：低贡献 alpha → 生成 `.md` 假设文件（走通构建+重构衔接）。
9. 真实磁盘 `[Explicit]` smoke：`store.Get("alpha042", 603799.SH, 2026-07-24) ≈ 1.06993`。
10. **可视化**：`stage_bridge.py` 落库后，`csi300_alpha101_stage` measurement 有 ≥3 行点（baseline + ≥1 optimize + ≥1 refactor），CSV 同步追加；Grafana `csi300-alpha101-loop` dashboard 打开能见阶段 Sharpe 折线 + 每 alpha 贡献热力图。

## 10. 风险与对策

- **风险**: 8 alpha 权重 Bayes 优化 200 trial × 3.6min/回测 = 12 小时。
  **对策**: smoke 阶段减 trial 到 5-10 验通路；正式跑后台 + `--no-build`；manifest `rebalance-days` 也可优化降频。
- **风险**: alpha058 行业覆盖 218/300，部分股 Missing。
  **对策**: Missing 跳过（非阻塞）；z-score 在有效子集上算；若某日 <10 只有效跳该 alpha。
- **风险**: proportional-z 归因在 alpha 投票互相抵消（零分母）时不稳定。
  **对策**: 零分母 → 等分残差路径，标 `attribution_method=proportional-z-zero-blend`。
- **风险**: 重构闭环依赖外部 create-strategy skill 调 `on_gate3_pass.py` CLI（repo 内无 production caller）。
  **对策**: spec 明示这是已知接线缺口；本任务用 hypothesize mock 验"低贡献 alpha → .md 生成"衔接，on_gate3_pass 全链路留作手动验证（不阻塞环路走通）。
- **风险**: 价格数据止于 2026-06-29，alpha parquet 有 7-23/24，两者不对齐。
  **对策**: 回测区间卡 2024-01-02..2026-06-29（与价格对齐）；7 月 alpha parquet 留作 live-paper 单独 spec。
- **风险**: 现有 `Get_RoutesBarraAndParquetAndRegistryByPrefix` 测试因 barra CSV 数据增长而失败（预存在，非本任务引入）。
  **对策**: 不在本任务修；如阻塞 CI，单独处理（改断言或换日期）。
- **风险**: bridge 每次回测后写 InfluxDB 增加 Stage 3 墙钟开销。
  **对策**: bridge 写库 ~0.5s/次（单点 HTTP），200 trial 增 ~100s，相对 12h 回测可忽略；失败不阻塞回测（try/except + 日志）。
- **风险**: dashboard 面板查询空 measurement（首次跑前无数据）显示空图。
  **对策**: dashboard 配置 `< 1 hour` 默认时间窗 + 空状态提示；验收标准 10 要求 ≥3 行点后 dashboard 非空。

## 10. 文件清单总览

| 文件 | 操作 | 阶段 |
|---|---|---|
| `Scripts/factor_zoo/backfill_alpha101_csi300.py` | 新建 | Stage 0 回填 |
| `Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py` | 新建 | Stage 0 测试 |
| `Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs` | 新建 | Stage 2 策略 |
| `Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs` | 新建 | Stage 2 策略 |
| `Launcher/config/config-csi300-alpha101-composite.json` | 新建 | Stage 4 回测 |
| `Tests/Common/Algorithm/Models/Alpha/Alpha101CompositeAlphaModelTests.cs` | 新建 | Stage 2 测试 |
| `Tests/Common/Algorithm/AShareCSI300Alpha101CompositeStrategyTests.cs` | 新建 | Stage 2 测试 |
| `Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml` | 新建 | Stage 3 优化 |
| `Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py` | 新建 | Stage 3 测试 |
| `Scripts/review/adapters/csi300_alpha101.py` | 新建 | Stage 5 复盘 |
| `Tests/Python/test_review_csi300_alpha101_attribution.py` | 新建 | Stage 5 测试 |
| `Scripts/feedback/adapters/csi300_alpha101.py` | 新建 | Stage 6 重构 |
| `Tests/Python/test_feedback_csi300_alpha101_trigger.py` | 新建 | Stage 6 测试 |
| `Scripts/csi300_alpha101/stage_bridge.py` | 新建 | §7 阶段结果落 InfluxDB + CSV |
| `Scripts/auto_optimize/strategies/csi300_alpha101_composite/run_with_bridge.py` | 新建 | §7 Bayes wrapper（每 N trial 调 bridge）|
| `monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json` | 新建 | §7 可视化 dashboard |
| `Tests/Python/test_csi300_alpha101_stage_bridge.py` | 新建 | §7 测试（落库 + CSV + 不污染既有 measurement）|

**复用既有组件（不新建、不修改）**:
- `Algorithm.CSharp/Universe/AShareCSI300UniverseSelectionModel.cs` (CSI300-only，月刷新，pythonnet loader)
- `Common/Orders/Fees/AShareStockFeeModel.cs` / `AShareStockFillModel.cs` / `AShareStockBuyingPowerModel.cs` / `DelayedSettlementModel.cs`
- `Algorithm.CSharp/Models/Risk/MaxDrawdownRiskModel.cs`
- `Algorithm.CSharp/Models/Portfolio/EqualWeightPortfolioModel.cs`（或 LEAN 原生 `EqualWeightingPortfolioConstructionModel`）
- `Scripts/export_backtest_results_to_influx.py` 的 `parse_numeric_value` / `metric_key` / `InfluxPoint`（import 复用，不改）

**新增可视化/落库文件（§7，全独立）**:
- `Scripts/csi300_alpha101/stage_bridge.py`（落 2 个新 measurement + CSV）
- `Scripts/auto_optimize/strategies/csi300_alpha101_composite/run_with_bridge.py`（Bayes wrapper）
- `monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json`（独立 dashboard）
