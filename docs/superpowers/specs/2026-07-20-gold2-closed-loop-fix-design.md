# Gold2 闭环量化环境修复 — 设计规格

> **目标**：修复导致"量化策略构建、优化、回测、复盘、重构"闭环**根本未生效**的 4 个串联缺陷，真正完成诚实机械闭环。让闭环的每一环（G0→G1→G2→G3→盲测封存）都**机械可评价**且基于**真实增量 + 真实样本外**，而非被 bug 短路。
>
> **日期**：2026-07-20
> **分支**：`fix/price-scaling-10000x`（worktree `gold2-closed-loop-proof`）
> **范围档**：诚实机械闭环（不含真 LLM 重构；不跨越规格 §6 的 proof/inspiration 隔离边界）

---

## 0. 背景与根因链

`docs/check-gold2-details.md` 的审计结论是：Gold2 闭环"没有产生任何策略改变"，闭环在 G0 处坍缩。审计把首因归于一个 `run_id` 含斜杠的 bug。**但斜杠只是 4 环之一**——即使修了斜杠，G1 仍可能零选中、G2 shaping 仍被丢、G3 gap 仍硬编、P4 盲测仍复用训练度量。闭环未生效的根因是 **4 个独立缺陷串联短路**：

| # | 缺陷 | 位置 | 后果 |
|---|---|---|---|
| **C1** | `candidate_id = f"{stage}-{partition}-{trial}"` 含 `/`（partition=`"W1/train"`）→ `run_id="W1-G1-G1-W1/train-0"` → LEAN `SaveResults` 写 `File.WriteAllText(Path.Combine(run_dir, "W1-G1-G1-W1/train-0.json"))` 父目录不存在 → 异常被 `StoreResult` 的 `catch(Exception){Log.Error}` **吞掉**（`Engine/Results/BacktestingResultHandler.cs:347-350`），结果包静默丢失 → `FAILED_MISSING_PACKET` → G1 全 `FAILED_STRATEGY` | `Scripts/gold2_closed_loop/adapters/parameter_optimizer.py:207` + `/tmp/gold2_p2_construct.py:100,118` + `BacktestingResultHandler.cs:314,337,621` | G1 零选中（16/16 试验失败） |
| **C2** | `g2_runner` 调 `_lean_run(...)` **不传 `params_override`**；C# `Gold2ClosedLoopProofStrategy`（继承 `Gold2BetaVolTargetStrategy`）**不读取** `extreme_risk_contrib_penalty`/`realrate_cap_contrib_penalty` | `/tmp/gold2_p2_construct.py:131-141` + `Algorithm.CSharp/Gold2ClosedLoopProofStrategy.cs`（grep 零匹配）+ `Gold2BetaVolTargetStrategy.cs:92,97-98` | G2 shaping 算出 1.6666 但被丢，G2 行为=G1 |
| **C3** | P3 的 G3 触发 gap **硬编 0.05**（`_scenario_for_window` 合成诊断），不随真实 G1/G2 变化 | `/tmp/gold2_p3_construct.py:134-140,161` | G3 永远 NOT_TRIGGERED，与 G1/G2 真实结果解耦 |
| **C4** | P4 `_blind_metrics()` 直接复用 `p2_report["g0_metrics"]`（训练期/样本内），`runner` 返回预计算值，**从不调用 LEAN** | `/tmp/gold2_p4_construct.py:89-91,121-122,147` | 裁决基于样本内，非真样本外 |

**关键洞察**：C1 是"执行层不产出结果包"，但即使修了 C1，G1 仍可能零选中——因为 `selection.py` 的 `min_dsr=0.0` 门槛会剪掉 W3/W4 的负 sortino 候选（W3 dsr=-0.3164, W4 dsr=-0.3211，见 `check-gold2-details.md` §4）。这是**诚实的**：网格是预注册的、门槛是预注册的，零选中是"unforced null result"，不是 bug。本设计**不碰**网格/门槛——只确保"零选中"是真的因为数据说话，而不是因为 C1 把所有候选的包都丢了。

### 范围档（用户已选）

- **C1+C2+C3+C4 全修**，G3 维持 `_StaticGen` 桩（LLM 隔离，不跨 §6 边界）。
- **不动网格/门槛**（反 p-hacking 硬线）。
- **不动 LEAN 核心 C#**（记忆 `never-modify-lean-native`：修输入而非 LEAN 代码）。
- **不修改任何成熟 feature 的既有行为**（记忆 `never-modify-existing-features`）；C# 改动仅"新增参数读取 + 默认中性"，G0/G1 路径字节不变。

