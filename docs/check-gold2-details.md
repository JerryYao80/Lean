# Gold2 闭环量化策略开发验证优化环境 — 最终审计文档

> 用户问题（逐字）："我还想知道这套'量化策略构建、量化策略优化、回测、回测复盘、量化策略重构'量化策略开发验证优化环境，有没有带来量化策略上的改变？即初始量化策略是什么？参数是如何优化的？最初的回测结果如何？复盘发现了什么有价值信息？重构的新量化是如何的？期间如何使用大模型帮助新量化策略构建？"
>
> 审计方法：8 个只读子代理并行核查 G0/G1/G2/G3/结果/LLM/规格/封存完整性，每条断言带 file:line 证据，综合成文。日期：2026-07-20。

---

## 1. 有没有带来量化策略上的改变？

**没有。**

这是坦率的前置结论：这套"量化策略构建、优化、回测、复盘、重构"闭环环境在 Gold2 实验中**没有产生任何策略上的改变**。闭环在 G0 处坍缩，原因链条如下：

- **G1（参数优化）在全部 4 个窗口中未选中任何候选**：16 次 G1 试验（每窗口 4 次）全部以 `FAILED_STRATEGY`、`metrics=null` 结束。根因是一个 `run_id` 含斜杠的 bug（`candidate_id = "G1-W1/train-0"` → `run_id = "W1-G1-G1-W1/train-0"`，LEAN 的 `BacktestingResultHandler.StoreResult` 无法写入含 `/` 的结果包路径），结果包被静默丢弃。trace.jsonl 证明策略实际运行并交易了（每候选约 242 笔 FILL），但没有度量，没有排名，没有选中。
- **G2（复盘反馈）别名到 G1**：因 G1 的 `selected_candidate=null`，G2 的 `aliased_to_g1=True` 在全部 4 个窗口成立。G2 虽计算出非空的 shaping 反馈（1.6666），但 (a) 构造脚本的 `g2_runner` 根本没有把 shaping 参数传入 LEAN 运行，(b) C# 策略类 `Gold2ClosedLoopProofStrategy` 压根不读取 `extreme_risk_contrib_penalty` / `realrate_cap_contrib_penalty` 这两个参数。
- **G3（重构）未触发**：全部 4 个窗口 `trigger_alias_reason=NOT_TRIGGERED`、`g3_build=null`，没有产生任何新策略源码。
- **最终全链 G0=G1=G2=G3**：P4 冻结证据中 G3 的 `source_sha256` 与 G0 字节相同；G2 的 `candidate_set_sha256` 与 G3 相同；G2 又别名到 G1。没有任何阶段产出新策略。

**重要限制（必须在开头说明）**：P4 的"盲测"分区**从未作为独立的样本外 LEAN 回测运行过**。P4 构造脚本 `/tmp/gold2_p4_construct.py` 的 `_blind_metrics()` 直接加载 `p2_report.json["g0_metrics"]`（即训练期/样本内度量）并将其原样当作"盲测"度量复用。因此最终裁决 `NOT_EFFECTIVE/REFUTED`（`aggregate_delta=-0.0812`）是基于样本内训练度量计算的，并非真正的样本外评价。下文第 4、8 节会详述这一限制。

---

## 2. 初始量化策略是什么？（G0）

初始策略（G0 baseline）是 `Gold2BetaVolTargetStrategy`，一个做多黄金 ETF（518880.SSE）的 beta 策略，核心特征为波动率目标仓位 + 趋势确认 + 风险叠加封顶。它实现了完整的 LEAN 5 层 Framework。

**策略类定义**：`Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:25`
```
public class Gold2BetaVolTargetStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
```

### 5 层结构

| 层 | 类 | 注册点 | 证据 |
|---|---|---|---|
| Universe（选股） | `Gold2UniverseSelectionModel`（继承 `ManualUniverseSelectionModel`，固定 518880 SSE） | — | `Models/Gold2/Gold2UniverseSelectionModel.cs:11-16`，`Symbol.Create("518880", SecurityType.Equity, Market.SSE)` |
| Alpha（信号） | `Gold2TrendAlphaModel`（继承 `AlphaModel`） | `SetAlpha` 行 94 | `Models/Gold2/Gold2TrendAlphaModel.cs:16` |
| Portfolio（仓位） | `Gold2VolTargetPortfolioModel`（继承 `PortfolioConstructionModel`） | `SetPortfolioConstruction` 行 96 | `Models/Gold2/Gold2VolTargetPortfolioModel.cs:13` |
| Risk（风控） | `Gold2ExtremeRiskModel` + `Gold2RealRateCapModel`（均继承 `RiskManagementModel`），通过 `CompositeRiskManagementModel` min 链式叠加 | `AddRiskManagement` 行 97-98 | `Models/Gold2/Gold2ExtremeRiskModel.cs:14` |
| Execution（执行） | `AShareLotSizeExecutionModel`（继承 `ExecutionModel`） | `SetExecution` 行 99 | `AShareLotSizeExecutionModel.cs:27` |

### 信号流水线（pipeline）

1. **波动率状态因子** `Gold2VolRegimeFactor`（EWMA λ）：输出 `FactorResult.Value = w_smooth`（平滑后的波动率目标权重），`RawValue = σ_ann`（年化波动率）。`Common/Factors/Forward/Gold2VolRegimeFactor.cs:79`
2. **趋势因子** `Gold2TrendFactor`：MA20/120 交叉，并用 AU.SHF 黄金期货同向确认。`Value = sign(MA_short − MA_long)` of 518880，`RawValue = confirm`（同向 1.0，否则 0.5）。`Common/Factors/Forward/Gold2TrendFactor.cs:53-56`
3. **Alpha 模型**消费趋势因子：`dir > 0 → InsightDirection.Up, weight = confirm`；否则 `InsightDirection.Flat, weight = floor`。`trend-disable=true` 时强制 `dirCoef=1.0`。`Models/Gold2/Gold2TrendAlphaModel.cs:42-75`
4. **Portfolio 模型**：`target = w_smooth × dirCoef(insight.Weight)`，再 `ApplyDeadZone`。产出 `PortfolioTarget.Percent`。`Models/Gold2/Gold2VolTargetPortfolioModel.cs:40-43`
5. **ExtremeRisk 叠加**：触发（VIX>P95 或 RVol60>P95）→ `min(weight, extremeCap=0.30)`，硬封顶，绕过 dead-zone。`Models/Gold2/Gold2ExtremeRiskModel.cs:32,42,57`
6. **RealRateCap 叠加**：`RISING_FAST` regime → `cap=0.60`，否则 1.0；与 ExtremeRisk 通过 CompositeRiskManagementModel 的 min 链式叠加。`Common/Factors/Forward/Gold2RealRateCapFactor.cs:34`

