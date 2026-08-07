# 策略启发环路(Strategy Inspiration Loop)设计规范

> **状态**:已批准(2026-07-13)。方案 A — per-layer 状态机 + LLM 假设文档 + evolution_scheduler 第 6 触发器。通用框架,gold2 为首个有代际历史的试验策略。
>
> **前序 spec**:`docs/superpowers/specs/2026-07-12-review-feedback-loop-design.md` §0.1 明确把"启发新策略"留给本 spec。

## 0. 目标与约束

### 0.1 目标(用户确认)

把复盘(review)层输出 + 优化轨代际历史反馈到**新策略构建**:当某因子层连续 N 代 reward shaping 修不动(gap 持续 + weight 不收敛),说明问题是结构性的,需新设计而非继续调参。LLM 读复盘+代际历史 → 产假设文档 → 走 create-strategy pipeline 落地新策略。

**两轨分工**(用同一份代际数据做区分依据,不重复消费):
- **优化轨**(review_drift,第 5 触发器):处理"能用 reward/参数修的问题" — gap 偶发、weight 收敛。
- **启发轨**(inspiration,第 6 触发器):处理"reward 修不动、需要换设计"的问题 — gap 连续 N 代 + weight 不收敛。

### 0.2 硬约束

- **通用框架**:inspiration 层无 gold2 硬编码。层名从 manifest `feedback.shaping_term_map` keys 动态读;LLM prompt 参数化层名 + 层语义。任何走 auto_optimize 管线的策略(有 manifest `feedback:` + 代际日志)都能触发启发。
- **per-layer 状态机**:`optimizing | inspiration_pending | redesigned`,per-layer 非 per-strategy。gold2 4 层,某层进 `inspiration_pending` 不连带暂停其他 3 层优化。
- **跨代持久性过滤**:连续 N 代同层 gap 超阈 + weight 不收敛才触发。跨代持久性天然过滤单次 review 噪声,不需另发明"诊断期"设计。
- **LLM 假设文档 → 走 create-strategy**:LLM 产 Markdown 假设 → 落 `Results/soloquant/local-strategies/` → create-strategy pipeline 自动消费(GATE 0 过)。最少改动,复用现有 code-gen + GATE 1-3 验证。
- **复用 glm-5.1 mydamoxing**:LLM 配置复用 `config-soloquant.json` 的 `code-generation-llm`(base-url https://mydamoxing.cn/v1, model glm-5.1, env ZHIPU_API_KEY)。
- **零 LEAN native 改动**:inspiration 层全 Python `Scripts/inspiration/`。不改 LEAN core、不改 gold2 C#、不改 review/feedback 层逻辑(只读 layer_states 做互斥)。

### 0.3 与优化轨的正交关系

优化轨(review_drift)改训练信号(reward + observation),让现有 RL agent 学得更好;启发轨(inspiration)产新策略,当 reward 修不动时换设计。两轨 per-layer 互斥:某层 `inspiration_pending` 时,review_drift 对该层跳过 gap 判定,避免重复消费。

---

## 1. 架构 + 数据流

### 1.1 目录结构

全部 greenfield Python 放 `Scripts/inspiration/`(与 `Scripts/review/`、`Scripts/feedback/` 同级):

```
Scripts/inspiration/
  __init__.py
  layer_state.py         # per-layer 状态机 + 持久化
  generations.py         # 代际日志读写 (generation_<N>.json)
  hypothesize.py         # LLM 假设生成 (review+代际历史 → Markdown)
  trigger.py             # inspiration 触发条件检测 (连续 N 代不收敛)
  provenance.py          # manifest provenance 写入
```

### 1.2 核心抽象

**`LayerState`** dataclass(`layer_state.py`):
```python
@dataclass
class LayerState:
    layer: str                          # "extreme_risk"
    status: str                         # "optimizing" | "inspiration_pending" | "redesigned"
    pending_since_generation: int = -1  # 进入 inspiration_pending 时的代数
    inspired_strategy_id: str = ""      # 启发产出的策略 id (redesigned 时填)
    retired_shaping_terms: list = field(default_factory=list)  # 退役的 shaping term
    candidate_deployed: bool = False    # spirit2 #3: 候选策略是否已部署
    candidate_status: str = "none"      # "none" | "pending_review" | "deployed" | "rejected"
```

**状态机合法转移**:
```
optimizing --(连续N代不收敛)--> inspiration_pending
inspiration_pending --(启发产物过GATE1-4 且 层改善验证通过)--> redesigned
inspiration_pending --(超时M代)--> optimizing
redesigned --(新策略也失效,再次N代不收敛)--> inspiration_pending  (允许二次启发)
```

**二次启发前置检查**(spirit2 #3):`redesigned → inspiration_pending` 的二次触发,不纯粹依赖父策略 gap 持续超标(父策略没变,缺陷还在,会反复生成相似假设)。**先检查 `candidate_status`**:
- `candidate_status == "pending_review"`(候选已产出但未部署)→ **不重复触发** inspiration,改为在 deploy_gate checklist 里提醒"已有候选策略待审"(防死信箱)
- `candidate_status == "deployed"`(候选已部署但仍失效)→ 允许二次触发(部署了也没解决,真需新一轮启发)
- `candidate_status == "rejected"`(候选被人工拒绝)→ 允许二次触发
- `candidate_status == "none"`(异常状态)→ 允许触发(容错)

`candidate_status` 由 deploy_gate checklist 人工回填(部署→deployed,拒绝→rejected)。新策略产出未审 → `pending_review`。

持久化在 `evolution_state.json` 的 `layer_states: {strategy: {layer: LayerState}}` 字段(per-strategy,per-layer)。

### 1.3 端到端数据流

```
每代 CQL 重训后 (fire_optimization)
  → generations.log(strategy, gen_N, layer_gaps, shaping_overrides, review_status)
  → 写 Results/auto_optimize/<strategy>/generation_<N>.json
  → state["generation_count"] += 1

check_triggers (evolution_scheduler)
  → 第 5 触发器 review_drift: 跳过非 optimizing 层的 gap 判定 (per-layer 互斥)
  → 第 6 触发器 inspiration:
     (a) detect: 连续 N 代同层不收敛 → 该层 → inspiration_pending
     (b) timeout 回收: inspiration_pending 超 M 代 → 打回 optimizing
     → 若有新 inspiration_pending 层 → inspiration trigger = True

fire_optimization (inspiration 触发后)
  → hypothesize.run(每层): review.json + 代际历史 → glm-5.1 prompt → 假设 Markdown
  → 写 Results/soloquant/local-strategies/<inspired-id>.md
  → create-strategy pipeline 自动消费 (GATE 0 过 → brainstorming → code gen → GATE 1-3)
  → 新策略验证通过 (GATE 3 Sharpe ≥ 0)
     → provenance.write(new_strategy_manifest, parent_strategy, review_artifact, inspired_layer, hypothesis)
     → layer_state.status = redesigned, retired_shaping_terms += [该层 shaping term]
  → 超时 M 代未验证 → layer_state.status = optimizing (打回)
```

### 1.4 per-layer 互斥:review_drift 跳过非 optimizing 层

review_drift 的 layer_gap 判定(在 `Gold2FeedbackAdapter._check_triggers` 内)跳过 `status != "optimizing"` 的层:
```python
for layer, agg in review_doc["layer_attribution"].items():
    ls = layer_states.get(layer) if layer_states else None
    if ls and ls.status != "optimizing":
        continue  # 该层在 inspiration_pending/redesigned,review_drift 跳过
    pct = abs(agg.get("pnl_pct_of_total", 0))
    if pct > gap_thresh:
        trigger = True
        reasons.append(f"layer_gap {layer}=...>{gap_thresh}")
```

`signals.orchestrate` 签名加 `layer_states` 参数,透传给 `adapter.feedback_signal(manifest, review_doc, state_trace, layer_states)`。ABC `feedback_signal` 加可选 `layer_states` 参数(默认 None = 全 optimizing,向后兼容)。

### 1.5 通用性保证

- `inspiration/` 模块无 gold2 硬编码 — 全部走 `manifest.raw["feedback"]` / `manifest.raw["review"]`
- gold2 是首个有 ≥3 代历史的策略(优化轨已跑过),故是首个能触发启发的策略;其他策略积累代际后自动适用
- `hypothesize.py` 的 prompt 模板用 `{layer_name}` / `{layer_semantic}` 占位,不写死 "extreme_risk = 保护层"
- provenance 写入新策略 manifest 时,`parent_strategy` 字段记实际父策略名(非 gold2)

### 1.6 零 LEAN native 改动

inspiration 层全 Python。不碰 LEAN core、不改 gold2 C#。复用 create-strategy skill(已存在的 LLM pipeline)。

---

## 2. manifest `inspiration:` 段 + 代际日志 schema + per-layer 状态机

### 2.1 通用 `inspiration:` 段(与 `review:`/`feedback:` 平级,raw-passthrough)

新顶层 key,仅 `inspiration` 模块 + evolution_scheduler 消费,不加 dataclass:

```yaml
inspiration:
  # 触发条件:连续 N 代同层 gap 超阈 + weight 不收敛
  persistence:
    min_generations: 3              # 连续 3 代
    gap_threshold: 0.15             # |layer.pnl_pct_of_total| > 此值
    weight_nonconvergence_delta: 0.1 # weight 单调升或波动幅度 > 此值 = 未收敛
  # 启发超时:进入 inspiration_pending 后 M 代未验证 → 打回 optimizing
  timeout_generations: 5
  # LLM 配置(复用 create-strategy 的 code-generation-llm,此处只留覆盖项)
  llm:
    config_key: code-generation-llm  # 读 config-soloquant.json 的此 block
    model: glm-5.1                   # 默认,可覆盖
    temperature: 0.7                 # 假设生成用稍高温度(创造性)
  # 产物落点
  hypothesis_dir: Results/soloquant/local-strategies  # 假设 Markdown 落此处,create-strategy 自动消费
```

### 2.2 代际日志 schema(`generation_<N>.json`)

`Results/auto_optimize/<strategy_name>/generation_<N>.json`:
```json
{
  "strategy": "Gold2BetaVolTargetStrategy",
  "generation": 3,
  "timestamp": "2026-07-13T...",
  "review_status": "pass",
  "layer_gaps": {
    "trend": {"pnl_pct_of_total": 0.10, "gap": 0.10},
    "extreme_risk": {"pnl_pct_of_total": -0.32, "gap": 0.32}
  },
  "shaping_overrides": {
    "extreme_risk_contrib_penalty": 2.13
  },
  "review_artifact": "Results/gold2-betavol/review/review.json"
}
```
`generations.py` 提供 `log()`(写) + `read_history(strategy, n)`(读最近 N 代)。

### 2.3 per-layer 状态机持久化

`evolution_state.json` 加 `layer_states` + `generation_count` 字段:
```json
{
  "last_optimize_date": "2026-07-13T...",
  "last_backtest_sharpe": 0.9,
  "generation_count": 3,
  "layer_states": {
    "Gold2BetaVolTargetStrategy": {
      "extreme_risk": {"status": "inspiration_pending", "pending_since_generation": 3, "inspired_strategy_id": "", "retired_shaping_terms": []},
      "trend": {"status": "optimizing", "pending_since_generation": -1, "inspired_strategy_id": "", "retired_shaping_terms": []}
    }
  }
}
```

`layer_state.py` 提供:
- `load_layer_states(state_path, strategy) -> dict[layer, LayerState]`
- `save_layer_states(state_path, strategy, states)`
- `transition(states, layer, new_status, **kwargs)` — 状态转移,校验合法路径

### 2.4 trigger 不收敛判据(通用)

`trigger.detect(strategy, last_n_gens, layer_states, thresholds)`:
```
对每个 status == "optimizing" 的层 L:
  取最近 min_generations 代的 layer_gaps[L].gap 序列 [g1, g2, ..., gN]
  条件1: 所有 gi > gap_threshold (连续 N 代超阈)
  条件2: weight 未收敛(spirit2 #1 修复,含 ceiling 判据):
    取同代的 shaping_overrides[L_term] 序列 [w1, ..., wN]
    未收敛 = (a) weight 趋势未降: (wN - w1 > weight_nonconvergence_delta)  # 单调升(惩罚加大仍没解决)
          OR (b) weight 波动: (max(w) - min(w) > weight_nonconvergence_delta)  # 波动不降
          OR (c) ceiling 封顶且 gap 仍超阈: all(wi >= CEILING - EPS for wi in [w1..wN])  # 惩罚顶格但 gap 没解决
                 (CEILING = feedback spec §3.3 的 clamp 上限 3.0; EPS = 0.01)
                 —— clamp 会把 [3.0,3.0,3.0] 抹平 delta=0 误判收敛,此判据独立兜底
  条件1 AND 条件2 → L 进入 inspiration_pending
```

**ceiling 判据说明**(spirit2 #1):`weight = clamp(gap/0.15, 0.5, 3.0)` 有上限。若某层 gap 从 0.32 恶化到 0.60,weight 早已封顶 3.0,序列 `[3.0, 3.0, 3.0]` 的 `max-min=0` 会被(a)(b)判为"收敛",但实际是**惩罚力度顶格了问题仍没解决**——恰恰最该触发启发。条件(c)独立兜底:weight 连续 N 代贴 ceiling AND gap 仍超阈 → 直接判未收敛,不依赖 delta 计算。`CEILING` 从 manifest `feedback` 段或 feedback spec §3.3 的 clamp 上限读(默认 3.0)。

### 2.5 gold2 manifest 完整 `inspiration:` 段

```yaml
inspiration:
  persistence:
    min_generations: 3
    gap_threshold: 0.15
    weight_nonconvergence_delta: 0.1
  timeout_generations: 5
  llm:
    config_key: code-generation-llm
    model: glm-5.1
    temperature: 0.7
  hypothesis_dir: Results/soloquant/local-strategies
```

---

## 3. LLM hypothesize + 假设文档 + 接入 create-strategy

### 3.1 hypothesize.run 流程(`Scripts/inspiration/hypothesize.py`)

```python
def run(strategy_name: str, inspired_layer: str, review_doc: dict,
        gen_history: list, manifest: dict, llm_cfg: dict) -> str:
    """读 review.json + 该层代际历史 → glm-5.1 prompt → 假设 Markdown。
    返回写入的 Markdown 路径 (Results/soloquant/local-strategies/<inspired-id>.md)。"""
```

步骤:
1. **组装 context**:该层最近 N 代 gap 序列 + weight 序列 + 当前 review.json 该层的 per-trade 归因细节 + drawdown_attribution 里该层出现的回撤 episode + manifest `review.layer_names`(层语义)
2. **调 LLM**:复用 `soloquant_orchestrator.create_http_client_from_config(config, "code-generation-llm")` 建 client(model/temperature 从 `inspiration.llm` 覆盖)
3. **产 Markdown**:LLM 输出根因诊断 + 替代设计假设
4. **写文件**:`Results/soloquant/local-strategies/<strategy>-<layer>-inspired-<ts>.md`

### 3.2 LLM prompt 模板(通用,层名参数化)

**system prompt**:
```
你是量化策略研究员。任务:基于一个量化策略某因子层的复盘归因 + 代际优化历史,
诊断该层"为什么 reward shaping 修不动",并提出一个替代设计假设(新策略思路)。

约束:
- 只针对指定的失效层提假设,不要泛泛给策略 idea
- 假设必须可落地为 A 股策略(518880/600xxx/000xxx/300xxx/ETF;T+1;100 股整手;不可做空)
- 假设要回答:"该层结构性失效的根因是什么" + "替代设计如何避免该根因"
- 不引入外部资料,只基于提供的复盘 + 代际数据推理
- 输出 Markdown,包含:根因诊断、替代设计假设、预期改善、A 股落地映射
```

**user prompt**(参数化):
```
## 策略
{strategy_name}

## 失效层
{inspired_layer} (层语义: {layer_semantic})

## 该层代际历史(最近 {N} 代)
{generation_table:  generation | gap  | shaping_weight | review_status}
代际观察: gap 连续 {N} 代超阈 {threshold}, shaping weight 从 {w1} 升到 {wN} 未收敛。

## 当前 review.json 该层归因细节
layer_attribution[{inspired_layer}]: {pnl_abs, pnl_pct_of_total, n_trades, n_wins}

## 该层参与的回撤 episode
{drawdown_episodes where inspired_layer in top_contributing_layers}

## 该层 per-trade 负贡献样本(最多 10 笔)
{per_trade where layer_contributions[inspired_layer] < 0}

## 输出要求
Markdown 文档,标题: "受 {strategy_name} {inspired_layer} 层失效启发的策略假设",
含 4 节: 根因诊断 / 替代设计假设 / 预期改善 / A 股落地映射。
```

### 3.3 假设 Markdown 产物示例

```markdown
# 受 Gold2BetaVolTargetStrategy extreme_risk 层失效启发的策略假设

## 根因诊断
extreme_risk 层(VIX P95 / RVol P95 → cap 0.30)连续 3 代负贡献 -32%,
shaping weight 从 1.0 升到 2.13 仍未收敛。根因:cap 是二元阈值触发
(VIX > P95 才降仓),但 A 股黄金 ETF 的极端回撤常发生在 VIX 未触 P95
但 RVol 持续高位的"慢热"区间,cap 漏判 → 持仓过重 → 亏损。

## 替代设计假设
用连续波动率状态切换替代二元 cap:基于 RVol 分位划分 3 档
(normal/elevated/extreme),每档独立仓位上限,过渡平滑而非跳变。
预期消除"cap 漏判慢热区间"的失效模式。

## 预期改善
- 慢热极端区间:RVol P80-P95 档降仓 50%,而非等 P95 才降
- 回撤改善:目标将该层贡献从 -32% → -10% 以内

## A 股落地映射
标的: 518880 (黄金 ETF,不可做空,100 股整手)
信号: RVol_60d 分位 3 档(RvolWindow=60 复用)
仓位: normal 1.0 / elevated 0.5 / extreme 0.3 (cap 连续而非二元)
风控: T+1 DelayedSettlement, AShareLotSizeExecution
数据: tushare 518880 日频(RVol 内生,无外部数据依赖)
```

### 3.4 接入 create-strategy pipeline

假设 Markdown 落 `Results/soloquant/local-strategies/` 后:
1. `soloquant_pipeline_runner.py` 的 `ingest_local_strategies` 扫描该目录(1h 间隔),捡到 `.md` 文件
2. create-strategy skill 的 GATE 0(`paper_download`:文件存在 + >100 字符)通过
3. Step 1.5 brainstorming 读该 Markdown 作"精准复现"(此处复现的是假设,不是论文)
4. Step 2 LLM extract → Step 3 code gen → GATE 1(数据可用性)→ GATE 2(code smoke)→ GATE 3(Sharpe ≥ 0)
5. GATE 3 通过 → 新策略上线;`provenance.write` 记录父策略 + 启发层 + 假设来源
6. layer_state 该层 → `redesigned`,`retired_shaping_terms` 加入该层老 shaping term

### 3.5 provenance 写入(`provenance.py`)

新策略的 SoloQuant manifest(`soloquant_orchestrator.py:4938-4951`)加 `provenance` block:
```json
{
  "strategy_id": "RvolRegimeGold-abc123",
  "provenance": {
    "source": "review_inspiration",
    "parent_strategy": "Gold2BetaVolTargetStrategy",
    "review_artifact": "Results/gold2-betavol/review/review.json",
    "inspired_layer": "extreme_risk",
    "hypothesis": "Results/soloquant/local-strategies/Gold2BetaVolTargetStrategy-extreme_risk-inspired-20260713.md",
    "inspired_at_generation": 3
  }
}
```
`origin` enum 扩展:`web | local | cli | review_inspiration`(`soloquant_orchestrator.py:245-246`)。

### 3.6 降级路径

- **LLM 调用失败**(网络/key/超时):`hypothesize.run` 抛异常,evolution_scheduler 的 inspiration 触发 try/except 包裹,log + 跳过,该层保持 `inspiration_pending`(下次触发重试,受 `timeout_generations` 超时保护)
- **假设文档 < 100 字符**(LLM 产空/过短):不写 `local-strategies/`,记 log,该层保持 pending
- **GATE 1-4 未通过**(假设落地失败):新策略 BLOCK,layer_state 保持 `inspiration_pending`,消耗一个 `timeout_generations` 计数;超时后打回 `optimizing`

### 3.7 层级改善验证(spirit2 #2)

**问题**:GATE 3 只检查新策略整体 Sharpe≥0,与 LLM 假设里"该层贡献从 -32%→-10%"的可验证主张脱钩。新策略可能因其他层碰巧表现好而 Sharpe 过关,但被诊断失效的 `inspired_layer` 压根没被验证是否真改善,系统却已标 `redesigned`。

**修复**:对 `origin == review_inspiration` 的策略,GATE 3 通过后加一条**层归因改善校验**(轻量,复用现有 review pipeline):

1. 对新策略跑 `run_review.py`(产新 review.json)
2. 比较新策略 vs 父策略同层归因:
   - `new_layer_pct = new_review.layer_attribution[inspired_layer].pnl_pct_of_total`
   - `old_layer_pct = parent_review.layer_attribution[inspired_layer].pnl_pct_of_total`
   - 改善 = `new_layer_pct - old_layer_pct`(负贡献减小 = 改善为正)
3. **校验通过条件**:`改善 > 0`(新策略该层贡献优于老策略)。若假设文档含明确数字主张(如"-32%→-10%"),校验 `new_layer_pct >= 主张目标`。
4. 校验通过 → `provenance.write` 时记 `improvement_verified: true` + `layer_improvement: 改善值` → layer_state `redesigned`
5. **校验未通过**(改善≤0):**不**标 `redesigned`,layer_state 保持 `inspiration_pending`(消耗一个 timeout 计数),provenance 记 `improvement_verified: false`。等下一轮启发重试或超时打回。

`provenance.py` 的 provenance block 加 2 字段:
```json
{
  "improvement_verified": true,
  "layer_improvement": 0.22,
  "improvement_claim": "该层贡献从 -32% → -10% 以内"
}
```
`improvement_claim` 从假设 Markdown 的"预期改善"节正则抽取(若有数字主张),否则留空(校验降级为只看 `改善>0`)。

---

## 4. evolution_scheduler 第 6 触发器 + 代际日志写入 + per-layer 互斥

### 4.1 代际日志写入点(fire_optimization)

`fire_optimization` 在 Layer C 重训完成、review overlay 跑完后(`return True` 前),加代际日志写入:
- 读本代 `.feedback.json` 拿 `shaping_overrides`
- 读本代 review sidecar 拿 `review_status` + review.json 拿 `layer_gaps`
- `state["generation_count"] += 1`
- `generations.log(strategy, gen_n, layer_gaps, shaping_overrides, review_status)` 写 `generation_<N>.json`

`generation_count` 写入 `evolution_state.json`(单调递增,启发轨用它做超时判定)。

### 4.2 第 6 触发器 inspiration(check_triggers)

`check_triggers` 加第 6 触发器(在 review_drift 之后):
- 读 manifest `inspiration` 段
- `load_layer_states(state_path, strategy)` 拿当前 per-layer 状态
- `read_history(strategy, min_generations)` 拿最近 N 代日志
- `trigger.detect(strategy, history, layer_states, thresholds)` 返回需进入 `inspiration_pending` 的层列表
- 对每个层 `transition(states, layer, "inspiration_pending", pending_since_generation=gen_count)`
- `save_layer_states` + `triggers["inspiration"] = True` + `state["inspired_layers"] = to_inspire`

### 4.3 per-layer 互斥:review_drift 跳过非 optimizing 层

review_drift 的 gap 判定跳过 `status != "optimizing"` 的层(§1.4)。`signals.orchestrate` 签名加 `layer_states` 参数,透传给 `adapter.feedback_signal(manifest, review_doc, state_trace, layer_states)`。ABC `feedback_signal` 加可选 `layer_states` 参数(默认 None = 全 optimizing,向后兼容)。

### 4.4 inspiration 触发后的 fire_optimization

`fire_optimization` 在 review overlay 后,检查 `state["inspired_layers"]`,对每个层调 `hypothesize.run`:
- `hypothesize.run(strategy, layer, review_doc, gen_history, manifest.raw, llm_cfg)` → 假设 Markdown
- 写 `Results/soloquant/local-strategies/<strategy>-<layer>-inspired-<ts>.md`
- `state["inspired_layers"] = []`(消费完清空)

### 4.5 超时回收(timeout_generations)

`check_triggers` 的 inspiration 触发器内,额外检查 `inspiration_pending` 层是否超时:
- 对每个 `status == "inspiration_pending"` 的层,`pending_gens = gen_count - pending_since_generation`
- 若 `pending_gens >= timeout_generations` → `transition(states, layer, "optimizing")`(打回)
- `save_layer_states`

### 4.5.1 候选策略待审提醒(spirit2 #3)

`redesigned` 状态下,若 `candidate_status == "pending_review"`(候选已产出未部署),**该层不因父策略 gap 持续超标而二次触发 inspiration**(避免反复生成相似假设)。改为:
- `_print_deploy_checklist` 加一条 "inspiration 候选策略待审" 提醒项:列出 `inspired_strategy_id` + provenance.hypothesis 路径,提示人工去 create-strategy pipeline 产物里审/部署/拒绝
- 人工回填 `candidate_status`(deployed/rejected)后,二次触发判据才恢复

### 4.6 redesigned 触发(新策略验证通过后)

新策略过 GATE 3 且**层改善验证通过**(§3.7)后,`provenance.write` 时回写父策略 layer_state:
- `load_layer_states(parent_state_path, parent_strategy)`
- `transition(states, inspired_layer, "redesigned", inspired_strategy_id=new_id, retired_shaping_terms=[shaping_term], candidate_status="pending_review")`(候选已产出,待审)
- `save_layer_states`

父策略 `state_path` 通过 manifest `inspiration.parent_state_path` 或约定 `Results/auto_optimize/evolution_state.json` 推断。

### 4.7 数据流总结

```
fire_optimization (每代重训后)
  → generations.log(strategy, gen_N, layer_gaps, shaping_overrides, review_status)
  → state["generation_count"] += 1

check_triggers
  → review_drift (第5): 跳过非 optimizing 层的 gap 判定 (per-layer 互斥)
  → inspiration (第6):
     (a) detect: 连续 N 代同层不收敛 → 该层 → inspiration_pending
     (b) timeout 回收: inspiration_pending 超 M 代 → 打回 optimizing
     → 若有新 inspiration_pending 层 → inspiration trigger = True

fire_optimization (inspiration 触发后)
  → hypothesize.run(每层): review+代际历史 → glm-5.1 → 假设 Markdown
  → 写 Results/soloquant/local-strategies/<id>.md
  → create-strategy pipeline 自动消费 → GATE 0-3
  → GATE 3 通过 → provenance.write → layer → redesigned
  → 超时 M 代 → layer → optimizing (打回)
```

---

## 5. 测试 + 已知局限

### 5.1 单测

- **LayerState + 状态机**(`Tests/test_inspiration_layer_state.py`):
  - 合法转移 `optimizing → inspiration_pending` / `→ redesigned` / `→ optimizing`(超时)
  - 非法转移抛错(如 `redesigned → optimizing` 直接跳,必须经 `inspiration_pending`)
  - `load/save_layer_states` 往返(per-strategy,per-layer)
- **generations 日志**(`Tests/test_inspiration_generations.py`):
  - `log()` 写 `generation_<N>.json`,字段齐全
  - `read_history(strategy, n)` 返回最近 N 代,不足 N 返回全部
  - 缺文件 → 空列表
- **trigger.detect**(`Tests/test_inspiration_trigger.py`):
  - 连续 3 代 gap>0.15 + weight 单调升 0.5→2.13(delta>0.1)→ 触发
  - 连续 3 代 gap>0.15 但 weight 收敛(1.0→1.05,delta<0.1)→ 不触发(修得动)
  - 仅 2 代超阈(不足 min_generations)→ 不触发
  - 该层已 `inspiration_pending` → 不重复触发
  - 通用性:层名从 manifest 读,不假设 `extreme_risk`
- **hypothesize.run**(`Tests/test_inspiration_hypothesize.py`,mock LLM):
  - mock `create_http_client_from_config` 返回固定 Markdown → 写 `local-strategies/<id>.md`,>100 字符
  - LLM 抛异常 → run 抛异常(不写空文件)
  - LLM 产 <100 字符 → 不写文件,抛 ValueError
  - prompt 含 `{strategy_name}`/`{inspired_layer}`/代际表 占位正确填充
- **provenance.write**(`Tests/test_inspiration_provenance.py`):
  - 新策略 manifest 加 `provenance` block
  - `origin` enum 扩展含 `review_inspiration`
  - 父策略 layer_state 该层 → `redesigned` + `retired_shaping_terms` 填充
- **evolution_scheduler 第 6 触发器**(`Tests/test_inspiration_trigger_e2e.py`):
  - mock 代际历史(3 代不收敛)+ layer_states → `triggers["inspiration"]=True` + 该层 `inspiration_pending`
  - 该层 `inspiration_pending` 时 review_drift 的 gap 判定跳过(per-layer 互斥)
  - 超时 M 代 → 打回 `optimizing`
  - `generation_count` 递增 + 代际日志写入

### 5.2 端到端集成

- **inspiration 全闭环 e2e**(`Tests/test_inspiration_e2e.py`):
  - 合成 3 代 `generation_<N>.json`(extreme_risk 连续 gap 0.32 + weight 升)
  - `check_triggers` → `inspiration=True` + layer `inspiration_pending`
  - `hypothesize.run`(mock LLM)→ 假设 Markdown 落 `local-strategies/`
  - 文件存在 + >100 字符(满足 GATE 0)
  - `provenance.write` → 父策略 layer `redesigned`
- **per-layer 互斥 e2e**:gold2 extreme_risk `inspiration_pending` 时,trend/vol_target/realrate_cap 仍正常 review_drift

### 5.3 已知局限

1. **依赖代际历史积累**:启发需 ≥ `min_generations`(默认 3)代日志。新策略无历史 → inspiration 不触发(gracefully skip)。gold2 是首个能触发的策略。
2. **LLM 假设质量不可控**:glm-5.1 产的假设可能空洞/不可落地。缓解:GATE 1-3 是硬验证(数据可用性 + code smoke + Sharpe≥0),假设再好过不了 Gate 也作废;`timeout_generations` 超时打回防无限期挂起。
3. **per-layer 互斥的 feedback_signal 签名变更**:`adapter.feedback_signal` 加可选 `layer_states` 参数。现有 `Gold2FeedbackAdapter` 需更新签名(默认 None 保持向后兼容)。
4. **redesigned 后老策略不自动退役**:`redesigned` 只标记该层 shaping term 退役,老策略本身继续跑(deploy_gate manual)。新策略与老策略并存,由人工决定是否切换。自动化切换留下一 spec(champion/challenger 影子部署)。
5. **`drawdown_attribution.top_contributing_layers` 仍空**:本 spec 不填(review 层 cli.py:58-59 的空 `[]`)。LLM prompt 里该字段为空时,根因诊断主要靠 layer_attribution + per_trade 归因。填该字段是独立的 review 层增强,留后续。
6. **`generation_count` 起始值**:首次跑的策略 `state.json` 无 `generation_count` → 默认 0,首代 log 为 generation_1。若 `state.json` 被删 → generation_count 重置,代际历史断裂 → `read_history` 读不到连续 N 代 → 不触发(安全降级)。
7. **假设 Markdown 命名冲突**:`<strategy>-<layer>-inspired-<ts>.md` 用时间戳防冲突。若同层多次启发(二次 redesigned),ts 不同,文件不覆盖。
8. **origin enum 扩展破坏性**:`soloquant_orchestrator.py:245-246` 的 `origin` enum 加 `review_inspiration`,需 grep 确认无 switch/if 对 origin 穷举匹配(否则 fallthrough)。

### 5.4 不改

- LEAN core(`Common/`/`Engine/`/`Report/`)
- gold2 C# 代码(启发轨纯 Python + 复用 create-strategy)
- review 层 `Scripts/review/`(只读 review.json + sidecar)
- 优化轨 `Scripts/feedback/`(只读 layer_states 做互斥,不改 feedback 逻辑)
- Layer A Bayesian / `bayesian_optimizer.py`
- 现有 14 个 Grafana dashboard

---

## 6. 文件清单(实现时创建/修改)

### 6.1 新建(Python inspiration 层)

- `Scripts/inspiration/__init__.py`
- `Scripts/inspiration/layer_state.py`(LayerState + 状态机 + load/save)
- `Scripts/inspiration/generations.py`(代际日志 log + read_history)
- `Scripts/inspiration/hypothesize.py`(LLM 假设生成 + Markdown 产物)
- `Scripts/inspiration/trigger.py`(连续 N 代不收敛检测)
- `Scripts/inspiration/provenance.py`(manifest provenance + origin enum 扩展 + layer_state 回写)
- `Tests/test_inspiration_layer_state.py`
- `Tests/test_inspiration_generations.py`
- `Tests/test_inspiration_trigger.py`
- `Tests/test_inspiration_hypothesize.py`
- `Tests/test_inspiration_provenance.py`
- `Tests/test_inspiration_trigger_e2e.py`
- `Tests/test_inspiration_e2e.py`

### 6.2 修改(gold2 manifest + 管线侧)

- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` — 加 `inspiration:` 段
- `Scripts/auto_optimize/evolution_scheduler.py` — check_triggers 加第 6 触发器 + fire_optimization 代际日志 + hypothesize 调用 + 超时回收
- `Scripts/feedback/signals.py` — orchestrate 签名加 `layer_states` 参数
- `Scripts/feedback/adapters/base.py` — `feedback_signal` 加可选 `layer_states` 参数(默认 None)
- `Scripts/feedback/adapters/gold2.py` — `_check_triggers` 跳过非 optimizing 层
- `Scripts/soloquant_orchestrator.py` — `origin` enum 加 `review_inspiration`(`:245-246`);provenance block 写入(`:4938-4951` 附近)

### 6.3 不改

LEAN core、gold2 C#、review 层、feedback 逻辑、Layer A、Grafana dashboard。

---

## 7. 开放问题(实现前需用户决策)

无。所有关键决策已在本次 brainstorming 中确认:
- 输入源:复盘归因 + 优化轨代际记录(跨代持久性过滤单次噪声)
- 代际数据:新增 `generation_<N>.json` 日志
- LLM 产物:假设文档 Markdown → 走 create-strategy pipeline
- 触发:evolution_scheduler 第 6 触发器 `inspiration`
- 两轨防冲突:per-layer 状态机(`optimizing | inspiration_pending | redesigned`)+ 超时回收
- LLM:复用 glm-5.1 mydamoxing
- 通用框架:无 gold2 硬编码,层名参数化
