# Gold2 闭环实验修复后最终诚实审计 (docs/check-gold2-dtls2.md)

> 用户的七问（逐字引用，作为本审计的索引）：
>
> 1. 有没有带来量化收益上的改变
> 2. 初始量化策略是什么
> 3. 参数是如何优化的
> 4. 最初的回测结果如何
> 5. 复盘发现了什么有价值信息
> 6. 重构的新量化是如何的
> 7. 期间如何使用大模型帮助新量化策略构建

**审计方法**：在 worktree 根 `/home/project/hope/Lean/.claude/worktrees/gold2-closed-loop-proof` 上，针对修复后的 P2/P3/P4 证据树部署 4 个并行 reader（P2 闭环/策略与机制/P3-P4 盲测/规格与诚实限制），每个论断必须附 `file:line` 证据引用。冻结证据目录 `result/gold2-p2-construction/`、`result/gold2-p3-construction/`、`result/gold2-p4-construction/`，规格 `docs/superpowers/specs/2026-07-14-gold2-closed-loop-economic-proof-design.md` 与 `docs/superpowers/specs/2026-07-20-gold2-closed-loop-fix-design.md`。审计日期 2026-07-21。

---

## 1. 有没有带来量化收益上的改变

**坦率结论：闭环产出了一个真实可评价的重构（G2 shaping），但它没有带来量化收益上的改善——OOS Sharpe 在 4/4 窗口上稳定下降，aggregate_delta = -2.7822，裁决 NOT_EFFECTIVE/REFUTED。这是数据结论，不是 bug-shortcut null。**

修复后的 P4 在真实 518880 数据上跑了真实 LEAN `dotnet Launcher` 盲年回测（W1=2022、W2=2023、W3=2024、W4=2025）。逐窗口配对 ΔSharpe = final_blind.sharpe − g0_blind.sharpe：

| 窗口 | G0 blind Sharpe | G2 blind Sharpe | ΔSharpe |
|---|---|---|---|
| W1 (2022) | -0.2288 | -1.5597 | -1.3309 |
| W2 (2023) | -0.037 | -0.5256 | -0.4886 |
| W3 (2024) | 0.6578 | 0.2011 | -0.4567 |
| W4 (2025) | 1.6589 | 1.1529 | -0.5060 |

`aggregate_delta = -2.7822`，`paired_direction = {positive: 0, negative: 4, majority_positive: false}` —— 4/4 窗口全部恶化，无一例外 (result/gold2-p4-construction/p4_report.json:99-163)。簇块移动 bootstrap：`mean_estimate = -0.000696, p_value = 0.0, ci_low = -0.001120, ci_high = -0.000469, effective_n = 4`，CI 整体位于零以下 (result/gold2-p4-construction/p4_report.json:bootstrap block)。`verdict = NOT_EFFECTIVE`，`disposition = REFUTED`，`verdict_reason = "aggregate loop delta non-positive"` (result/gold2-p4-construction/p4_report.json:verdict block)。

**关键边界**：STOP P4 GATE **通过**（`stop_p4_verdict = PASS`，`verify_seal = true`），但 GATE 的判定标准是冻结/盲测/封存机件完整、封存验证通过，**不是**裁决为 EFFECTIVE。`construct_p4.py` docstring 第 18-23 行明示："The economic verdict itself may be NOT_EFFECTIVE/REFUTED or NOT_EVALUATED/UNPROVEN — both are valid §14 outcomes; the GATE is that the freeze/blind/seal machinery is intact and the seal verifies, NOT that the verdict is EFFECTIVE." (Scripts/gold2_closed_loop/construct_p4.py:18-23)。

**修复的"成功"在于**：闭环从修复前的 bug-shortcut null（16 次 G1 试验全 FAILED_STRATEGY、P4 复用训练期 g0_metrics 当盲测，aggregate_delta = -0.0812，"null"来自 run_id 含斜杠 bug + 样本内复用）升级为修复后的真实可评价证伪。null 从 bug 短路升级为数据证伪。详见 §8。

---