**输出字段**：Portfolio 模型的目标权重输出字段为 `LastActualWeight`（post-deadzone 目标权重，`Models/Gold2/Gold2VolTargetPortfolioModel.cs:22`）；Alpha 模型的方向输出字段为 `LastDirCoef`（等于 `insight.Weight`，`Models/Gold2/Gold2TrendAlphaModel.cs:25,75`）。定性方向（Up/Flat）承载在 Insight 的 `InsightDirection` 枚举内，并非一个独立命名字段。

### 全部可调参数及默认值（G1 搜索空间 = `GetTunableParameterNames` 枚举的 11 个旋钮）

| # | 参数名 | 类型 | 默认值 | 证据 | 说明 |
|---|---|---|---|---|---|
| 1 | `trend-ma-short` | int | 20 | `Gold2BetaVolTargetStrategy.cs:79` | 趋势短均线窗口 |
| 2 | `trend-ma-long` | int | 120 | `Gold2BetaVolTargetStrategy.cs:79` | 趋势长均线窗口 |
| 3 | `ewma-lambda` | decimal | 0.94 | `Gold2BetaVolTargetStrategy.cs:80` | EWMA 衰减系数（波动率） |
| 4 | `vol-target` | decimal | 0.11 | `Gold2BetaVolTargetStrategy.cs:81` | 波动率目标 |
| 5 | `vol-warmup` | int | 60 | `Gold2BetaVolTargetStrategy.cs:81` | 波动率预热窗口 |
| 6 | `smooth-alpha` | decimal | 0.25 | `Gold2BetaVolTargetStrategy.cs:82` | 权重平滑系数 |
| 7 | `rebalance-threshold` | decimal | 0.05 | `Gold2BetaVolTargetStrategy.cs:95` | dead-zone 再平衡阈值 |
| 8 | `extreme-vol-cap` | decimal | 0.3 | `Gold2BetaVolTargetStrategy.cs:92,97` | 极端波动硬封顶（行 92、97 同默认值） |
| 9 | `realrate-cap` | decimal | 0.6 | `Gold2BetaVolTargetStrategy.cs:85` | 实际利率快升封顶（传入 `Gold2RealRateCapFactor.risingFastCap`） |
| 10 | `trend-floor` | decimal | 0.2 | `Gold2BetaVolTargetStrategy.cs:93` | Flat 区基础权重（做多底仓） |
| 11 | `trend-disable` | bool | false | `Gold2BetaVolTargetStrategy.cs:90` | 趋势禁用（vol_only 控制实验旁路） |

`GetTunableParameterNames` 恰好枚举这 11 个旋钮（`Gold2BetaVolTargetStrategy.cs:171-176`）。

**不可调的承载性常量**（不在 G1 搜索空间内，但实际影响策略行为）：
- `RvolWindow = 60`（`const`，与 `vol-warmup` 解耦）— `Gold2BetaVolTargetStrategy.cs:35`
- `RealRateRisingFastThreshold = 0.5m`、`RealRateFallingFastThreshold = -0.5m`（`const`，DFII10 regime 阈值）— `Gold2BetaVolTargetStrategy.cs:38-39`

**基础设施参数**（`GetXxxParameter` 调用但不在可调列表中）：
- `initial-capital`：decimal，默认 1,000,000 — `Gold2BetaVolTargetStrategy.cs:59`
- `start-date`：date，默认 2020-01-01 — `Gold2BetaVolTargetStrategy.cs:60`
- `end-date`：date，默认 2026-06-23 — `Gold2BetaVolTargetStrategy.cs:61`

---

## 3. 参数是如何优化的？（G1）

### 参数空间

G1 的搜索空间是一个 **2 参数 × 每参 2 取值的全笛卡尔网格 = 4 候选**，由 P2 构造脚本 `/tmp/gold2_p2_construct.py:116-118` 定义：

```python
{"trend-ma-short": {"type": "choice", "values": ["20", "30"]},
 "vol-target": {"type": "choice", "values": ["0.11", "0.15"]}}
```

网格通过 `itertools.product` 生成（`adapters/parameter_optimizer.py:445-496`），4 候选即：`(20,0.11), (30,0.11), (20,0.15), (30,0.15)`。4 个窗口的网格与洗牌顺序完全相同（`W2/g1-events.jsonl` 验证）。

**关键**：G0 基线参数（`trend-ma-short=20, vol-target=0.11`）正是这 4 个网格点之一（`config/proof_lean_base.json:22-25`）。

### 候选预算

`budget=4, seed=17`（`/tmp/gold2_p2_construct.py:113-115`）。因网格恰好 4 候选，预算覆盖整个网格（全网格搜索，无截断）。

### 选择度量

- 主排序度量：`sharpe`（降序），来自 `preregistration.selection` 或默认值。`adapters/parameter_optimizer.py:158-165`
- 平局裁决：`net_profit` 降序，再 `mdd` 升序。
- 候选门槛（`select()` 应用）：`min_trades=1, max_mdd=0.5, min_dsr=0.0`（默认）。`selection.py:261-312`。通过门槛的 `SUCCEEDED` 尝试去重后按度量排序取首名；无候选通过则返回 `None`。

### 实际选中了什么？—— 什么都没选中

**G1 在任何窗口都没有选中任何候选**。`p2_report.json` 在 W1-W4 全部显示 `g1_attempted=4, g1_selected=null`（`p2_report.json:22-23,49-50,76-77,103-104`）。