---

## 1. C1 — `run_id` 斜杠 bug 修法

### 1.1 双层修法（全在证明侧 Python，不碰 LEAN C#）

**层 1 — 源头净化**（`Scripts/gold2_closed_loop/adapters/parameter_optimizer.py:207`）：

```python
# 现状：
candidate_id = f"{self._stage_id}-{partition}-{trial_count}"
# partition = "W1/train"（含 /）→ candidate_id = "G1-W1/train-0" → run_id = "W1-G1-G1-W1/train-0"

# 修法：在 candidate_id 构造点净化（partition 字段本身在 journal 里只是字符串，保留原值不影响语义）
safe_partition = partition.replace("/", "-")           # "W1-train"
candidate_id = f"{self._stage_id}-{safe_partition}-{trial_count}"  # "G1-W1-train-0"
```

`partition` 在 journal 里是字符串字段（不是路径），保留 `"W1/train"` 不影响语义；只有当它流进 `candidate_id → run_id → algorithm-id → 包路径` 时才必须文件系统安全。在 `candidate_id` 构造点净化，斜杠永远不进入下游任何路径。

**层 2 — 边界硬守卫**（`Scripts/gold2_closed_loop/lean_runner.py` 的 `build_run_config`）：

```python
if run_id is not None:
    if "/" in run_id or "\\" in run_id:
        raise ValueError(
            f"run_id contains a path separator: {run_id!r}; "
            f"packet path <run_dir>/<run_id>.json would nest into a "
            f"nonexistent subdir and LEAN swallows the write"
        )
    config["algorithm-id"] = run_id
```

即使将来某个上游又漏进斜杠，这里**大声失败**而不是静默丢包。这把 C1 的"静默丢包"永久转成"启动前 raise"——`run_lean` 的"exact packet `<run_dir>/<run_id>.json`"不变量从此被强制。

### 1.2 一致性

`_metrics(run_dir, run_id)` 用 `run_dir / f"{run_id}.json"` 读包（`gold2_p2_construct.py:56,90,106,140`）。因为净化发生在 `candidate_id` 构造点（run_id 的唯一上游），所有地方拿到的 `run_id` 都是净化后的同形式，包路径一致，**无需改 `_metrics`**。

### 1.3 不动的东西

- LEAN 核心 C#：`BacktestingResultHandler.cs:347-350` 的 `catch(Exception){Log.Error}` **不改**为 rethrow（记忆 `never-modify-lean-native`）。
- 网格 `{20,30}×{0.11,0.15}`、`budget=4`、`seed=17`、`min_dsr=0.0`。

---

## 2. C2 — G2 shaping 转发 + 策略真读取

### 2.1 shaping 语义：倍率惩罚，非绝对值替换

复盘反馈的 shaping 语义（`feedback_construction.py:198-201`）是 `weight = clamp(gap/threshold, 0.5, 3.0)`，含义是**对风险封顶项的惩罚强度倍率**：`1.0`=中性（=G0 默认），`<1.0`=放松封顶（允许更大仓位），`>1.0`=收紧封顶（更小仓位）。它**不是**直接替换 `extreme-vol-cap=0.3` 这个绝对值——若把 1.6666 当 cap 用，仓位会被封到 166%，无意义。

所以 C# 侧要把 `contrib_penalty` 当**倍率**接入，作用在"该层风险封顶的有效 cap"上：

```
effective_cap = base_cap / penalty
```

- `penalty=1.0`（默认，G0/G1 路径）→ `effCap = baseCap / 1.0 = baseCap` → **行为与现状逐字节相同**。
- `penalty=1.6666`（G2 转发）→ `effCap = 0.3 / 1.6666 = 0.18` → 极端波动时仓位封顶更保守 → G2 行为偏离 G0/G1。

### 2.2 层 1 — Python：`g2_runner` 转发 shaping

`/tmp/gold2_p2_construct.py:131-141`（生产构造脚本最终应固化到 `Scripts/gold2_closed_loop/`，不在 `/tmp`）：

```python
def g2_runner(req: OptimizationRequest) -> TrialResult:
    cdir = wdir / "G2" / req.candidate_id; cdir.mkdir(parents=True, exist_ok=True)
    ctr = cdir / "trace.jsonl"
    p = req.parameters                       # shaping terms 已在 grid 里(feedback_construction.py:240-258)
    r = _lean_run(cdir, f"{wid}-G2-{req.candidate_id}", "G2",
                  req.candidate_id, wid, _iso(w.train[0]), _iso(w.train[1]),
                  ctr, params_override=p)   # ← 新增：转发 shaping 进 params_override
    ...
```