## 2. 初始量化策略是什么

G0 是 `Gold2BetaVolTargetStrategy`（在证明子类化为 `Gold2ClosedLoopProofStrategy`），一个 5 层 QCAlgorithm Framework，多 518880 SSE 黄金 ETF (Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:20-25, :67)。

### 2.1 五层框架

| 层 | 模型 | 关键参数（默认值） |
|---|---|---|
| L1 Universe | `Gold2UniverseSelectionModel`（ManualUniverse 518880 SSE，安装 A-share fee/fill/buyingpower/settlement）| — |
| L2 Alpha | `Gold2TrendAlphaModel` 包装 `Gold2TrendFactor`（MA20/120 交叉 + AU.SHF 确认）| `trend-ma-short=20, trend-ma-long=120, trend-floor=0.2, trend-disable=false` |
| L3 Portfolio | `Gold2VolTargetPortfolioModel` 包装 `Gold2VolRegimeFactor`（EWMA vol-target + dead-zone）| `ewma-lambda=0.94, vol-target=0.11, vol-warmup=60, smooth-alpha=0.25, rebalance-threshold=0.05` |
| L4 Risk | 复合 min-chain：`Gold2ExtremeRiskModel`（`extreme-vol-cap=0.3`，RVol_60d/VIX 触发）+ `Gold2RealRateCapModel`（`realrate-cap=0.6`，仅 RISING_FAST regime）| `extreme-vol-cap=0.3, realrate-cap=0.6` |
| L5 Execution | `AShareLotSizeExecutionModel` | — |

证据：L1-L5 装配在 Gold2BetaVolTargetStrategy.cs:87-108；因子实例化在 :79-85；数据管道（AuShfDailyBar + FredMacroData VIX/DFII10）在 :113-145 (Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:79-145)。

### 2.2 11 个可调参数（G1 网格仅覆盖 2 个）

`GetTunableParameterNames()` 返回 11 项：`trend-ma-short, trend-ma-long, ewma-lambda, vol-target, vol-warmup, smooth-alpha, rebalance-threshold, extreme-vol-cap, realrate-cap, trend-floor, trend-disable` (Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:180-185)。G1 网格只调 `trend-ma-short` 与 `vol-target` 两个；其余 9 个保持 G0 冻结默认。

### 2.3 证明子类不修改成熟逻辑

`Gold2ClosedLoopProofStrategy : Gold2BetaVolTargetStrategy` 只叠加 `TracingExecutionModel` 装饰器 + 形式化 trace sink，**不修改** Gold2 成熟的 sizing/fill/fee/holdings 逻辑 (Algorithm.CSharp/Gold2ClosedLoopProofStrategy.cs:48-49)。

---

## 3. 参数是如何优化的

G1 是 2×2 网格搜索：`{trend-ma-short: [20, 30]} × {vol-target: [0.11, 0.15]}`，全笛卡尔 4 候选，`budget=4, seed=17`，仅在 **TRAIN** 分区上跑 (Scripts/gold2_closed_loop/construct_p2.py:112-119)。

- **选择指标**：`sharpe` desc（默认），tie-break `net_profit` desc / `mdd` asc (Scripts/gold2_closed_loop/adapters/parameter_optimizer.py:157-165)。
- **门槛**：`min_trades=1, max_mdd=0.5, min_dsr=0.0`（单元测试默认，因未传 preregistered success_thresholds）(adapters/parameter_optimizer.py:77-79)。

### 3.1 修复后 G1 实际选中结果（诚实）

| 窗口 | G1 attempted | G1 selected | best sharpe | 说明 |
|---|---|---|---|---|
| W1 | 4 | **G1-W1-train-3** (trend-ma-short=30, vol-target=0.15) | 0.5609 | 选中 (W1/g1-events.jsonl:G1-W1-train-3 SUCCEEDED) |
| W2 | 4 | **G1-W2-train-3** (trend-ma-short=30, vol-target=0.15) | 0.2717 | 选中 (W2/g1-events.jsonl:G1-W2-train-3 SUCCEEDED) |
| W3 | 4 | **null** | -0.1492 (G1-W3-train-1) | 4 候选全 PRUNED，全部 sharpe 为负，min_dsr=0.0 门槛过滤 (W3/g1-events.jsonl:G1-W3-train-1 PRUNED) |
| W4 | 4 | **null** | -0.0785 (G1-W4-train-1) | 4 候选全 PRUNED，同上 (W4/g1-events.jsonl:G1-W4-train-1 PRUNED) |