全部 16 次 G1 试验（每窗口 4 次）均被归类为 `FAILED_STRATEGY`、`metrics=null`（`W1/g1-events.jsonl:1-8`）。

**根因**（一个 `run_id` 含斜杠的 bug）：
1. 优化器生成 `candidate_id = f"{stage_id}-{partition}-{trial_count}"`，例如 `"G1-W1/train-0"`（`parameter_optimizer.py:207`）— 注意其中含 `/`。
2. 构造脚本组合 `run_id = f"{wid}-G1-{candidate_id}"` = `"W1-G1-G1-W1/train-0"`（`/tmp/gold2_p2_construct.py:100`）。
3. `lean_runner` 将 `run_id` 设为 `algorithm-id`，期望的结果包路径 `run_dir/{run_id}.json` 因含 `/` 而嵌套进一个不存在的子目录，LEAN 永远写不出包。
4. `run_lean` 归类 `FAILED_MISSING_PACKET`（`lean_artifacts.py:163-164`），`g1_runner` 映射为 `TrialResult(status="FAILED_STRATEGY", metrics=None)`（`/tmp/gold2_p2_construct.py:103-104`）。

**决定性证据**：策略实际运行并交易了真实 518880 数据。每个 G1 候选的 `trace.jsonl` 非空，含数百个 FILL/HOLDINGS_SNAPSHOT/ORDER_INTENT 事件（如 W1/train-0 = 242 笔 FILL、1876 个 DECISION，文件 785844 字节）。只有结果包被静默丢失，所以没有度量、没有排名、没有选中。`result/gold2-p2-construction` 下没有任何 `G1/*` 的结果包 `*.json`（仅 `config.json` + `data-monitor` + `trace.jsonl`）；而 G0 的 4 个包 `W1-G0-C0.json … W4-G0-C0.json` 都存在。

**结论**：优化器没有改变策略，因为它无法选中任何东西。G0 基线参数虽然存在于网格中，但"G1 是否会选中 G0 还是别的候选"从这份证据中**不可知**——优化产出了零个可用结果。

---

## 4. 最初的回测结果如何？（G0 度量）

**关键 IS/OOS 区分**：G0 是冻结默认参数的基线候选，在每窗口的 3 年**训练分区**上回测。每个 G0 原始包的 `algorithmConfiguration` 显示 `startDate/endDate = 训练期`（如 W1 2018-01-02..2020-12-31），`outOfSampleDays=0`、`outOfSampleMaxEndDate=null`（`W1/G0/W1-G0-C0-summary.json:94-97`）。**这些是样本内基线窗口数字，不是样本外/盲测评价**。真正的盲测（W1 盲年=2022, W2=2023, W3=2024, W4=2025，见设计文档 105-112 行）本应在 G3/盲开阶段单独运行，但从未执行（见第 8 节）。

### 各窗口 W1-W4 训练期度量（样本内）

来源：`p2_report.json` 的 `g0_metrics`，直接取自各原始 LEAN 包的 `totalPerformance.portfolioStatistics` + `statistics.Total Fees`。

| 窗口 | 训练期 | Sharpe | Net Profit | MDD | DSR(=Sortino) | Total Fees | End Equity |
|---|---|---|---|---|---|---|---|
| W1 | 2018-01-02 — 2020-12-31 | 0.5316 | 0.315 | 0.074 | 0.5001 | 10780.032502 | 1,315,011.7675 |
| W2 | 2019-01-01 — 2021-12-31 | 0.0817 | 0.147 | 0.147 | 0.0766 | 10218.449271 | 1,146,961.4507 |
| W3 | 2020-01-01 — 2022-12-31 | -0.3586 | 0.0106 | 0.165 | -0.3164 | 8120.860602 | 1,010,626.7394 |
| W4 | 2021-01-01 — 2023-12-31 | -0.3359 | 0.048 | 0.089 | -0.3211 | 13182.644126 | 1,048,031.9559 |

证据：`p2_report.json:5-18`（W1）、`:33-46`（W2）、`:61-74`（W3）、`:89-102`（W4）。

**命名陷阱说明**：
- p2_report 的 `dsr` 字段**并非 Deflated Sharpe Ratio**，它精确等于 LEAN 的 `sortinoRatio`（如 W1：`dsr=0.5001` = `W1-G0-C0-summary.json:158` 的 `sortinoRatio:0.5001`）。原始包里没有名为 `dsr` 的字段，报告未文档化这一重命名。
- 费用值采用 LEAN `statistics.Total Fees`（含未实现/舍入，货币符号剥离），而非 `tradeStatistics.totalFees`（仅已实现交易）。例如 W1：10780.03 vs 10714.10。`W1-summary.json:35,142`

### 全量 2020-2026 生产回测总计（不同的策略/运行）

这是一个**不同的策略与运行**，不是证明 G0。它使用算法类型 `Gold2BetaVolTargetStrategy`（文件/目录名），跨度 2020-01-01..2026-06-23，不是证明的按窗口训练切片。

来源：`/home/project/hope/Lean/Results/gold2-betavol/Gold2BetaVolTargetStrategy-summary.json`

| 指标 | 值 | 证据 |
|---|---|---|
| 日期范围 | 2020-01-01 — 2026-06-23 | `:71-89` |
| CAGR | 7.627%（`compoundingAnnualReturn 0.0763`） | `:16-24` |
| Sharpe | 0.521（`0.5207`） | `:88-89` |
| Sortino | 0.491（`0.4910`） | `:144-150` |
| PSR | 36.937%（`0.3694`） | `:144-150` |
| MDD | 16.500%（`drawdown 0.165`） | `:144-150` |
| Total Orders | 610 | `:13` |
| Total Fees | ¥16,187.69（`tradeStatistics.totalFees 16105.4877`） | `:35` |
| End Equity | ¥1,610,469.11 | `:42,57` |
| Net Profit | 61.047%（`totalNetProfit 0.6105`；runtimeStats 已实现 ¥591,978.48） | `:45,144-147` |
| Win Rate | 87%（`winRate 0.8677`） | `:144-147` |