`g1_runner` 已经在转发 `trend-ma-short`/`vol-target`（`gold2_p2_construct.py:98-99,102`），照搬此模式。

### 2.3 层 2 — C#：`Gold2BetaVolTargetStrategy.Initialize` 读取倍率并注入风险层

`Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:92,97-98`：

```csharp
// 现状：
_extremeCap = GetDecimalParameter("extreme-vol-cap", 0.3m);
AddRiskManagement(new Gold2ExtremeRiskModel(_ext, _gold, GetDecimalParameter("extreme-vol-cap", 0.3m)));
AddRiskManagement(new Gold2RealRateCapModel(_realrate, _gold));

// 修法：读取 shaping 倍率(默认 1.0=中性=G0)，effective_cap = base_cap / penalty
var extremePenalty = GetDecimalParameter("extreme_risk_contrib_penalty", 1.0m);
var realratePenalty = GetDecimalParameter("realrate_cap_contrib_penalty", 1.0m);
var effExtremeCap = Math.Max(0m, _extremeCap / Math.Max(0.0001m, extremePenalty));
var effRealRateCap = Math.Max(0m, GetDecimalParameter("realrate-cap", 0.6m) / Math.Max(0.0001m, realratePenalty));
AddRiskManagement(new Gold2ExtremeRiskModel(_ext, _gold, effExtremeCap));
// realrate 层：在 Model 层缩放 cap，不动 Factor 构造(见 2.4)
AddRiskManagement(new Gold2RealRateCapModel(_realrate, _gold, effRealRateCap));
```

**不变式守恒**（关键论证）：
- G0/G1 运行时**不传**这两个参数 → `GetDecimalParameter(..., 1.0m)` 返回默认 1.0 → `effCap = baseCap / 1.0 = baseCap` → **行为与现状逐字节相同**。成熟 G0/G1 行为不变。
- 只有 G2 运行时（g2_runner 转发 shaping）这俩参数才≠1.0 → cap 被缩放 → G2 行为偏离 G0/G1。
- `Math.Max(0.0001m, penalty)` 防止 penalty=0 导致除零（penalty 来自 clamp(…,0.5,3.0)，实际不会为 0，但防御性编程）。

### 2.4 层 2b — realrate 层：Model 层缩放，不动 Factor

`Gold2RealRateCapModel` 构造器当前只接 `Gold2RealRateCapFactor cap`（`Models/Gold2/Gold2RealRateCapModel.cs:23-27`），而 `risingFastCap` 被烤进 factor 对象（`Common/Factors/Forward/Gold2RealRateCapFactor.cs:25-28`）。

**选择**：在 Model 层缩放 cap，不动 Factor 构造——风险更小，符合"不动成熟 feature"。

```csharp
// Gold2RealRateCapModel 增加一个可选 effRealRateCap 构造重载：
public Gold2RealRateCapModel(Gold2RealRateCapFactor cap, Symbol gold, decimal effRealRateCap)
    : this(cap, gold)
{
    _effRealRateCap = effRealRateCap;   // 若 >0 则覆盖 factor 的 cap 值
}

// ManageRisk 中：capFactor = _effRealRateCap > 0 ? _effRealRateCap : _cap.Compute(...).Value
```

这样 `Gold2RealRateCapFactor` 成熟构造（`risingFastCap=0.6`）不变；G0 路径用旧构造器（无 effRealRateCap）→ 行为不变；G2 路径用新重载传入缩放后的 cap。

### 2.5 层 3 — `GetTunableParameterNames` 不加 shaping 项

`Gold2BetaVolTargetStrategy.cs:171-176` 的 `GetTunableParameterNames` **不加** `extreme_risk_contrib_penalty`/`realrate_cap_contrib_penalty`。它们**不进 G1 搜索空间**（G1 只搜 `trend-ma-short`/`vol-target`，见 `gold2_p2_construct.py:116-117`）。shaping 项是 G2 复盘**派生**的，不是人工旋钮。若加进 `GetTunableParameterNames`，G1 会去搜它们，破坏"G1=2×2 预注册网格"的反 p-hacking界线。shaping 项是**仅 G2 路径**的参数，从 G0/G1 视角透明（默认 1.0）。

---

## 3. C3 — G3 触发从真实 G2 归因导出

### 3.1 当前 bundle 归因已非占位（重要修正）