W3/W4 的 None **不是 bug**，而是诚实的负筛选：G0 自身训练期 Sharpe 已为负（W3=-0.3586, W4=-0.3359，见 §4），所有 G1 候选无法越过 `min_dsr=0.0` 门槛。这与修复前 16/16 全 FAILED_STRATEGY 有本质区别。

---

## 4. 最初的回测结果如何

### 4.1 G0 训练期度量（P2 g0_metrics，已与 LEAN 原生 summary.json 交叉确认）

| 窗口 | 训练区间 | Sharpe | net_profit | mdd | dsr/sortino | total_fees | end_equity |
|---|---|---|---|---|---|---|---|
| W1 | 2018-01-02..2020-12-31 | **0.5316** | 0.315 | 0.074 | 0.5001 | 10780.032502 | 1315011.7675 |
| W2 | 2019-01-01..2021-12-31 | **0.0817** | 0.147 | 0.147 | 0.0766 | 10218.449271 | 1146961.4507 |
| W3 | 2020-01-01..2022-12-31 | **-0.3586** | 0.0106 | 0.165 | -0.3164 | 8120.860602 | 1010626.7394 |
| W4 | 2021-01-01..2023-12-31 | **-0.3359** | 0.048 | 0.089 | -0.3211 | 13182.644126 | 1048031.9559 |

证据：result/gold2-p2-construction/p2_report.json:W1-W4 g0_metrics blocks；交叉确认 W1-G0-C0-summary.json 的 `endEquity=1315011.7675, sharpeRatio=0.5316, sortinoRatio=0.5001` (W1/G0/W1-G0-C0-summary.json:totalPerformance.portfolioStatistics)，W3-G0-C0-summary.json 的 `sharpeRatio=-0.3586, probabilisticSharpeRatio=0.0268, sortinoRatio=-0.3164` (W3/G0/W3-G0-C0-summary.json)。

G0 在 W1-W2 盈利但偏弱，在 W3-W4 Sharpe 与 Sortino 双双为负 —— 这正是 §3 中 W3/W4 G1 None 的根因。

### 4.2 G0 盲测（OOS）度量（P4 g0_blind_metrics）

| 窗口 | 盲年区间 | trades | G0 blind Sharpe | netProfit | endEquity |
|---|---|---|---|---|---|
| W1 | 2022-01-01..2022-12-31 | 43 | -0.2288 | 2.566% | ¥1,025,659.13 |
| W2 | 2023-01-01..2023-12-31 | 55 | -0.037 | 3.429% | — |
| W3 | 2024-01-01..2024-12-31 | 100 | 0.6578 | 7.324% | — |
| W4 | 2025-01-01..2025-12-31 | 103 | 1.6589 | 17.660% | — |

证据：p4_report.json g0_blind_metrics block (result/gold2-p4-construction/p4_report.json:61-98)；config.json start-date/end-date 确认是盲年非训练期 (W1/blind/G0/config.json:parameters.start-date=2022-01-01, W4/blind/G0/config.json:parameters.start-date=2025-01-01)；summary.json Sharpe 比率匹配 (W1/blind/G0/W1-G0-BLIND-summary.json:statistics.Sharpe Ratio = -0.229, ... W4/blind/G0/W4-G0-BLIND-summary.json:sharpe=1.659)。

---

## 5. 复盘发现了什么有价值信息

### 5.1 G2 FormalReviewAdapter bundle（layer_attribution）