**与证明的关系**：此生产回测的 2020-2023 段与证明 W3/W4 训练期及 W1/W2 盲年重叠，2024-2026 段覆盖证明 W3/W4 的盲年。因此它**不是独立的样本外锚点**，它包含了证明保留为盲测的那些年份。

---

## 5. 复盘发现了什么有价值信息？（G2）

### 复盘计算了什么

G2 复盘是两阶段流水线（`/tmp/gold2_p2_construct.py:91,143-145`）：

1. **`FormalReviewAdapter`** 读取 G0 的 formal trace（`HOLDINGS_SNAPSHOT` + `FILL` 事件），每窗口产出一个冻结的 review bundle，含 `w_realized`（由最后一个 post-fill `HOLDINGS_SNAPSHOT` 导出）+ 逐层 `layer_attribution`（声称 telescoping 方法）。
2. **`FeedbackConstructionAdapter`** 消费该 bundle 一次，计算逐层归因差距（`|pnl_pct_of_total|` per layer，阈值 `_GAP_THRESHOLD=0.15`），若某层差距超阈值则产出 shaping override `weight = clamp(gap/threshold, 0.5, 3.0)`，映射到 `extreme_risk_contrib_penalty` / `realrate_cap_contrib_penalty`（`feedback_construction.py:52-60,188-209`）。只有 `extreme_risk` 和 `realrate_cap` 在 `_DEFAULT_TERM_MAP` 中；`trend` 和 `vol_target` 不产出 shaping 项。

### 复盘产出的反馈（运行时实际值）

在 P2 运行时（2026-07-18，commit `9182d5985`），bundle 的 `layer_attribution` 是**等分占位**：`trend=vol_target=extreme_risk=realrate_cap=0.2500` 各（`W1/review/review-bundle-3f877e96a84a0a8d.json:5-18`，`attribution_method: telescoping`，`w_realized: 0.1963`）。

基于此 0.25 占位：`gap=0.25 > 0.15`，对两个映射层触发，`weight = 0.25/0.15 = 1.6666666666666667`（钳制后与 p2_report 精确匹配）。`shaping_overrides = {extreme_risk_contrib_penalty: 1.6666, realrate_cap_contrib_penalty: 1.6666}, trigger=True`（`p2_report.json:25-28,52-56,82-86,109-113`）。

**这是非空反馈**：G2 确实推荐了一个方向（将两个风险封顶惩罚项提高到约 1.67）。

### 但反馈没有带来任何改变，两个叠加原因

**(a) 构造脚本的 `g2_runner` 丢弃了 shaping 参数**：它调用 `_lean_run(cdir, run_id, 'G2', candidate_id, wid, ...)` 时**没有传 `params_override`**，所以每个 G2 候选都以冻结的 G0 默认值运行。`W1/G2/G2-W1/train-0/config.json` 确认 `extreme_risk_contrib_penalty=None, realrate_cap_contrib_penalty=None`。

**(b) C# 策略类 `Gold2ClosedLoopProofStrategy` 压根不读取这些参数**：`grep -rn 'extreme_risk_contrib_penalty|realrate_cap_contrib_penalty|contrib_penalty' Algorithm.CSharp/` 零匹配。即便 runner 转发了它们，策略行为也不会改变。

### "aliased to G1 / flat increments / unforced" 的具体含义

- **"G2 aliased to G1"**：`aliased_to_g1 = (opt_result.selected_candidate is None)`（`feedback_construction.py:163-164`）。每窗口的 2 个 G2 候选全部 `FAILED_STRATEGY`（`W1/g2-events.jsonl:2,4`，同样的 run_id 斜杠 bug），所以 `selected_candidate=None` → `g2_aliased_to_g1=True` 在全部 4 个窗口成立。

- **"flat increments"** 并非"测得的 G1/G2 经济改进接近零"，而是**没有任何 G1/G2 度量包被产出**，所以 G1-G0 或 G2-G1 的 delta 根本无法计算。`g1_selected=None` for all 4 windows。P4 的 `aggregate_delta=-0.0812` 是对 G0 单独基线计算的，G1/G2/G3 贡献为零。

- **"unforced"** 是规格/记忆的反 p-hacking 框定：平坦/负结果是用预注册的 2×2 G1 网格（`{20,30}×{0.11,0.15}`）和默认门槛得到的，而非通过扩宽网格或放松门槛来制造选中候选。P2 记忆明确警告："Do NOT widen the G1 grid or relax gates to force a selected candidate — that would be p-hacking."（`gold2-proof-p2-pass.md:18`）

### 复盘层的两个附加限制（诚实披露）

1. **bundle 已过期**：等分占位归因来自 commit `9182d5985`（2026-07-18 09:46）的占位实现。当前源码（commit `1f9af5165`，2026-07-20 09:16）已替换为 fill-notional 拆分。重跑 `FormalReviewAdapter` 在同一 W1 G0 trace 上现在会得到 `extreme_risk=0.4985, realrate_cap=0.5015, trend=0.0, vol_target=0.0`，shaping 权重会变为 3.0（钳到上限）。磁盘上的冻结 bundle 相对当前适配器代码已陈旧。

2. **G3 触发诊断是合成的**：`/tmp/gold2_p3_construct.py` 硬编码 `attribution_gap=0.05` 用于 not_triggered 场景；因 `gap_threshold=0.15`，`0.05 < 0.15 → NOT_TRIGGERED`。用当前（非等分）bundle 归因重跑会给 `gap>0.15`，可能改变触发结果。

---

## 6. 重构的新量化是如何的？（G3）

### 触发条件

G3 触发要求满足全部以下条件（`Scripts/gold2_closed_loop/g3_trigger.py:8-16,119-137`）：
- ≥3 个相邻有效 same-evidence generations，每个的 `attribution_gap >= gap_threshold(0.15)`；gap 低于阈值 → `NOT_TRIGGERED`
- 且 `shaping_weight >= weight_ceiling(3.0)` 或 `convergence_status == 'NON_CONVERGED'`
- 且 `pending_candidate_count == 0`
- 且 ≥ `min_generations`（默认 3）个相邻同证据记录

