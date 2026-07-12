# 复盘反馈优化环路(Review-Feedback Optimization Loop)设计规范

> **状态**:已批准(2026-07-12)。方案 A — StrategyFeedbackAdapter ABC + manifest 声明 + 三层全闭环。gold2 为首个 adapter 试验,接口通用。
>
> **边界说明**:`docs/update2-feedback.md` 澄清本 spec(训练信号优化)与现有 Layer A Bayesian(参数优化)的正交关系。

## 0. 目标与约束

### 0.1 目标(用户确认)

把复盘(review)层的输出(review.json layer 归因 / per-trade narrative / drawdown attribution / TCA + sidecar review.last_review.json)反馈到 auto-evolution 管线,提升量化策略效果:

1. **机器闭环优化**(本 spec):review_status/归因 gap → evolution_scheduler 第 5 触发器 + reward shaping 新 term + CQL observation 扩展。闭环:背测→复盘→反馈→重训 CQL→改 alpha。
2. **启发新策略**(下一个 spec,本 spec 不做):review 归因 → create-strategy GATE 3 brainstorming + LLM 提示。

本 spec 只做 1(优化轨)。双轨拆分,启发轨留后续 spec。

### 0.2 硬约束

- **通用接口**:StrategyFeedbackAdapter ABC + manifest `feedback:` 段声明。gold2 是首个 adapter,但其他策略可加。
- **全闭环**:信号生成 + evolution_scheduler 触发器 + reward shaping + CQL observation 扩 13 维。
- **零 LEAN native 改动**:feedback 层全 Python `Scripts/feedback/`;唯一 C# 改动是 gold2 策略侧 SerializeRlState 补 2 字段(`Algorithm.CSharp/`,非 LEAN core)。
- **与 Layer A 正交**:不碰 `bayesian_optimizer.py` / `parameter_space`(参数优化保持原样)。

### 0.3 与现有参数优化的区别(详见 `docs/update2-feedback.md`)

> **Bayesian 优化策略参数(标量空间,全局,DSR 目标);复盘优化训练信号(reward + observation,state→action 级,层归因目标)。两者正交:Bayesian 选参数,复盘让 RL 学行为。**

不碰 Layer A;只碰 Layer C 上下游:`offline_rl_trainer` reward 源、`trace_to_mdp_dataset` observation/reward、`evolution_scheduler` 触发器。

---

## 1. 架构 + 数据流

### 1.1 目录结构

全部 greenfield Python 放 `Scripts/feedback/`(与 `Scripts/review/`、`Scripts/auto_optimize/` 同级),C# 改动仅 gold2 策略侧:

```
Scripts/feedback/
  __init__.py
  adapters/
    __init__.py
    base.py              # StrategyFeedbackAdapter ABC + FeedbackAction
    gold2.py             # Gold2FeedbackAdapter
  signals.py             # feedback_signal orchestrator (load adapter, build FeedbackAction)
  reward_injector.py     # apply shaping_overrides to trace_to_mdp_dataset
  observation_injector.py # apply observation_fields to _encode_state
```

### 1.2 核心抽象

**`FeedbackAction`** dataclass(`adapters/base.py`):
```python
@dataclass
class FeedbackAction:
    trigger: bool                    # True → evolution_scheduler review_drift 触发重训
    trigger_reason: str              # "review_status=fail" | "layer_gap extreme_risk=0.32>0.15" | ""
    shaping_overrides: dict          # {term_name: weight} e.g. {"extreme_risk_contrib_penalty": 2.0}
    observation_fields: list[str]    # state_schema 字段子集,传给 _encode_state
    attribution_method: str          # "telescoping" | "residual"(从 review.json 透传)
    per_bar_layer_contrib: list      # 下采样到 per-bar 的层贡献(供 reward 用),空则不用
```

**`StrategyFeedbackAdapter` ABC**:
```python
class StrategyFeedbackAdapter(ABC):
    LAYERS: list[str]   # 与 StrategyReviewAdapter.LAYERS 一致

    @abstractmethod
    def feedback_signal(self, manifest, review_doc: dict, state_trace: list) -> FeedbackAction:
        """读 review.json (layer 归因/gap/status) + state_trace (per-bar 层状态)
        → 产 FeedbackAction。纯函数,无副作用。"""
```

通用协议:review 层不感知策略内部;adapter 读 manifest `feedback:` 段 + review_doc 产 FeedbackAction。

### 1.3 端到端数据流(全闭环)