`FormalReviewAdapter` 在 4/4 窗口产出 **VALID** 冻结 bundle，`attribution_method = "telescoping"`，`layer_attribution` 把已实现 P&L 在 `extreme_risk` 与 `realrate_cap` 两层间拆分（`trend` 与 `vol_target` 均 0.0000）。`w_realized` 来自最后一个 post-fill 的 HOLDINGS_SNAPSHOT（LEAN 原生派生，非来自 P&L/dp）。

| 窗口 | w_realized | extreme_risk pnl_pct | realrate_cap pnl_pct | validity |
|---|---|---|---|---|
| W1 | 0.1963 | 0.4985 | 0.5015 | VALID |
| W2 | 0.1982 | 0.5130 | 0.4870 | VALID |
| W3 | 0.5983 | 0.5220 | 0.4780 | VALID |
| W4 | 0.5985 | 0.5024 | 0.4976 | VALID |

证据：W1/review/review-bundle-18aea063949faa9a.json:2-20；W3/review/review-bundle-5b3552ce252dd3dd.json:5-20。

### 5.2 FeedbackConstructionAdapter shaping_overrides 计算

`_GAP_THRESHOLD = 0.15`，`_WEIGHT_FLOOR = 0.5`，`_WEIGHT_CEIL = 3.0` (adapters/feedback_construction.py:50-60)。逻辑：对每个 layer，若 `|gap| > 0.15`，则 `weight = |gap| / 0.15`，clamp 到 [0.5, 3.0]，写入 `overrides[term]` (adapters/feedback_construction.py:188-209)。由于每层 `pnl_pct_of_total ≈ 0.50`（远超 0.15），4/4 窗口均计算出 `shaping_overrides = {extreme_risk_contrib_penalty: 3.0, realrate_cap_contrib_penalty: 3.0}`（W1/W2/W3/W4 全部触顶 3.0）(p2_report.json:g2_shaping fields per window)。

### 5.3 g2_runner 转发 shaping（C2 修复，关键）

修复后的 `construct_p2.py:g2_runner` **确实**把完整 shaping dict 通过 `params_override=p` 转发到 C# 策略，使 `effCap = base_cap / penalty` 真正与 G1 分叉 (Scripts/gold2_closed_loop/construct_p2.py:131-147, 注释 134-138 明示 "C2: forward the G2 shaping terms ... so the C# strategy's effCap = base_cap / penalty actually diverges from G1")。对比 `g1_runner` 仅转 `trend-ma-short` 和 `vol-target` (construct_p2.py:93-102)。

### 5.4 G2 是否 aliased 到 G1（诚实 per-window）

| 窗口 | G2 候选结果 | g2_aliased_to_g1 |
|---|---|---|
| W1 | G2-W1-train-0 SUCCEEDED, sharpe=0.4718 | **false** |
| W2 | G2-W2-train-0 SUCCEEDED, sharpe=0.0971 | **false** |
| W3 | 两 G2 候选均 PRUNED, sharpe=-0.4301/-0.438 | **true** |
| W4 | 两 G2 候选均 PRUNED, sharpe=-1.1275/-1.1777 | **true** |

证据：W1/g2-events.jsonl:G2-W1-train-0 SUCCEEDED；W4/g2-events.jsonl:G2-W4-train-0 PRUNED；p2_report.json:g2_aliased_to_g1 字段。`valid_window_count = 4, stop_p2_verdict = PASS` (p2_report.json:valid_window_count + stop_p2_verdict)。

### 5.5 bundle 的本质：fill-notional 拆分

bundle 的 layer_attribution 是把已实现 P&L 在风险层之间做 **fill-notional 拆分**（telescoping 方法），不是 alpha 归因。`trend`/`vol_target` 两层 pnl_pct_of_total = 0.0000，因为这两层不直接产生已实现盈亏 —— 信号来自它们但 P&L 实现在风险层约束后的持仓上。

---

## 6. 重构的新量化是如何的

### 6.1 G3 触发条件