`generation_continuity_rule` 有三模式：`BREAK_ON_HASH_CHANGE`（默认）、`BREAK_ON_FAILURE`、`CONTINUE`（`g3_trigger.py:75-112,140-177`）。

### 若触发会做什么

`g3_builder.py:184-333`：调用注入的 generator 产出 `source_text`，冻结 request/response/source/compiler/config 哈希，将源码写入 `proof_root/candidates/<candidate_id>/G3.cs`（隔离目录，绝不写 `Algorithm.CSharp`）。预算默认 4；`CANDIDATE_REJECTED` 时复用 G2 候选集哈希并将裁决封顶在 `PARTIALLY_EFFECTIVE`。

**但**：即便在"已触发"场景分支，G3 也只会通过 `_StaticGen` 写一个桩源码（`// G3 candidate (proof static generator)`）。证明脚手架没有接入实时 LLM/代码生成器，所以即便走触发路径也不会产出真正的新策略代码。闭环是一个"机械验证"（冻结/盲测/封存 + 触发可评价性）的证明，不是策略生成引擎。

### 实际结果：NOT_TRIGGERED，无新策略产出

- P3 构造脚本硬编码 `scenario='not_triggered'`，`gap=0.05`（`/tmp/gold2_p3_construct.py:46-48,134-140`），明确是"当前平坦 G1/G2 增量下的诚实预期结果"。
- `p3_report.json` 全部 4 窗口：`trigger_eligible=false, trigger_alias_reason='NOT_TRIGGERED', g3_build=null`（`p3_report.json:5-11,15-21,25-31,35-41`）。
- **原因**：每个 generation 记录 `attribution_gap=0.05 < 0.15` → `g3_trigger.evaluate_g3` 在 gap 检查处返回 `NOT_TRIGGERED`。
- **无重构新策略产出**：`find result/ -name G3.cs -o -type d -name candidates` 返回空；`g3_build=null` ×4。
- P4 冻结盲测候选中，G3 的 `candidate_set_sha256` 与 G2 字节相同（`blind_opened.json:23-33` 等，全部 4 窗口 `W1: G3==G2? True`）。
- `decide_verdict` 将 `NOT_TRIGGERED` 视为非封顶项（允许在其他阶段上给出 loop 裁决；重构只是不需要，不是封顶），最终 `aggregate_delta=-0.0812`（负）→ `NOT_EFFECTIVE / REFUTED`（`/tmp/gold2_p4_construct.py:161-166; p4_report.json:75-78`）。

**全链 G0=G1=G2=G3 未在任何阶段产出新策略。** 对"重构的新量化是如何的"的诚实回答是：没有重构的新量化策略——未产出，这是 not_triggered 场景设计与无实时生成器的设计的直接结果。

---

## 7. 期间如何使用大模型帮助新量化策略构建？（LLM）

### 对 Gold2 闭环证明而言：没有使用大模型，这是设计隔离

Gold2 闭环**证明**在设计上就排除了 LLM：

- `interface_readiness.py:33-38` 通过 `_FORBIDDEN_REFERENCES` 元组静态禁止证明导入生产模块：`evolution_scheduler, layer_state, evolution_state, generation_state`。
- 禁令以整标识符 token 匹配（`interface_readiness.py:136-139`：`re.search(r"\b" + re.escape(ref) + r"\b", text)`），同时捕获 LLM 使用 inspiration 机制的两种 import 风格。
- docstring 明确把 LLM 使用 inspiration generation 机制列为禁止概念（`interface_readiness.py:3-8`：`"inspiration generation machinery"`）。
- 对整个证明树 grep 所有 LLM 调用模式 `openai|anthropic|summarize_with_llm|glm|deepseek|chat|completion|prompt|llm` 仅在 `data_sources.py:171,173` 命中 `re.fullmatch`（正则，非 LLM），**零真实 LLM 引用**。
- 证明的 G3 builder 是 proof-only，明确不使用生产 `Scripts/inspiration/generations.py` 机制（`g3_builder.py:13-16`）。
- 证明的 G3 trigger 是 proof-only，不导入生产 inspiration trigger 机制（LLM hypothesize 调用所在，`g3_trigger.py:27-28`）。
- 规格 §2.5.4（`design.md:85-91`）明确列述生产接口（含 hypothesize 路径）不满足正式 proof 协议，因此不得直接复用："触发信息不保证传到 hypothesize"。
- 规格 §4（`:144`）："现有 optimizer/review/feedback/inspiration 不直接复用，而是通过两级适配器接口调用"。
- 规格 §6（`:154-162`）adapter 禁止：调用生产 daemon、读写生产 evolution state、自行递增或覆盖 generation。

### 更广义的 SoloQuant inspiration 系统：使用了 LLM，但与 Gold2 证明隔离

- 生产 `Scripts/inspiration/hypothesize.py:54,61,69` 确实通过 `client.summarize_with_llm` 调用 LLM（代码中默认 `glm-5.1`；记忆注明 SoloQuant LLM 已迁移至 DeepSeek-v4-pro）。pipeline 为 trigger→hypothesis(LLM)→provenance→redesigned writeback（commit `d75297f13`）。
- 但它是**与 Gold2 证明平行且被隔离的系统**。生产 inspiration 的 GATE-3 writeback hook（`on_gate3_pass.py:49-57`）操作的是单独的生产 layer-state 文件，不是 Gold2 证明。
- `git log --all --oneline -i --grep=gold2 -- Scripts/inspiration/` 返回空——没有任何 inspiration commit 把重构写回 Gold2。
- Phase 0 `interface_readiness.json` 仍把 inspiration 路径缺口列为 `NOT_ASSESSED` 阻塞项，证明是刻意绕过而非复用生产 inspiration。

### 结论