```
背测 (LEAN, gold2 SerializeRlState callsite 已接, RL_TRACE_PATH 触发写盘)
  → state_trace.jsonl (per-bar 13 字段) + review.json (layer 归因) + sidecar
  → signals.orchestrate(manifest): 合并 review.json + sidecar → review_doc
  → adapter.feedback_signal(manifest, review_doc, state_trace)
  → FeedbackAction {trigger, shaping_overrides, observation_fields, per_bar_layer_contrib}
  → 三层注入:
     (1) evolution_scheduler.check_triggers 加 review_drift (读 FeedbackAction.trigger)
     (2) trace_to_mdp_dataset._compute_reward 读 manifest feedback.shaping_term_map
         + per-bar state_trace 层字段 → 新 term layer_contrib_penalty
     (3) trace_to_mdp_dataset._encode_state 读 manifest feedback.observation_fields
         (gold2 13 维, 替代硬编码 8 维)
  → CQL 重训 (observation 13 维 + reward 含 layer_contrib_penalty)
  → ONNX → deploy_gate (manual)
```

### 1.4 gold2 state_trace callsite + 路径约定(依赖项)

gold2 `SerializeRlState`(`Gold2BetaVolTargetStrategy.cs:136`)已写 11 字段,`OnData` 末尾 `WriteRlStateTraceIfNeeded(data.Time)` callsite 已接(Task 3 review-layer 实现)。背测时设 `RL_TRACE_PATH` env 产 state_trace.jsonl,解锁 per-bar 归因。

**state_trace 路径约定(eval-update2 衔接点修复)**:为避免"写入方靠 env var、读取方靠约定"的隐式双约定(踩过 CLI --parameters 被 LEAN 忽略同类坑),统一约定:

- **背测产出路径固定**:`RL_TRACE_PATH` 背测时指向 `Results/<strategy_name>/state_trace.jsonl`(与 review.json 同 results 目录)。
- **`orchestrate(manifest, results_dir)` 按约定拼路径读**:`Path(results_dir) / "state_trace.jsonl"`,不依赖 env var 在重训时还存在(重训是另一进程/另一次调用,env var 可能已失)。
- **`alpha_perturbation_runner` 配合**:`run_perturbed_backtest` 的 `trace_output_path` 参数已接受自定义路径(`alpha_perturbation_runner.py:39`),改为指向约定路径。`merge_traces` 产 `state_trace_diverse.jsonl`(多 perturbation 合并)时也落在同目录。
- **校验**:`manifest_lint` 加一条 warning(非 error)若 `feedback.observation_fields` 非空但 `Results/<strategy>/state_trace.jsonl` 不存在(预示 callsite 未接或 env 未设)。

需补 2 字段(`drawdown`/`pnl`,见 §3.5)→ state_schema dim_hint 11→13。

### 1.5 零 LEAN native 改动保证

feedback 层全 Python。唯一 C# 改动是 gold2 策略侧 SerializeRlState 补 `drawdown`/`pnl` 2 字段(策略侧,非 LEAN core)。

---

## 2. manifest `feedback:` 段 + gold2 声明

### 2.1 通用 `feedback:` 段(与 `review:` 平级,raw-passthrough)

新顶层 key,仅 `feedback` 模块 + evolution_scheduler 消费,**不加 dataclass**(匹配 `review:`/`cpcv:` precedent):

```yaml
feedback:
  adapter_module: feedback.adapters.gold2      # importlib 解析(Scripts/feedback/ sys.path bootstrap)
  adapter_class: Gold2FeedbackAdapter
  # 触发器层:review_status / 归因 gap → evolution_scheduler review_drift 触发器
  trigger_thresholds:
    review_status_fail: true                   # review_status == "fail" → trigger
    max_layer_attribution_gap: 0.15            # 任意 |layer.pnl_pct_of_total| > 此值 → trigger
    min_narrative_trades: 20                   # review 交易数 < 此 → warn(不 trigger)
  # reward shaping 层:layer → shaping term 映射
  shaping_term_map:
    extreme_risk: extreme_risk_contrib_penalty # 层名 → shaping term 名
    realrate_cap: realrate_cap_contrib_penalty
  # observation 层:CQL observation 读哪些 state_schema 字段
  observation_fields: [tpv, cash_pct, w_smooth, trend_dir, extreme_triggered,
                       realrate_cap, dir_coef, w_after_vol, extreme_cap, trend_disabled,
                       drawdown, pnl, t]
```

### 2.2 新 shaping term(通用,注册到 gym_env + trace_to_mdp_dataset)