`g3_trigger.py:evaluate_g3` 触发条件：窗口有 ≥ `MIN_GENERATIONS=3` 个相邻 valid same-evidence generation 记录，每条 `attribution_gap ≥ GAP_THRESHOLD=0.15`（inclusive）、`shaping_weight ≥ WEIGHT_CEILING=3.0` 或 `NON_CONVERGED`、`pending_candidate_count == 0` (Scripts/gold2_closed_loop/g3_trigger.py:119-137)。

### 6.2 修复后：gap 来自真实 P2 bundle，4/4 触发

修复后的 `construct_p3.py:_real_gap_for_window` 取 P2 review bundle 中 `extreme_risk`/`realrate_cap` 两层 `|pnl_pct_of_total|` 的最大值作为 gap。实际记录的 gap：W1=0.5015, W2=0.5024, W3=0.522, W4=0.513（均 ≥ 0.15）(result/gold2-p3-construction/W1/generations.jsonl:1)，4/4 窗口 `trigger_eligible = true, trigger_alias_reason = null` (p3_report.json:lines 2-66)。

### 6.3 _StaticGen stub：写入桩源码，不编译

G3Builder 被注入 `_StaticGen` stub，其 `__call__` 返回 `source_text = "// G3 candidate (proof static generator)\n"`、`compiler_hash = "c0mp1ler"*8`、`training_gate_passed = True` (construct_p3.py:67-72)。该 source 被 hash-pin 并冻结为 G3 候选源，`source_sha256 = 5f6befe417eb852095f883bdb0eec233e3b57272a0a2152a30fc96c1c47fdf3c`，4/4 窗口**完全相同** (p3_report.json:14-15, 30-31, 46-47, 62-63)。**G3 桩源码不可编译**，这是 LLM 隔离设计的必然代价（见 §7、§8）。

### 6.4 别名链：final_stage 解析回 G2

`construct_p4.py:_resolve_final_stage` 逻辑：若 `win.get("g2_shaping")` 存在则返回 `"G2"`；否则若 `g1_selected` 存在返回 `"G1"`；否则 `"G0"` (Scripts/gold2_closed_loop/construct_p4.py:116-128)。由于 4/4 窗口都有 `g2_shaping`，`final_stage_per_window = {W1:G2, W2:G2, W3:G2, W4:G2}` (p4_report.json:137-143)。

**所以真正被盲测的"重构"是 G2-shaped 策略，而非 G3-generated 策略。**

### 6.5 G2 shaping 的机制（C2 重构的实际语义）

C2 重构保持策略骨架不变，仅在风险 cap 上叠加**逐层 penalty 乘子**：

```csharp
// Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:97-107
var extremePenalty = GetDecimalParameter("extreme_risk_contrib_penalty", 1.0m);
var realratePenalty = GetDecimalParameter("realrate_cap_contrib_penalty", 1.0m);
var effExtremeCap = Math.Max(0m, _extremeCap / Math.Max(0.0001m, extremePenalty));
var effRealRateCap = Math.Max(0m, GetDecimalParameter("realrate-cap", 0.6m) / Math.Max(0.0001m, realratePenalty));
```

- **default penalty = 1.0** → `effCap = baseCap`，G0/G1 byte-identical。
- **G2 penalty = 3.0**（W1-W4 均触顶）→ `effExtremeCap = 0.3/3.0 = 0.1`，`effRealRateCap = 0.6/3.0 = 0.2`，cap 收紧，极端/rising regime 持仓变小。

**realrate 层是 regime-aware**：`Gold2RealRateCapModel` 仅在 `GoldRegime.RISING_FAST` 时 `capFactor = (_effRealRateCap > 0 ? _effRealRateCap : computed.Value)`，其他 regime `capFactor = 1.0m` (Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs:35-57, Common/Factors/Forward/Gold2RealRateCapFactor.cs:30-36)。这是修复后 C2 的关键 regime-aware 设计：shaping penalty 不得泄漏到本不该被 cap 的 regime。

**extreme 层**：`Gold2ExtremeRiskModel.ApplyCap(weight, triggered, cap) = triggered ? Math.Min(weight, cap) : weight` (Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs:56-57)，即仅在 RVol_60d/VIX 触发时收紧。

---

## 7. 期间如何使用大模型帮助新量化策略构建