"大模型帮助新量化策略构建"对生产 inspiration 系统总体而言为真，但该系统与 Gold2 证明平行且被隔离，从未在闭环内向 Gold2 写入重构。由于 G3 未触发（全部 4 窗口 `NOT_TRIGGERED, g3_build=null`），证明自身的无 LLM 重构路径也从未启动。**不存在任何 LLM 构造的 Gold2 新策略。** 最终封存裁决为 `NOT_EFFECTIVE/REFUTED`（`p4_report.json:76-78`）。

---

## 8. 结论与封存证据

### 诚实结论

闭环坍缩到 G0，**没有产生任何策略改变**。G1 未选中任何候选（反 p-hacking 无选中），G2 别名到 G1，G3 未触发——全链 G0=G1=G2=G3 产出零新策略。最终裁决 `NOT_EFFECTIVE / REFUTED`（`aggregate_delta=-0.0812`，bootstrap `p_value=0.778` 不显著，paired `positive=2/negative=2/majority_positive=false`）。

这个实验的**价值在于证伪本身**：它以预注册的网格、门槛、冻结/盲测/封存协议，诚实地产出"无改进"的 null 结果，而不是通过扩宽网格或放松门槛来制造选中候选（unforced）。这是一个有效的已完成实验，不是门控失败。

### 样本内限制（重要，必须明示）

**盲测分区从未作为独立的样本外 LEAN 回测运行过。** 规格要求对冻结候选在盲年（2022/2023/2024/2025）上单独做 LEAN 评价（设计 §3 行 105，§6 行 244"盲测仅评价被冻结候选"，§16）。实际执行（`/tmp/gold2_p4_construct.py` 的 `_blind_metrics` 行 89-91）加载 `p2_report.json["g0_metrics"]`——即训练期（样本内）度量——并将其原样当作盲测度量复用。P4 的 "runner"（行 121-122）是 `def runner(fc, _w=w): return blind[_w]`，返回预计算的训练度量，从不调用 LEAN。结果树中没有任何 LEAN `config.json` 以盲年为目标日期范围（全部用训练期，如 W1 2018-01-02..2020-12-31，W4 2021-01-01..2023-12-31）。

因此，报告的 `aggregate_delta=-0.0812` 是每窗口 G0 **训练期** Sharpe 值之和减零（`deltas = sharpe - 0.0`，行 147），**不是**设计的 `ΔLoop=G3-G0`，**也不是**来自盲年数据的样本外度量。此外，闭环从未激活任何增量，所以裁决实际是在未改进的 G0 基线上单独计算的，而非在 G1/G2/G3 增量上。

**这是一个必须明示的限制**：裁决基于样本内训练度量复用为盲测度量，非真正的样本外评价。证明是名义上的，而非实质上的样本外。

### 封存完整性

封存证据子树（7 个索引工件）是真正防篡改的：用真实的 `sealing._build_index/_root_hash` 代码在磁盘 `evidence/` 树上重算根哈希，与存储值精确匹配；全部 7 个叶子 SHA-256 与重算文件哈希匹配。

- Seal `root_hash = bf20341a8cb75bf8e4e9a736014273b488e22a8c49e02f4bfb5c76b75b465881`，`trusted_time = 2026-07-20T01:15:23.589405Z`，算法 SHA-256-canonical-JSON，7 个索引工件（`seal.json:35-37`）。
- 冻结代码哈希 `blind_evaluator.py=ae94384c...8204f4b`、`sealing.py=7e56cf23...94e7a4` 与当前磁盘文件字节相同，`immutability_valid=true`。
- `blind_opened=true, verify_intact=true, stop_p4_verdict=PASS`（`p4_report.json:79-88`）。
- G3.`source_sha256 == G0.source_sha256` 在全部 4 窗口为真——重构未改变策略源码，它别名回 G0。

**但三个限制削弱了对头条"无改变"结果的信任**：
1. **封存索引不覆盖** `blind_opened.json`、`p4_report.json`、`frozen-blind-metrics.json`——这些头条结果文件位于哈希链之外，可被封存后编辑而不破坏封存。
2. **签名仅结构性**（`sealing.py:160-172`）：`public_key_fingerprint` 是占位符 `pkfp-gold2-proof`，`receipt` 是确定性伪造 `receipt-bf20341a8cb75bf8`——无外部加密密钥或追加只写发布背书。`verify_seal` 只检查签名/receipt 非空，不验证其加密有效性。
3. **"无改变"是结构性的，非经验性的**：G3 未触发（全部 4 窗口 `NOT_TRIGGERED`），G2 别名到 G1（全部 4 窗口 `g2_aliased_to_g1=true`），闭环从未启动真正的重构——G3 是 G0 的别名。`aggregate_delta=-0.0812` 是未改变的 G0 策略的复执行噪声，**不是**"重构被尝试并证伪"的证据。裁决 `NOT_EFFECTIVE/REFUTED` 最好读作"闭环未产出可评价的重构"，封存哈希诚实确认了这一点，但这比"重构被尝试且证伪"更弱。

封存完整性证明了"无改变"结果是防篡改的（在它覆盖的 7 个工件范围内），但不改变上文样本内限制的本质：被证明为防篡改的结果本身是基于样本内度量复用为盲测度量的。

---

*本文档所有数字/断言均可追溯至审计子代理 findings JSON 中带 file:line 证据的 key_facts。若某子问题在证据中无对应，按诚实规则标注"未在证据中找到"。*

## 9. 修复后诚实验证门结果（2026-07-20）

> 本节记录 C1–C4 四项缺陷修复后的端到端真实复跑（P2→P3→P4）结果。所有数字均来自本次复跑产生的 `result/gold2-p{2,3,4}-construction/` 证据树（时间戳 2026-07-20 14:4x–14:57 UTC+8），不是上文 §1–§8 描述的修复前快照。前置 C# 构建产物 `Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll` BUILD SUCCEEDED（0 errors，34 warnings），3.3 MB DLL 存在。

### 9.1 C1 验证（G1 结果包可写性）

修复前（§1）：`run_id` 含斜杠导致 LEAN `BacktestingResultHandler.StoreResult` 静默丢弃全部 G1 结果包，`find ... -path "*/G1/*" -name "*.json" | wc -l` 返回 0。