`check-gold2-details.md` §5 基于的是 P2 运行时（2026-07-18 commit `9182d5985`）冻结的旧 bundle（等分占位）。而当前源码 `formal_review.py:236-308` 的 `_layer_attribution` **已是 fill-notional 拆分**（非等分），会产出 `extreme_risk`/`realrate_cap` 非 0 的真实归因。这意味着 G2 shaping 的 gap 会真实 >0.15，G3 触发判断会真实可触发——C3 的"从真实 G2 归因导出"修法与此对齐。

### 3.2 层 1 — 从真实 bundle 导出 generation gap

`/tmp/gold2_p3_construct.py`（生产路径应固化到 `Scripts/gold2_closed_loop/`）：

```python
def _real_gap_for_window(window_id: str) -> float:
    """从 P2 冻结的 review bundle 读 layer_attribution，
    取 _DEFAULT_TERM_MAP 两个映射层(extreme_risk/realrate_cap)
    的 |pnl_pct_of_total| 最大值作为 generation gap。
    与 feedback_construction.py:198 的 gap 语义一致。"""
    bundle = _load_p2_review_bundle(window_id)   # 读 result/gold2-p2-construction/<W>/review/review-bundle-*.json
    attr = bundle["layer_attribution"]
    mapped = [abs(float(attr[layer]["pnl_pct_of_total"]))
              for layer in ("extreme_risk", "realrate_cap") if layer in attr]
    return max(mapped) if mapped else 0.0

# generation 记录的 gap 用真实值：
real_gap = _real_gap_for_window(wid)
params = [{"gap": real_gap, "weight": 3.0, "pending": 0, "converged": True},
          {"gap": real_gap, "weight": 3.0, "pending": 0, "converged": True},
          {"gap": real_gap, "weight": 3.0, "pending": 0, "converged": True}]
```

**判定流**：
- 真实 `real_gap >= 0.15` → G3 `TRIGGERED`（bundle 归因现已非 0 拆分 + C2 修好后 G2 真有 shaping，gap 大概率 ≥0.15）→ G3 走 `_StaticGen` 桩产出隔离源码（不真编译，符合 §6 LLM 隔离）。
- 真实 `real_gap < 0.15` → `NOT_TRIGGERED`（诚实 null，重构不需要）。

### 3.3 层 2 — `_StaticGen` 桩保持不变

`_StaticGen` 仍只写 `// G3 candidate (proof static generator)` 桩源码（`gold2_p3_construct.py:67-72`）。C3 只让**触发判断**变真，不让**重构产出**变真——后者需要真 LLM，跨越 §6 边界，属已排除的"含真 LLM 重构"档。

**诚实标注**：G3 在"机械可评价性"上变真（触发判断随真实数据），但"产出可运行新策略代码"仍不真。

### 3.4 层 3 — 删除 `scenario='not_triggered'` 硬编

`gold2_p3_construct.py:161` 的 `scenario = "not_triggered"` 删除；gap 从真实 bundle 导出，触发与否由数据决定。`_scenario_for_window` 函数保留（作为 `triggered`/`unobservable` 场景的合成测试用例），但**生产 P3 构造脚本不再调用它的 `not_triggered` 分支**。

---

## 4. C4 — P4 盲测真起 LEAN 跑盲年（样本外）

### 4.1 修法核心：替换注入的 runner

`BlindEvaluator.execute_frozen(window, stage, candidate_set_sha256=, runner=)` 是注入点（`blind_evaluator.py:497-545`）。现状 `runner = def runner(fc,_w=w): return blind[_w]` 返回预计算值从不调 LEAN（`gold2_p4_construct.py:121-122`）。`WindowDefinition.blind` 已定义盲年（W1=2022, W2=2023, W3=2024, W4=2025，`phase0_types.py:23-49`）。

```python
def _blind_runner_factory(windows):
    def runner(fc: FrozenCandidate):
        wdef = next(w for w in windows if w.window_id == fc.window_id)
        blind_dir = OUT / fc.window_id / "blind" / fc.stage_id
        blind_dir.mkdir(parents=True, exist_ok=True)
        trace = blind_dir / "trace.jsonl"
        params = _frozen_params_for(fc)   # G0=默认; G1=selected; G2=shaping; G3=alias
        _lean_run(blind_dir, f"{fc.window_id}-{fc.stage_id}-BLIND", fc.stage_id,
                  fc.candidate_id, fc.window_id,
                  _iso(wdef.blind[0]), _iso(wdef.blind[1]), trace, params_override=params)
        return _metrics(blind_dir, f"{fc.window_id}-{fc.stage_id}-BLIND")
    return runner
```