**坦率结论：Gold2 证明中完全没有使用 LLM，这是设计隔离。重构是机械的 G2 shaping 乘子，G3 codegen 是 stub。**

### 7.1 规格三层禁止 LLM 介入

1. **interface_readiness 静态扫描门禁**：`_FORBIDDEN_REFERENCES = ("evolution_scheduler", "layer_state", "evolution_state", "generation_state")`，用整 token 正则 `r"\b" + re.escape(ref) + r"\b"` 匹配，禁止证明导入生产 daemon/evolution/inspiration 生成机件，且此 ready 标志门控 preregister (Scripts/gold2_closed_loop/interface_readiness.py:33-38, 135-139)。
2. **规格 §4/§6 适配器隔离**：明确"现有 optimizer/review/feedback/inspiration 不直接复用，而是通过两级适配器接口调用"，枚举 adapter 禁止行为：调用生产 daemon、读写生产 evolution state、自行递增或覆盖 generation (docs/superpowers/specs/2026-07-14-gold2-closed-loop-economic-proof-design.md:144-162)。
3. **proof-only G3 builder/trigger**：`g3_builder.py:13-16` 声明 "does NOT read global history or the production Scripts/inspiration/generations.py machinery (spec §6 line 158 forbids reuse)"；`g3_trigger.py:27-28` 声明 "PROOF-ONLY. It does NOT import the production inspiration trigger machinery"。

### 7.2 G3 _StaticGen 桩而非真实生成

`construct_p3.py:51-72` 的 `_StaticGen` docstring 明示："A deterministic generator for the G3 builder (proof-only): produces a fixed candidate source and passes the training gate. Used so the STOP P3 harness can exercise the triggered-build path **without depending on the production LLM generator**." 桩源码 `"// G3 candidate (proof static generator)\n"` 不编译。

### 7.3 生产 inspiration 系统平行且隔离

生产 SoloQuant inspiration 系统 `Scripts/inspiration/hypothesize.py:_call_llm` 使用 `client.summarize_with_llm`（model `glm-5.1`），写 hypothesis markdown 到 `Results/soloquant/local-strategies` (Scripts/inspiration/hypothesize.py:53-74)。但证明树只在 `FORBIDDEN-reference` 注释里引用 `Scripts/inspiration`（g3_builder.py:15、interface_readiness.py:6），从不导入。对证明树 grep 所有 LLM 调用模式（`summarize_with_llm/client.chat/openai/anthropic/deepseek/glm`）零真实命中（仅 data_sources.py 的 `re.fullmatch` 正则命中，非 LLM）(docs/check-gold2-details.md:247-250)。

**因此：LLM 没有帮助构建 Gold2 重构。重构是机械的 G2 shaping 乘子（penalty 3.0 触顶），G3 codegen 是 stub。**

---

## 8. 结论与诚实限制

### 8.1 诚实裁决

修复后的闭环产出了一项真实可评价的重构 —— G2 shaping（在风险 cap 上叠加 `extreme_risk_contrib_penalty=3.0` 与 `realrate_cap_contrib_penalty=3.0`，驱动 `effCap = base_cap / penalty` 收紧极端/rising regime 持仓）。但 OOS 数据诚实证伪了它：4/4 窗口 ΔSharpe 全为负（-1.3309, -0.4886, -0.4567, -0.5060），`aggregate_delta = -2.7822`，bootstrap p_value = 0.0，CI 整体低于零，`verdict = NOT_EFFECTIVE`，`disposition = REFUTED`。

**这不是"有希望的 null"，这是稳定的负向证伪。** G2 shaping 在 W3/W4（G0 自带正 Sharpe 的窗口）反而把 0.658/1.659 拉到 0.201/1.153 —— 收紧 cap 既降低了上涨 regime 的暴露，也没有在下跌 regime（W1/W2 G0 已为负）提供足够保护，反而扩大了 W1 的亏损（-0.229 → -1.560）。

### 8.2 修复前 vs 修复后对比