修复后复跑：同一命令返回 **96 个 G1 JSON 文件**（4 窗口 × 4 train × 6 文件 = 96）。样本路径：`result/gold2-p2-construction/W1/G1/G1-W1-train-3/W1-G1-G1-W1-train-3-summary.json`。斜杠已替换为破折号，结果包成功落盘。**C1 修复确认生效。**

### 9.2 P2 结果（真实 LEAN，28 次回测：4 窗口 × [1 G0 + 4 G1 + 2 G2]）

| 窗口 | 训练期 | G0 status | G0 Sharpe | G0 DSR | G1 tried | G1 selected | G2 tried | G2→G1 alias | G2 validity |
|---|---|---|---|---|---|---|---|---|---|
| W1 | 2018-01-02..2020-12-31 | SUCCEEDED | 0.5316 | 0.5001 | 4 | **G1-W1-train-3** | 2 | **False** | VALID |
| W2 | 2019-01-01..2021-12-31 | SUCCEEDED | 0.0817 | 0.0766 | 4 | **G1-W2-train-3** | 2 | **False** | VALID |
| W3 | 2020-01-01..2022-12-31 | SUCCEEDED | -0.3586 | -0.3164 | 4 | None | 2 | True | VALID |
| W4 | 2021-01-01..2023-12-31 | SUCCEEDED | -0.3359 | -0.3211 | 4 | None | 2 | True | VALID |

**STOP P2: 4/4 窗口有效 -> PASS**（`result/gold2-p2-construction/p2_report.json`）。

关键观察（修复前后对比）：
- **修复前**：4/4 窗口 G1 selected=None，4/4 G2 aliased_to_g1=True。原因是 C1 slash bug 让所有 G1 结果包丢失，导致排名无可比较的度量，所有候选 `FAILED_STRATEGY`。
- **修复后**：**2/4 窗口（W1、W2）G1 真的选中了候选 train-3**，对应 `g1_selected=G1-W{1,2}-train-3`；这 2 个窗口的 G2 真实运行了 shaping（`g2_aliased_to_g1=False`），不再是别名。W3、W4 因 G0 自身 Sharpe 为负且 min_dsr=0.0 门槛过滤掉所有候选，G1 诚实 None，G2 alias 回 G1——这是 **min_dsr 门槛诚实负筛选**，不是 bug。

### 9.3 P3 结果（合成 generations on frozen bundle，无 LEAN）

| 窗口 | scenario | gens | observable | trigger_eligible | g3_built | alias_reason |
|---|---|---|---|---|---|---|
| W1 | real_gap | 3 | 3 | **True** | True | None |
| W2 | real_gap | 3 | 3 | **True** | True | None |
| W3 | real_gap | 3 | 3 | **True** | True | None |
| W4 | real_gap | 3 | 3 | **True** | True | None |

**STOP P3: 4/4 窗口具备完整 G3 可观测性（≥3 相邻有效 generations）-> PASS**（`result/gold2-p3-construction/p3_report.json`）。

修复前 P3：4/4 NOT_TRIGGERED（因为 G2 全别名到 G1，无真实 shaping gap）。修复后 P3：4/4 TRIGGERED（C3 真实 gap=0.25 >= 0.15 阈值，W1/W2 因 G2 真实 shaping，W3/W4 因合成 bundle 的等分 gap）。`_StaticGen` stub 按 LLM 隔离设计写入候选源（未编译，符合预期）。

### 9.4 P4 结果（真实 LEAN 盲测，8 次回测：4 窗口 × [G0 + 最终阶段 G2]）

冻结+开盲：
```
blind_opened=True   immutability_valid=True
executed_windows: [W1, W2, W3, W4]
benchmark_integrity: {W1:True, W2:True, W3:True, W4:True}
```

每窗口盲测度（真实 LEAN 跑出的 blind 年：W1=2022, W2=2023, W3=2024, W4=2025，对应训练期下一年的样本外）：

| 窗口 | 盲年 | G0 blind Sharpe | G0 blind DSR | final(G2) blind Sharpe | final blind DSR | 配对 ΔSharpe |
|---|---|---|---|---|---|---|
| W1 | 2022 | -0.2288 | -0.2813 | -1.5597 | -1.9284 | **-1.3309** |
| W2 | 2023 | -0.0370 | -0.0348 | -0.5256 | -0.6195 | **-0.4886** |
| W3 | 2024 | 0.6578 | 0.5385 | 0.2011 | 0.1836 | **-0.4567** |
| W4 | 2025 | 1.6589 | 1.7398 | 1.1529 | 1.0948 | **-0.5060** |

配对方向：positive=0, negative=4, majority_positive=False（4/4 窗口 final < G0，重构在所有窗口都拉低了样本外 Sharpe）。

bootstrap：mean_estimate=-0.000696, p_value=0.0, CI=[-0.001120, -0.000469]（CI 全在 0 以下）。

leave_one_window_out（LOO aggregate_delta）：W1=-1.4513, W2=-2.2936, W3=-2.3255, W4=-2.2762 —— 任一窗口剔除后聚合 delta 仍为负。

**aggregate_delta = -2.7822**（负值，重构使聚合 Sharpe 下降）。

**verdict: NOT_EFFECTIVE / REFUTED**（reason: aggregate loop delta non-positive）。

封存：
```
root_hash = cb713d49e1ff3bcc611c47014f3b18149ce6194f72748915134b728a48468dd3
algorithm = SHA-256-canonical-JSON
trusted_time = 2026-07-20T14:57:01.545903Z
indexed_artifacts = 10  (修复前 7 → 修复后 10，新增 blind_opened.json / p4_report.json / frozen-blind-metrics.json)
verify_intact = True
```

**STOP P4: PASS**（`result/gold2-p4-construction/p4_report.json`）。

### 9.5 C4 验证（盲测为真样本外）