runner 签名 `Callable[[FrozenCandidate], Any]` 不变，只是实现从"返回字典"换成"调 dotnet Launcher 跑 blind 年 → 从真实包取 portfolioStatistics"。`wdef.blind = (date(2022,1,1), date(2022,12,31))` 等已是真盲年。

### 4.2 修复 delta 语义：`ΔLoop = 最终候选_盲年 − G0_盲年`

现状 `deltas = sharpe - 0.0`（`gold2_p4_construct.py:147`）——减 0 而非减 G0，且只跑 G0。诚实修法：盲测跑两个候选——G0 baseline + 闭环最终候选（G3 alias chain 终点）。

```python
g0_blind = evaluator.execute_frozen(w, "G0", ..., runner=_blind_runner)
final_stage = _resolve_final_stage(p2_report, p3_report)  # G3?G2:G1?G1:G0 别名链终点
final_blind = evaluator.execute_frozen(w, final_stage, ..., runner=_blind_runner)
deltas[w] = final_blind["sharpe"] - g0_blind["sharpe"]      # 真 ΔLoop，样本外
```

**别名链终点的诚实语义**：
- C1+C2 修好后 **G1 真选中** + **G2 真 shaping** → 最终候选=G2 shaped → `delta = G2_盲年sharpe − G0_盲年sharpe`（可能正可能负，真增量）。
- G1 仍零选中（数据说话）→ 别名链终点=G0 → `delta = G0−G0 = 0`（诚实 null，闭环未产出可评价重构）。
- G3 TRIGGERED + `_StaticGen` 桩源码 → 桩不真编译，最终候选仍别名回 G2/G1 → delta 同上（诚实标注 G3 产出未真生效）。

### 4.3 封存索引覆盖修复（`check-gold2-details` §8 限制 1）

现状 `blind_opened.json`/`p4_report.json`/`frozen-blind-metrics.json` 写在 `OUT`（p4 根），不在 `ev_root` 的 `evidence/` 子树下，所以游离在 7-工件封存索引之外（`sealing.py:181-205` 的 `_build_index` 只 walk `ev_root`）。修法：把这三个头条结果文件写到 `ev_root` 子树下（如 `ev_root/aggregate/`、`ev_root/blind/`），让 `build_seal(ev_root, ...)` 自动纳入哈希链。

### 4.4 诚实标注（必须写进文档与报告）

- 盲年回测用 **G0 默认参数 + 冻结 shaping**（不重新优化，否则违反 §14 "盲测之后禁止重选候选"）。
- 盲测只评价**冻结候选**，不在盲年上做任何 G1 重搜/G2 重构——反 p-hacking 硬线。
- 若 G3 `_StaticGen` 桩触发，盲测无法跑"桩源码"（桩不可编译），最终候选别名回 G2/G1——**这是 §6 LLM 隔离的必然代价**，文档明示。
- P3 `sealing.py:160-172` 的签名仅结构性（`public_key_fingerprint` 占位符）——**本设计不修**，属已知的"诚实限制"，在报告中标注。

---

## 5. 测试矩阵