| 维度 | 修复前（§1 check-gold2-details.md）| 修复后（本审计）|
|---|---|---|
| G1 试验 | 16/16 FAILED_STRATEGY（run_id 含斜杠 bug，metrics=null）| 16/16 SUCCEEDED 或 PRUNED（W1/W2 选中 train-3，W3/W4 诚实 None）|
| G2 shaping | 全部 aliased_to_g1=True（未真运行）| W1/W2 真运行 SUCCEEDED, W3/W4 PRUNED aliased 回 G0 |
| G3 触发 | 4/4 NOT_TRIGGERED（gap 取自空 bundle）| 4/4 TRIGGERED（gap 取自真实 P2 bundle，0.5015-0.522）|
| P4 盲测 | **从未真跑**，复用 p2_report 的 g0_metrics 当盲测 | 真起 LEAN dotnet Launcher 跑盲年 2022/2023/2024/2025 |
| aggregate_delta | -0.0812（bug-shortcut null）| **-2.7822**（数据证伪）|
| "null" 性质 | bug 短路 | 数据证伪 |
| 裁决 | NOT_EFFECTIVE/REFUTED（但实验未完成）| NOT_EFFECTIVE/REFUTED（**有效完成的实验**）|

证据：修复前状态见 docs/check-gold2-details.md:11-13, 15-18, 20, 320-323, 429-436, 386-394；修复后状态见 p4_report.json:99-163 与本审计 §1-§7。

### 8.3 修复后仍存在的诚实限制

1. **封存签名仅结构性**：`public_key_fingerprint = pkfp-gold2-proof` 是占位符，`receipt = receipt-cb713d49e1ff3bcc` 是确定性伪造，`verify_seal` 只检查非空、不验证加密有效性，无外部加密密钥或追加只写发布背书。C1-C4 修复未触及此项，修复前后一致 (docs/check-gold2-details.md:442; docs/superpowers/specs/2026-07-20-gold2-closed-loop-fix-design.md:250-252)。
2. **W3/W4 G1 仍是 None**：因 G0 自身 Sharpe 为负（W3=-0.3586, W4=-0.3359）且 `min_dsr=0.0` 门槛过滤掉所有候选 —— 这是诚实负筛选，不是 bug。W3/W4 的 final stage 名义上是 G2 alias 回 G0，但 P4 仍跑了 G0 vs G2 盲测对比且 G2 shaping 配置真传入了 LEAN（W3 G0=0.658 vs G2=0.201；W4 G0=1.659 vs G2=1.153）(docs/check-gold2-details.md:443)。
3. **G3 `_StaticGen` stub 不编译**：candidate source 被写为桩源码 `"// G3 candidate (proof static generator)"`，不编译，这是 LLM 隔离设计的必然代价 —— 盲测无法跑桩源码，最终候选别名回 G2 (construct_p3.py:68; docs/check-gold2-details.md:444; docs/superpowers/specs/2026-07-20-gold2-closed-loop-fix-design.md:250-252)。**严格说，"盲测的 refactor"是 G2-shaped 而非 G3-generated。**
4. **regime-aware 修复**：C2 在 `Gold2RealRateCapModel` 中显式仅对 `RISING_FAST` regime 施加 `effRealRateCap`，其他 regime `capFactor = 1.0m`，确保 shaping penalty 不泄漏到本不该被 cap 的 regime (Gold2RealRateCapModel.cs:35-57)。这避免了"shaping 跨 regime 误伤"的潜在 bug，但即使做了 regime-aware 修复，OOS 仍证伪 —— 说明问题不在 regime 泄漏，而在 shaping 方向本身错误。

### 8.4 反 p-hacking 边界守恒

四道硬线在修复后完整保留：