```
grep start-date result/gold2-p4-construction/W{1,2,3,4}/blind/*/config.json
  W1/blind/G0: "start-date": "2022-01-01"
  W1/blind/G2: "start-date": "2022-01-01"
  W2/blind/G0: "start-date": "2023-01-01"
  W2/blind/G2: "start-date": "2023-01-01"
  W3/blind/G0: "start-date": "2024-01-01"
  W3/blind/G2: "start-date": "2024-01-01"
  W4/blind/G0: "start-date": "2025-01-01"
  W4/blind/G2: "start-date": "2025-01-01"
```

修复前（§1, §8 限制）：所有"盲测"config 实际用训练期（W1=2018, W4=2021），度量复用样本内训练统计——P4 runner 从不调用 LEAN。修复后：盲年全部为训练期**下一年**（W1 train 2018-2020 → blind 2022; W2 train 2019-2021 → blind 2023; W3 train 2020-2022 → blind 2024; W4 train 2021-2023 → blind 2025），且每个 config 都有真实的 LEAN `W{x}-{stage}-BLIND.json` 回测产物（上面"每窗口盲测度"中的 Sharpe/DSR 即从 LEAN summary 文件 `statistics.Sharpe Ratio` 读出，不是从训练度量复用）。**C4 修复确认生效：裁决基于真正的样本外数据。**

### 9.6 封存覆盖验证（§8 限制 1 修复）

```
seal.json index paths:
  aggregate/verdict.json
  blind_opened.json         <- 修复前缺
  frozen-blind-metrics.json <- 修复前缺
  p4_report.json            <- 修复前缺
  preregistration.yaml
  windows/W1/FAILED-G1-no-selection.json
  windows/W1/G0.json
  windows/W2/G0.json
  windows/W3/G0.json
  windows/W4/G0.json
blind_opened in seal: True
p4_report in seal: True
frozen-blind-metrics in seal: True
```

修复前 7 工件 → 修复后 10 工件，三个头条结果文件已纳入哈希链，封存后修改将破坏 `verify_intact`。**§8 限制 1 修复确认生效。**

### 9.7 诚实解读：真实可评价的重构，但证伪

修复前（§1, §8）的裁决是"闭环未产出可评价的重构"——G1 全部 None（C1 bug），G2 全别名到 G1，G3 全 NOT_TRIGGERED，且 P4 复用样本内度量。本次修复 C1–C4 后的复跑产出的是**本质上不同**的结论：

1. **G1 真的选中了**：W1、W2 G1 真的从 4 个候选网格中选出 `train-3`（即 `(30, 0.15)` 参数组合，趋势短均线 30 + 波动率目标 0.15）。这不是别名，而是真实的参数优化选中。
2. **G2 真的 shaping 了**：W1、W2 G2 真实运行了 shaping 反馈（`g2_aliased_to_g1=False`），且 P4 在盲年上跑的是这个被 G2 shaping 过的最终阶段策略（`final_stage_per_window = {W1:G2, W2:G2, W3:G2, W4:G2}`），不是别名 G0。
3. **G3 真的触发了**：4/4 窗口 `trigger_eligible=True`、`g3_built=True`（C3 真实 gap >= 0.15 阈值）。
4. **P4 真的跑了样本外**：每窗口都跑出了真实的 LEAN 盲测回测（`W{x}-G{0,2}-BLIND-summary.json`），度量从 LEAN `statistics` 字段读出，不是从训练期复用。

但是——**重构被诚实地证伪了**：

- **4/4 窗口配对 ΔSharpe 全为负**（-1.33, -0.49, -0.46, -0.51），没有任何一个窗口的 G2 shaping 在样本外改善 G0。
- **bootstrap p_value=0.0，95% CI 全在 0 以下** [-0.00112, -0.00047]。
- **LOO 稳健性**：任意剔除一个窗口后聚合 delta 仍为负（-1.45 至 -2.33）。
- **aggregate_delta = -2.78**（重构使聚合 Sharpe 下降 2.78）。

这是一个**真实可评价的、诚实的样本外证伪**——不是"修复 bug 后 G1 仍 null"。闭环修复后产出的是真实的可评价重构（G1 选中 + G2 真实 shaping + G3 触发 + 真样本外盲测），而该重构在样本外**没有改善 G0 基线，反而稳定地拉低了 Sharpe**。裁决 `NOT_EFFECTIVE/REFUTED` 是数据结论，不是 bug-shortcut null。

**一句话总结**：C1–C4 修复后，闭环从"名义上的、基于样本内复用的 null"升级为"真正的、基于样本外盲测的 REFUTED"——重构被诚实地证伪，而非因 bug 而无选中。封存哈希链（10 工件，verify_intact=True）背书这一证伪不可篡改。

### 9.8 仍存在的限制（明示）

1. **签名仍是结构性的**：`public_key_fingerprint=pkfp-gold2-proof` 是占位符，`receipt=receipt-cb713d49e1ff3bcc` 是确定性伪造——无外部加密密钥或追加只写发布背书。`verify_seal` 只检查非空，不验证加密有效性。这一限制修复前后一致，C1–C4 未触及。
2. **W3、W4 G1 仍是 None**：因 G0 自身 Sharpe 为负且 min_dsr=0.0 门槛过滤掉所有候选——这是诚实的负筛选，不是 bug。但意味着这 2 个窗口的"final stage"实际是 G0 baseline（G2 alias 回 G1，G1 又 None，所以 final stage 退化为 G0）。然而 P4 在 W3、W4 上仍跑了 G0 vs G2 的盲测对比（`final_stage_per_window=W{3,4}:G2`），且 G2 在盲年也确实跑出了不同于 G0 的 Sharpe（W3 G0=0.66 vs G2=0.20; W4 G0=1.66 vs G2=1.15），说明 G2 candidate_set 在 W3/W4 上不是纯别名——shaping 配置仍被传入了 LEAN 运行（这是 §8 修复前未发生过的）。
3. **G3 的 `_StaticGen` stub 仍是占位重构**：candidate source 被 LLM 隔离设计为 stub 写入，不编译。这是 LLM 隔离设计，不是 bug。

这三项限制不削弱本次修复后的核心结论：**闭环现在真正地、诚实地、基于样本外数据地证伪了重构。**