| 测试 | 覆盖 | 断言 |
|---|---|---|
| `test_run_id_no_path_separator` | C1 | `candidate_id` 与 `run_id` 不含 `/`/`\`；`build_run_config` 对含斜杠 run_id `raise ValueError` |
| `test_g1_packet_actually_written` | C1 端到端 | 修斜杠后，至少一个 G1 候选的 `<run_dir>/<run_id>.json` 真存在（不再 FAILED_MISSING_PACKET） |
| `test_g2_shaping_forwarded_and_read` | C2 | g2_runner 的 config.json 含 `extreme_risk_contrib_penalty`；G0 运行时该参数缺失 → C# 默认 1.0 → effCap=baseCap（行为逐字节=G0） |
| `test_g2_behavior_diverges_from_g1` | C2 端到端 | shaping≠1.0 时 G2 目标权重 ≠ G1（同样 grid 点） |
| `test_g3_gap_from_real_bundle` | C3 | P3 generation gap 来自 P2 bundle `layer_attribution`，不再硬编 0.05；`_scenario_for_window` 的 not_triggered 分支不再被生产路径调用 |
| `test_p4_blind_runs_real_lean` | C4 | P4 runner 调 dotnet Launcher 跑 `wdef.blind` 年；config.json 的 start/end=盲年；包来自盲年回测 |
| `test_p4_delta_is_out_of_sample` | C4 | `delta = final_blind_sharpe − g0_blind_sharpe`，非 `sharpe−0` |
| `test_seal_covers_headline_results` | §8限1 | `blind_opened.json`/`p4_report.json`/`frozen-blind-metrics.json` 在 seal.index 内 |
| `test_no_p_hacking_regression` | 反p-hacking | 网格仍={20,30}×{0.11,0.15}、budget=4、min_dsr=0.0、blind 后无重选 |

---

## 6. 错误处理原则：从"静默吞"到"大声失败"

- **C1**：源头净化 + 边界 raise，把"静默丢包"永久转成"启动前 raise"。
- **C2**：C# `GetDecimalParameter(..., 1.0m)` 默认中性——参数缺失不报错（G0/G1 路径透明），只有 G2 转发时才≠1.0。
- **C3**：bundle 缺失/无 layer_attribution → `real_gap=0.0` → NOT_TRIGGERED（诚实 null），不伪造 gap。
- **C4**：盲年 LEAN 失败 → `FAILED_MISSING_PACKET` 透传 → 该窗口 delta 标记为不可评价（不退回训练期复用）。

---

## 7. 诚实验证门（端到端真跑，非合成）

修完 C1-C4 后，**必须真跑一次完整 P2→P3→P4**（每窗口真起 dotnet Launcher，约 16+8+8 次 LEAN 回测），产出真实的：
- G1 是否真选中候选（可能仍 null——数据说话）
- G2 是否真产出 ≠G1 的增量
- G3 是否真触发（gap 来自真实 bundle）
- P4 盲测 delta 是否真样本外

**无论结果是 EFFECTIVE 还是 NOT_EFFECTIVE**，都是诚实的。这是"诚实机械闭环"的真谛：让 null 结果（如果仍 null）是因为数据，而非因为 4 个 bug 把闭环短路了。

---

## 8. 边界（不做的事，明示）

- **不动 LEAN 核心 C#**：`BacktestingResultHandler.cs` 的 `catch` 不改为 rethrow（记忆 `never-modify-lean-native`）。
- **不接真 LLM**：G3 `_StaticGen` 桩不变（§6 隔离）。
- **不动网格/门槛**：`{20,30}×{0.11,0.15}`、`budget=4`、`min_dsr=0.0` 不变（反 p-hacking）。
- **不修改任何成熟 feature 的既有行为**（记忆 `never-modify-existing-features`）；C# 改动仅"新增参数读取 + 默认中性"，G0/G1 路径字节不变。
- **不修 P3 封存签名的结构性占位**（`pkfp-gold2-proof`）——属已知诚实限制，在报告标注。

---

## 9. 文件改动清单

| 文件 | 改动 | 缺陷 |
|---|---|---|
| `Scripts/gold2_closed_loop/adapters/parameter_optimizer.py:207` | `candidate_id` 用 `safe_partition` | C1 |
| `Scripts/gold2_closed_loop/lean_runner.py` (`build_run_config`) | run_id 含斜杠 raise | C1 |
| `/tmp/gold2_p2_construct.py` → 固化到 `Scripts/gold2_closed_loop/` | `g2_runner` 转发 `params_override` | C2 |
| `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:92,97-98` | 读 shaping 倍率 + effCap 缩放 | C2 |
| `Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs` | 新增 effRealRateCap 构造重载 | C2 |
| `/tmp/gold2_p3_construct.py` → 固化 | `_real_gap_for_window` + 删 scenario 硬编 | C3 |
| `/tmp/gold2_p4_construct.py` → 固化 | 真起 LEAN runner + delta 语义 + 封存覆盖 | C4 |
| `Scripts/gold2_closed_loop/sealing.py`（或 p4 构造脚本） | 头条结果文件移入 `ev_root` | §8限1 |
| `Tests/gold2_closed_loop/`（Python 单测，与现有 gold2 闭环测试同目录） | 测试矩阵 §5 | 全部 |

**注**：`/tmp/gold2_p*_construct.py` 是临时构造脚本，最终应固化到 `Scripts/gold2_closed_loop/` 下（如 `construct_p2.py`/`construct_p3.py`/`construct_p4.py`），便于版本控制与复现。固化是实施阶段的工作。

---

*本规格所有断言均可在 worktree `gold2-closed-loop-proof` 内以 file:line 追溯。实施按 writing-plans 产出的计划执行，每个缺陷一个可验证步骤，最终以端到端真跑（§7）验证。*