1. **G1 网格不动**：`{trend-ma-short in [20,30]} × {vol-target in [0.11,0.15]}` 全笛卡尔 4 候选，`budget=4/seed=17` 覆盖整个网格（全网格搜索无截断），`min_dsr=0.0` 门槛不变 (construct_p2.py:112-119; selection.py:238, 250-251)。W3/W4 G1 None 正是 min_dsr 过滤的诚实结果，非为制造选中而放松。
2. **盲测用 FROZEN 参数不重优化**：`construct_p4.py:_blind_runner_factory` 跑盲年时 `params_override` 取自冻结候选（G0=默认, G1=selected, G2=shaping, G3=alias），不在盲年做任何 G1 重搜/G2 重构 (construct_p4.py:131-144; docs/superpowers/specs/2026-07-20-gold2-closed-loop-fix-design.md:249-251)。
3. **规格 §6 line 244**："盲测仅评价被冻结候选；盲测之后禁止生成、重排、修复或重试候选；任何逻辑修改必须使用新实验 ID" (docs/superpowers/specs/2026-07-14-gold2-closed-loop-economic-proof-design.md:244)。
4. **记忆反复警告**：`gold2-proof-p4-pass.md` 与 `gold2-proof-p2-pass.md` 同款警告——"Do NOT widen the G1 grid or relax gates to flip the verdict to EFFECTIVE — that would be p-hacking. To pursue a positive verdict, run a NEW experiment id with a different (preregistered) parameter space / structure, per spec §14 line 422" (memory/gold2-proof-p4-pass.md:24, gold2-proof-p2-pass.md:20)。

### 8.5 最终判语

修复的成功是**方法学的成功**，不是**经济 verdict 的成功**：闭环从"未产出的 bug 短路"升级为"产出真实重构并被 OOS 数据诚实证伪的完整实验"。G2 shaping 的方向（收紧极端/rising regime cap）在 518880 的 2022-2025 数据上是错的 —— 它在上涨 regime（W3/W4）削掉了正收益，在下跌 regime（W1/W2）没能提供足够保护。若要追求正向 verdict，须按 §14 line 422 另立新 experiment id，使用不同的（预注册）参数空间或结构，**不得**在本实验内放宽门槛或扩网格。

---

## 责任声明与证据可追溯性

本审计所有数字与事实论断均附 `file:line` 证据引用，来源为 4 个并行 reader 在 worktree `/home/project/hope/Lean/.claude/worktrees/gold2-closed-loop-proof` 上对修复后证据树的扫描结果。主要证据文件（均为绝对路径前缀 `/home/project/hope/Lean/.claude/worktrees/gold2-closed-loop-proof/`）：

- `result/gold2-p2-construction/p2_report.json` —— G0 训练度量、G1 选中结果、G2 shaping、alias 状态、STOP P2 verdict
- `result/gold2-p2-construction/W{1..4}/{g1,g2}-events.jsonl` —— G1/G2 候选事件流
- `result/gold2-p2-construction/W{1,3}/review/review-bundle-*.json` —— G2 review bundle layer_attribution
- `result/gold2-p3-construction/p3_report.json` 与 `W{1..4}/generations.jsonl` —— G3 触发与 generation 记录
- `result/gold2-p4-construction/p4_report.json` —— G0/G2 盲测度量、bootstrap、verdict、final_stage_per_window
- `result/gold2-p4-construction/W{1..4}/blind/{G0,G2}/{config.json,*-BLIND-summary.json}` —— 真实 LEAN 盲测 config 与统计
- `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`、`Gold2ClosedLoopProofStrategy.cs`、`Models/Gold2/Gold2{ExtremeRisk,RealRateCap}Model.cs`、`Common/Factors/Forward/Gold2RealRateCapFactor.cs` —— 策略与重构机制
- `Scripts/gold2_closed_loop/{construct_p2,construct_p3,construct_p4,g3_trigger,g3_builder,interface_readiness}.py` 与 `adapters/{parameter_optimizer,feedback_construction}.py` —— 闭环脚本
- `docs/superpowers/specs/2026-07-{14,20}-gold2-closed-loop-*.md` —— 设计与修复规格
- `docs/check-gold2-details.md` —— 修复前状态基线

**未在证据中找到的子问题**：无。用户的 7 个子问题全部在 reader findings 中有直接证据支持。

审计人：Claude（subagent）。审计日期：2026-07-21。