`layer_contrib_penalty` — 通用 term,对**保护性质层**(extreme_risk/realrate_cap)的负贡献做**非对称加权惩罚**:
```
layer_contrib_penalty = weight * sum(
    -C_layer_bar  for layer in {extreme_risk, realrate_cap}
    if C_layer_bar < 0
)
```
**设计意图(eval-update2 #1 澄清)**:这是**有意的非对称风险惩罚**,不是"避免 double-count"。理由:extreme_risk/realrate_cap 两层本应是**保护性质**(cap 在极端 regime 降仓以避险)。这两层出现负贡献,意味着保护逻辑在亏损 market 里仍在持仓(即保护**失效**),比 trend/vol_target 层的普通亏损更值得加权惩罚 — 后者是策略主动承担的风险,前者是保护机制失灵。故只对这两层负贡献额外惩罚;正贡献不额外奖励(保护层盈利是其本职,不应诱导 RL 为追求保护层盈利而降仓)。

base reward 的 `scaled_pnl` 已含所有层总 PnL(含这两层负贡献),penalty 是**叠加的非对称加权**,目的是让 RL 对保护层失效更敏感。这与 `drawdown_excess_penalty`(对总回撤额外惩罚)是同类设计 — 都是有意的非对称风险厌恶,非"避免重复计算"。

### 2.3 reward 源统一(顺带修 divergent 缺陷)

当前 `manifest.reward_config.shaping`(2 terms,PPO)与 `config.yaml:offline_rl.reward_terms`(4 terms,CQL)divergent。本 spec 统一:
- **manifest `reward_config.shaping` 为 source of truth**
- `offline_rl_trainer.py:92-94` 改为读 `manifest.reward_config.shaping`(不再读 `config.yaml:offline_rl.reward_terms`)
- `config.yaml:offline_rl` 只留算法超参(`algorithm/var_budget/max_dd/fqe_n_steps`)
- `feedback.shaping_term_map` 注入的 term **合并进** shaping 列表(adapter 产 `shaping_overrides` 权重)

### 2.4 gold2 manifest 完整 `feedback:` 段

```yaml
feedback:
  adapter_module: feedback.adapters.gold2
  adapter_class: Gold2FeedbackAdapter
  trigger_thresholds:
    review_status_fail: true
    max_layer_attribution_gap: 0.15
    min_narrative_trades: 20
  shaping_term_map:
    extreme_risk: extreme_risk_contrib_penalty
    realrate_cap: realrate_cap_contrib_penalty
  observation_fields: [tpv, cash_pct, w_smooth, trend_dir, extreme_triggered,
                       realrate_cap, dir_coef, w_after_vol, extreme_cap, trend_disabled,
                       drawdown, pnl, t]
```

---

## 3. StrategyFeedbackAdapter ABC + gold2 feedback_signal 数学

### 3.1 ABC + FeedbackAction(`Scripts/feedback/adapters/base.py`)

```python
@dataclass
class FeedbackAction:
    trigger: bool                    # True → evolution_scheduler review_drift 触发重训
    trigger_reason: str              # "review_status=fail" | "layer_gap extreme_risk=0.32>0.15" | ""
    shaping_overrides: dict          # {term_name: weight} e.g. {"extreme_risk_contrib_penalty": 2.0}
    observation_fields: list[str]    # state_schema 字段子集,传给 _encode_state
    attribution_method: str          # "telescoping" | "residual"(从 review.json 透传)
    per_bar_layer_contrib: list      # 下采样到 per-bar 的层贡献(供 reward 用),空则不用


class StrategyFeedbackAdapter(ABC):
    LAYERS: list[str]   # 与 StrategyReviewAdapter.LAYERS 一致

    @abstractmethod
    def feedback_signal(self, manifest, review_doc: dict, state_trace: list) -> FeedbackAction:
        """读 review.json (layer 归因/gap/status) + state_trace (per-bar 层状态)
        → 产 FeedbackAction。纯函数,无副作用。"""
```

### 3.2 触发器层逻辑(gold2)

`feedback_signal` 触发判定(读 manifest `feedback.trigger_thresholds`)。

**`review_doc` 参数定义**:orchestrator(`signals.py`)合并 review.json + sidecar `review.last_review.json` 后传入的 dict。合并规则:`review_doc = {**review_json_content, "review_status": sidecar["review_status"], "last_review": sidecar["last_review"]}`。adapter 不直接读 sidecar 文件。

**样本量前置 gate**(eval-update2 #3 修复):`min_narrative_trades` 是 layer_gap 的**前置门控**,不是独立判据。样本不足时 layer_gap 整体跳过(小样本噪声不应触发重训),只保留不依赖样本量的 `review_status=fail` 硬失败判据。

```
trigger = False
reasons = []
# (1) 硬失败:不依赖样本量,始终生效
if review_doc.get("review_status") == "fail":               # review_status_fail: true (来自 sidecar)
    trigger = True; reasons.append("review_status=fail")
# (2) 样本量前置 gate:不足时跳过 layer_gap(避免小样本噪声触发重训)
n_trades = len(review_doc.get("per_trade_narrative", []))
min_trades = thresholds.get("min_narrative_trades", 20)
if n_trades < min_trades:
    reasons.append(f"warn: n_trades={n_trades}<{min_trades}, layer_gap skipped (small-sample)")
else:
    # (3) layer_gap:仅在样本充足时生效
    for layer, agg in review_doc["layer_attribution"].items():
        if abs(agg["pnl_pct_of_total"]) > thresholds["max_layer_attribution_gap"]:  # 0.15
            trigger = True; reasons.append(f"layer_gap {layer}={agg['pnl_pct_of_total']}>0.15")
```

### 3.3 reward shaping 层(per-bar 层贡献下采样)

**关键**:review.json 的 layer_attribution 是 **per-trade** 聚合,reward 需要 **per-bar**。adapter 用 state_trace 的 per-bar 字段重算 per-bar 层贡献。

gold2 telescoping per-bar(同 review §3.2 公式,但 Δp 用 bar 间 close 变化):
```
对每个 state_trace 行 t (有 dir_coef, w_after_vol, extreme_triggered, extreme_cap, realrate_cap, tpv):
  scale_t   = tpv_t / close_{t-1}
  w_trend   = dir_coef
  w_vol     = w_after_vol
  w_ext     = extreme_triggered ? min(w_vol, extreme_cap) : w_vol
  w_real    = min(w_ext, realrate_cap)
  dp_bar    = close_t - close_{t-1}      # bar 间价格变化
  C_trend_bar        = (w_trend * scale_t) * dp_bar
  C_vol_target_bar   = (w_vol - w_trend) * scale_t * dp_bar
  C_extreme_risk_bar = (w_ext - w_vol) * scale_t * dp_bar
  C_realrate_bar     = (w_real - w_ext) * scale_t * dp_bar
```
per-bar 求和 == per-trade 归因 telescoping 不变式(同 review spec §3.2,bar 级 telescoping)。

**新 shaping term `layer_contrib_penalty`**(注册到 `gym_env.eval_term` + `trace_to_mdp_dataset._compute_reward`)— 仅对 `shaping_term_map` 声明的**保护性质层**(gold2: extreme_risk/realrate_cap)的负贡献做非对称加权惩罚(设计意图见 §2.2):
```
layer_contrib_penalty = weight * sum(
    -C_layer_bar  for layer in shaping_term_map.keys()
    if C_layer_bar < 0
)   # 仅保护层负贡献;正贡献不奖励(保护层盈利是本职,非对称风险惩罚)
```
**代际权重溯源**(eval-update2 #2):每代重训实际生效的 shaping 权重是**非平稳**的(逐轮由 review gap 决定)。`FeedbackAction.shaping_overrides` 必须写入训练 log + `.feedback.json`(已含),`offline_rl_trainer` 把生效的 `{term: weight}` 落入 `Results/auto_optimize/<strategy>/generation_<N>_shaping.json`,供回溯"这一代变好/变差是策略学到东西还是 reward 变了"。版本间不可直接比 FQE 分数,需同代际 shaping 对比。

`shaping_overrides` = `{layer_term: weight}`,权重由 adapter 按 review 归因 gap 强度定:
```
weight = clamp(abs(agg["pnl_pct_of_total"]) / gap_threshold, 0.5, 3.0)
# extreme_risk gap=0.32, threshold=0.15 → weight=2.13
```

### 3.4 observation 层

`feedback.observation_fields`(manifest 声明)传给 `trace_to_mdp_dataset._encode_state`,替代硬编码 8 维:
```python
def _encode_state(s, observation_fields):
    return [float(s.get(f, 0)) for f in observation_fields]
```
gold2 13 维:`[tpv, cash_pct, w_smooth, trend_dir, extreme_triggered, realrate_cap, dir_coef, w_after_vol, extreme_cap, trend_disabled, drawdown, pnl, t]`。`drawdown/pnl/t` 需 state_trace 含(OptionVolArb 已有,gold2 需 SerializeRlState 补 — 见 §3.5)。

### 3.5 gold2 state_trace callsite + 补 drawdown/pnl 字段

Task 3 review-layer 已接 gold2 `WriteRlStateTraceIfNeeded` callsite(OnData 末尾)。但 SerializeRlState 当前 13 字段**缺 drawdown/pnl**(只有 tpv/cash_pct/positions/w_smooth/trend_dir/extreme_triggered/realrate_cap/dir_coef/w_after_vol/extreme_cap/trend_disabled = 11)。需补 2 字段:
- `drawdown` — 需 gold2 策略维护 `_peakTpv`(镜像 OptionVolArb5LayerStrategy.cs:253)
- `pnl` — per-bar pnl = `(tpv_t - tpv_{t-1}) / tpv_{t-1}`(SerializeRlState 里算,需存 `_prevTpv`)

gold2 `Gold2BetaVolTargetStrategy.cs` 加 `_peakTpv/_prevTpv` 字段 + OnData 末尾更新 + SerializeRlState 补 `drawdown/pnl` 2 字段 → state_schema dim_hint 11→13,manifest `feedback.observation_fields` 13 维齐。

### 3.6 gold2 Gold2FeedbackAdapter

```python
class Gold2FeedbackAdapter(StrategyFeedbackAdapter):
    LAYERS = ["trend", "vol_target", "extreme_risk", "realrate_cap"]

    def feedback_signal(self, manifest, review_doc, state_trace):
        fb = manifest.raw.get("feedback", {})
        thresholds = fb.get("trigger_thresholds", {})
        term_map = fb.get("shaping_term_map", {})
        obs_fields = fb.get("observation_fields", [])

        # 触发器
        trigger, reasons = self._check_triggers(review_doc, thresholds)

        # per-bar 层贡献下采样(state_trace 缺失 → 空,shaping 不生效)
        per_bar = self._downsample_per_bar(review_doc, state_trace) if state_trace else []

        # shaping_overrides:按 gap 强度定权重
        shaping = {}
        for layer, term in term_map.items():
            agg = review_doc["layer_attribution"].get(layer, {})
            gap = abs(agg.get("pnl_pct_of_total", 0))
            if gap > thresholds.get("max_layer_attribution_gap", 0.15):
                shaping[term] = min(max(gap / 0.15, 0.5), 3.0)

        return FeedbackAction(
            trigger=trigger, trigger_reason="; ".join(reasons),
            shaping_overrides=shaping, observation_fields=obs_fields,
            attribution_method=review_doc.get("run_meta", {}).get("attribution_method", "residual"),
            per_bar_layer_contrib=per_bar,
        )
```

### 3.7 降级路径

- **无 state_trace**(gold2 callsite 未接或 RL_TRACE_PATH 未设):`per_bar_layer_contrib=[]`,shaping term `layer_contrib_penalty` 不生效(=0),observation 仍可扩(读 state_trace 行,行缺失则字段填 0)。触发器仍工作(只依赖 review.json)。
- **review 残差模式**(`attribution_method=residual`):per-trade 归因 trend=全 PnL/其余=0,下采样后只 trend 层有 per-bar 贡献,`layer_contrib_penalty` 只惩罚 trend 负贡献(粗糙但安全)。

---

## 4. evolution_scheduler 第 5 触发器 + reward/observation 注入

### 4.1 触发器层:evolution_scheduler.check_triggers 加 review_drift

`evolution_scheduler.py:43-77` 现有 4 触发器。加第 5 个 `review_drift`,读 sidecar `review.last_review.json` + 调 adapter.feedback_signal:

```python
# evolution_scheduler.py check_triggers 内,line 75 后加:
# 5. review_drift (复盘反馈触发器, spec §4.1)
# signals.orchestrate(manifest) 合并 review.json + sidecar → review_doc, load adapter, build FeedbackAction
from feedback.signals import orchestrate as orchestrate_feedback
review_path = pathlib.Path(results_dir) / manifest.strategy_name / "review" / "review.last_review.json"
if review_path.exists():
    fb_cfg = manifest.raw.get("feedback", {})
    if fb_cfg:
        action = orchestrate_feedback(manifest, results_dir)  # 内部读 review.json + sidecar + state_trace
        triggers["review_drift"] = action.trigger
        if action.trigger:
            # 把 shaping_overrides + observation_fields 写入临时文件,供 fire_optimization 读
            _write_feedback_action(action, state_path + ".feedback.json")
    else:
        triggers["review_drift"] = False
else:
    triggers["review_drift"] = False
```

`fire_optimization` 在 Layer C 前读 `.feedback.json`,把 `shaping_overrides` 注入 reward 源、`observation_fields` 注入 `_encode_state`。

### 4.2 reward 源统一 + shaping_overrides 注入

**当前 divergent**:`offline_rl_trainer.py:92-94` 读 `config.yaml:offline_rl.reward_terms`(4 terms);`ppo_trainer.py:9` 读 `manifest.reward_config.shaping`(2 terms)。

**统一**(spec §2.3):manifest `reward_config.shaping` 为 source of truth。

`offline_rl_trainer.py` 改:
```python
# line 92-94 原:
# reward_terms = orl.get("reward_terms", [...])
# 改为读 manifest reward_config.shaping + feedback shaping_overrides 合并:
shaping = [{"term": t.term, "weight": t.weight} for t in manifest.reward_config.shaping]
# 合并 feedback shaping_overrides(覆盖同 term 权重,或追加新 term)
feedback_action = _load_feedback_action(state_path + ".feedback.json")
for term, w in feedback_action.shaping_overrides.items():
    shaping = [s if s["term"] != term else {"term": term, "weight": w} for s in shaping]
    if not any(s["term"] == term for s in shaping):
        shaping.append({"term": term, "weight": w})
reward_terms = shaping
```

`config.yaml:offline_rl.reward_terms` **删除**(只留 `algorithm/var_budget/max_dd/fqe_n_steps`)。`gym_env.eval_term` + `trace_to_mdp_dataset._compute_reward` 注册新 term `layer_contrib_penalty`(读 `feedback_action.per_bar_layer_contrib`)。

### 4.3 observation 注入

`trace_to_mdp_dataset.py:16-22` `_encode_state` 改为读 manifest `feedback.observation_fields`:
```python
def _encode_state(s, observation_fields=None):
    if observation_fields is None:
        observation_fields = ["tpv","cash_pct","var_1d99","var_regime","drawdown","n_open_positions","pnl","t"]  # 原 8 维 fallback
    return [float(s.get(f, 0.0)) for f in observation_fields]
```
`offline_rl_trainer.py` 把 `feedback_action.observation_fields` 透传给 `trace_to_transitions` → `_encode_state`。CQL `policy_exporter --obs-dim` 从硬编码 8 改为读 manifest(obs_dim = len(observation_fields))。

### 4.4 数据流总结

```
check_triggers
  → adapter.feedback_signal(manifest, review_doc, state_trace)
  → FeedbackAction
  → 写 state_path + ".feedback.json"
fire_optimization
  → Layer A (Bayesian, 不变)
  → Layer C (offline_rl_trainer 读 .feedback.json):
      reward_terms = manifest.reward_config.shaping ∪ feedback.shaping_overrides
      observation_fields = feedback.observation_fields
      _compute_reward 用 per_bar_layer_contrib 算 layer_contrib_penalty
      _encode_state 读 observation_fields (gold2 13 维)
  → policy_exporter --obs-dim 13
  → FQE (用同 observation_fields)
  → ONNX → deploy_gate (manual)
```

---

## 5. 测试 + 已知局限

### 5.1 单测

- **FeedbackAction / ABC 合规**(`test_feedback_base.py`):MockAdapter 返回合法 FeedbackAction;BadAdapter(trigger 无 reason)抛错。
- **gold2 feedback_signal 触发器**(`test_feedback_gold2.py`):
  - review_status=fail → trigger=True
  - layer gap extreme_risk=0.32>0.15 → trigger=True,reason 含 "extreme_risk=0.32"
  - 全 pass + 小 gap → trigger=False
  - min_narrative_trades<20 → layer_gap **跳过**(不 trigger);若同时 review_status=fail 仍 trigger(eval-update2 #3 回归测试)
  - 小样本 + 大噪声 gap(n_trades=5, extreme_risk gap=0.32)→ **不 trigger**(layer_gap 被前置 gate 拦)
- **per-bar 层贡献下采样**(`test_feedback_gold2.py`):合成 state_trace + close 序列 → per-bar 求和 == per-trade telescoping 归因(同 review §3.2 不变式,bar 级)。无 state_trace → per_bar=[]。
- **shaping_overrides 权重**:`gap=0.32,threshold=0.15 → weight=clamp(0.32/0.15,0.5,3.0)=2.13`。
- **reward 源统一**(`test_reward_injection.py`):manifest shaping 2 terms + feedback overrides 1 term → 合并 3 terms;同 term 权重覆盖。
- **observation 注入**:`_encode_state` 读 13 字段,缺失字段填 0。
- **evolution_scheduler review_drift 触发器**(`test_review_drift_trigger.py`):mock sidecar + adapter → triggers["review_drift"] 正确;无 feedback 段 → False;_check_and_fire 触发 fire_optimization。
- **reward term layer_contrib_penalty**:`gym_env.eval_term` + `trace_to_mdp_dataset._compute_reward` 注册,per-bar 负贡献求和 ×weight。

### 5.2 端到端集成

- **gold2 全闭环 e2e**(`test_feedback_e2e.py`):
  - 跑 gold2 背测 with `RL_TRACE_PATH`(产 13 维 state_trace)
  - 跑 review → review.json + sidecar
  - 跑 feedback adapter → FeedbackAction
  - 跑 offline_rl_trainer 读 .feedback.json → CQL 训练(observation 13 维 + reward 含 layer_contrib_penalty)
  - FQE 评估 → beats_lower_bounds
  - policy_exporter --obs-dim 13 → ONNX
- **降级 e2e**:无 state_trace(gold2 callsite 未接)→ per_bar=[],layer_contrib_penalty=0,observation 仍 13 维(字段填 0),CQL 训练仍跑通,不崩。

### 5.3 已知局限

1. **依赖 gold2 state_trace callsite**:per-bar 层贡献 + observation 13 维需 state_trace.jsonl。gold2 SerializeRlState 已写 11 字段,需补 drawdown/pnl 2 字段(§3.5)。callsite 已接(Task 3 review-layer),路径约定见 §1.4(固定 `Results/<strategy>/state_trace.jsonl`)。未产文件 → 降级。
2. **per-bar 归因近似**:bar 级 telescoping 用 close_t - close_{t-1} 作 Δp,与 per-trade(EntryPrice→ExitPrice)求和严格相等需 state_trace 覆盖每 bar;若 state_trace 缺 bar(hold bar 未写)→ 求和有残差,`layer_contrib_penalty` 近似。spec 标注 `attribution_method` 让下游知晓精度。
3. **reward 源统一破坏 PPO**:当前 `ppo_trainer.py:9` 读 manifest shaping(2 terms);统一后 manifest 是 source of truth,PPO 自动跟随。但 `config.yaml:offline_rl.reward_terms` 删除会 break 旧调用方(若有 hardcoded 依赖)— 需 grep 确认无其他消费者。
4. **CQL observation 维度变更不向后兼容**:旧 ONNX(obs_dim=8)与新 CQL(obs_dim=13)不兼容;deploy_gate 需校验 ONNX obs_dim == manifest observation_fields 长度,否则拒绝部署。
5. **触发器非阻断**:review_drift trigger 触发重训,但 deploy_gate 仍 manual;review_status=fail 不阻断部署(保持 review 层 non-blocking 设计)。block_deploy 仍由 manifest `review.block_deploy` 控制(默认 false)。
6. **gold2 是首个 adapter**:其他策略无 feedback adapter → triggers["review_drift"]=False,feedback 段缺失警告进 manifest_lint.warnings(非 error)。
7. **alpha_perturbation 不联动**:本 spec 不改 `alpha_perturbation_runner.py`(固定 Beta 采样);feedback 不喂 perturbation 分布。留作后续。
8. **归因窗口与训练窗口重合的过拟合风险(eval-update2 #4,未本 spec 解决,标注待定)**:per-bar 层贡献从**同一次**回测的 state_trace 重算,若该 trace 就是 CQL 训练样本来源,等于"模型在某段路径某层表现差 → 惩罚模型在那段路径做过的动作 → 重训"。若该"差"只是特定市场状态的正常噪声(非可泛化 regime 模式),则是对单次实现路径过拟合,与项目 Gate 验证/样本外稳健性原则冲突。**本 spec v1 不分离诊断期与训练期**(工程复杂度高);作为缓解,(a) 代际 shaping 权重溯源(见 §3.3 #2)让过拟合可被察觉;(b) `min_narrative_trades` 前置 gate(§3.2)过滤小样本噪声;(c) **建议下一 spec 引入滚动 OOS 诊断期**(review 期 = 训练窗口之外的最近一段),让 reward shaping 来自与训练窗口不完全重合的路径。实现 `Gold2FeedbackAdapter` 前需用户拍板:是否本 spec 内分离窗口(增加复杂度)还是留下一 spec(接受 v1 过拟合风险 + 缓解措施)。
9. **champion/challenger 影子部署未接(eval-update2 #5,留下一 spec)**:本 spec 自动触发重训但 `deploy_gate` 仍 manual,无 champion-challenger 影子对比机制。自动化程度越高,人工 gate 越易变形式主义("看 FQE 分数过了就点头")。**建议紧接本 spec 的下一个 spec 补 champion/challenger 影子部署**(新 policy 先影子对比 champion,达胜率阈值才升 champion),否则本闭环跑起来后缺口风险放大。

### 5.3.1 deploy_gate 人工核对 checklist(v1 必需,低成本兜底)

**背景(eval-update2 #4+#5 组合风险)**:本 spec v1 接受归因窗口与训练窗口重合的过拟合风险(§5.3 #8,缓解三招可观测),且 champion/challenger 影子部署推到下一 spec(§5.3 #9)。两者同时 delay = "reward 可能背答案 + 没人在部署前看一眼" 同时成立 — 不可接受。故在完整 champion/challenger 落地前,给 `deploy_gate` 的 manual review 加一条**必需的低成本人工核对点**(不是自动化诊断期,只是一次手动检查):

**review_drift 触发的每一代 CQL,部署前人工必须做**(champion = 当前线上 ONNX,challenger = 本代新 ONNX):

1. **归因窗口外近期数据对比**:用归因窗口(本代 state_trace 覆盖期)**之外**的一段近期数据(如最近 1-3 个月,未参与 reward shaping),手动跑一次 challenger vs champion 的 FQE + 1-day 回测。
   - 不需要自动化诊断期设计,只需手动 `python3 ope_evaluator.py --policy challenger.onnx --observations <近期>...` + `dotnet run --project Launcher --config config-<strategy>-challenger-recent.json`。
   - 目的:确认 challenger 在归因窗口外不劣化(防 reward 过拟合单次路径)。
2. **代际 shaping 权重核对**:读 `generation_<N>_shaping.json`(§3.3 #2),人工对比本代 vs 上代 shaping 权重变化。若某 term 权重跳变 >2×(如 extreme_risk_contrib_penalty 0.5→3.0),需在部署前口头确认"这是 review gap 真实反映,还是单次噪声"(配合 §3.2 min_trades gate 已过滤小样本)。
3. **observation 维度校验**:确认 challenger ONNX `obs_dim` == manifest `feedback.observation_fields` 长度(§5.3 #4),否则拒绝部署。

**checklist 落地形式**:在 `fire_optimization` 末尾(deploy_gate 提示前)打印一段 `[deploy_gate checklist]` 文本块,列上述 3 项 + 各自的命令/文件路径,操作员手动执行并核对后人工 confirm deploy。**不阻断自动化**(review_drift 仍触发重训),但 deploy 必须过此 checklist。代码改动:`evolution_scheduler.fire_optimization` 加一个 `_print_deploy_checklist()` 函数(纯打印,无副作用)。

**退出路径**:下一 spec 落地 champion/challenger 影子部署后,第 1 项(归因窗口外对比)自动化为影子对比,champion 升级阈值替代人工;第 2/3 项保留为 deploy_gate 常驻 checklist。

### 5.4 不改

- `bayesian_optimizer.py` / `parameter_space` / Layer A(正交,见 `docs/update2-feedback.md`)
- review 层 `Scripts/review/`(只读 review.json + sidecar,不改)
- LEAN core(`Common/`/`Engine/`/`Report/`)
- 现有 14 个 Grafana dashboard
- gold2 4 层望远镜归因数学(复用,不改)

---

## 6. 文件清单(实现时创建/修改)

### 6.1 新建(Python feedback 层)

- `Scripts/feedback/__init__.py`
- `Scripts/feedback/adapters/__init__.py`
- `Scripts/feedback/adapters/base.py`(ABC + FeedbackAction)
- `Scripts/feedback/adapters/gold2.py`(Gold2FeedbackAdapter,触发器 + per-bar 下采样 + shaping_overrides)
- `Scripts/feedback/signals.py`(orchestrator:load adapter,读 review+state_trace,build FeedbackAction)
- `Scripts/feedback/reward_injector.py`(apply shaping_overrides 到 trace_to_mdp_dataset)
- `Scripts/feedback/observation_injector.py`(apply observation_fields 到 _encode_state)
- `Tests/test_feedback_base.py`
- `Tests/test_feedback_gold2.py`
- `Tests/test_reward_injection.py`
- `Tests/test_observation_injection.py`
- `Tests/test_review_drift_trigger.py`
- `Tests/test_feedback_e2e.py`

### 6.2 修改(gold2 策略侧 C# — 非 LEAN core)

- `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs` — 加 `_peakTpv`/`_prevTpv` 字段 + OnData 末尾更新 + SerializeRlState 补 `drawdown`/`pnl` 2 字段
- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` — 加 `feedback:` 段 + state_schema 补 drawdown/pnl + dim_hint 13

### 6.3 修改(管线侧 Python)

- `Scripts/auto_optimize/evolution_scheduler.py` — check_triggers 加第 5 触发器 `review_drift` + fire_optimization 读 `.feedback.json`
- `Scripts/auto_optimize/offline_rl_trainer.py` — reward 源改读 manifest `reward_config.shaping` + 合并 feedback shaping_overrides
- `Scripts/auto_optimize/trace_to_mdp_dataset.py` — `_encode_state` 读 observation_fields + `_compute_reward` 注册 `layer_contrib_penalty` term
- `Scripts/auto_optimize/gym_env.py` — `eval_term` 注册 `layer_contrib_penalty`
- `Scripts/auto_optimize/policy_exporter.py` — `--obs-dim` 从硬编码 8 改为读 manifest observation_fields 长度
- `Scripts/auto_optimize/config.yaml` — 删 `offline_rl.reward_terms`(只留算法超参)

### 6.4 不改

LEAN core、Layer A Bayesian、review 层、现有 Grafana dashboard。

---

## 7. 开放问题(实现前需用户决策)

无。所有关键决策已在本次 brainstorming 中确认:
- 双轨拆分,先做优化轨(启发轨留后续 spec)
- 全闭环(触发器 + reward shaping + observation 扩 13 维)
- 通用 ABC + manifest 声明结合
- gold2 作试验,接口通用
- 与 Layer A 正交(详见 `docs/update2-feedback.md`)
