# 策略启发环路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 per-layer 状态机 + LLM 假设文档 + evolution_scheduler 第 6 触发器,当某因子层连续 N 代 reward shaping 修不动时,LLM 产假设文档走 create-strategy pipeline 落地新策略。

**Architecture:** Python inspiration 层在 `Scripts/inspiration/`,通用框架(无 gold2 硬编码)。代际日志 `generation_<N>.json` 记录每代 layer gap + shaping weight;trigger 检测连续 N 代不收敛(含 ceiling-clamp 判据);per-layer 状态机 `optimizing|inspiration_pending|redesigned` 控制两轨互斥;LLM(glm-5.1 mydamoxing)产假设 Markdown 落 `local-strategies/` 供 create-strategy 消费;层改善验证 + candidate_status 防死信箱。

**Tech Stack:** Python 3(.11)、stdlib `json`/`pathlib`/`dataclasses`、`soloquant_orchestrator.create_http_client_from_config`(LLM 调用)。

**关键事实(实现者必读,源自真实代码核查):**
- `evolution_scheduler.py` check_triggers 已有 5 触发器,签名 `check_triggers(config, state, metrics_path, results_dir=None, manifest=None)`。`fire_optimization(manifest_path, config, state_path)` 末尾有 review overlay + deploy checklist。
- `feedback/signals.py` `orchestrate(manifest, results_dir)` 调 `adapter.feedback_signal(manifest, review_doc, state_trace)`。
- `feedback/adapters/base.py` `StrategyFeedbackAdapter.feedback_signal(self, manifest, review_doc, state_trace) -> FeedbackAction`。
- `feedback/adapters/gold2.py` `_check_triggers(review_doc, thresholds)` 内 layer_gap 判定遍历 `review_doc["layer_attribution"]`。
- `soloquant_orchestrator.py:3406` `create_http_client_from_config(config, post_json=default_post_json, use_code_generation_llm=False)`;`client(request_payload)` 调 LLM。config 从 `config-soloquant.json` 读,`code-generation-llm` block(base-url https://mydamoxing.cn/v1, model glm-5.1, env ZHIPU_API_KEY)。
- `soloquant_orchestrator.py:245-262` `normalize_origin(source, default="web")` — origin enum `web/local/cli`。
- `soloquant_orchestrator.py:4938-4951` manifest 写入 dict。
- review.json 路径:`Results/<strategy>/review/review.json` 或 `Results/gold2-betavol/review/review.json`。
- `evolution_state.json` 已有 `last_optimize_date/last_backtest_sharpe`;feedback 加了 `_state_path`。`generation_count` 待加。

---

## File Structure

### 新建 — Python inspiration 层

| 文件 | 职责 |
|---|---|
| `Scripts/inspiration/__init__.py` | package marker |
| `Scripts/inspiration/layer_state.py` | LayerState dataclass + 状态机 transition + load/save |
| `Scripts/inspiration/generations.py` | 代际日志 log + read_history |
| `Scripts/inspiration/trigger.py` | 连续 N 代不收敛检测(含 ceiling 判据) |
| `Scripts/inspiration/hypothesize.py` | LLM 假设生成 + Markdown 产物 |
| `Scripts/inspiration/provenance.py` | manifest provenance + origin enum 扩展 + layer_state 回写 + 层改善验证 |
| `Tests/test_inspiration_layer_state.py` | 状态机 + load/save |
| `Tests/test_inspiration_generations.py` | 代际日志 |
| `Tests/test_inspiration_trigger.py` | 触发检测(含 ceiling) |
| `Tests/test_inspiration_hypothesize.py` | LLM(mock)+ Markdown |
| `Tests/test_inspiration_provenance.py` | provenance + 层改善验证 |
| `Tests/test_inspiration_trigger_e2e.py` | evolution_scheduler 第 6 触发器 |
| `Tests/test_inspiration_origin_enum.py` | origin enum 扩展 |
| `Tests/test_inspiration_e2e.py` | 全闭环 |

### 修改

| 文件 | 改动 |
|---|---|
| `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` | 加 `inspiration:` 段 |
| `Scripts/auto_optimize/evolution_scheduler.py` | check_triggers 第 6 触发器 + fire_optimization 代际日志 + hypothesize 调用 + 超时回收 + checklist 待审提醒 |
| `Scripts/feedback/signals.py` | orchestrate 签名加 `layer_states` |
| `Scripts/feedback/adapters/base.py` | feedback_signal 加可选 `layer_states` |
| `Scripts/feedback/adapters/gold2.py` | _check_triggers 跳过非 optimizing 层 |
| `Scripts/soloquant_orchestrator.py` | normalize_origin 加 `review_inspiration` |

### 不改

LEAN core、gold2 C#、review 层、feedback 逻辑、Layer A、Grafana dashboard。

---

## Task 1: LayerState + 状态机 + load/save

**Files:**
- Create: `Scripts/inspiration/__init__.py`
- Create: `Scripts/inspiration/layer_state.py`
- Test: `Tests/test_inspiration_layer_state.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_layer_state.py`:
```python
"""Tests for LayerState + state machine + load/save. Spec §1.2, §2.3."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from layer_state import LayerState, transition, load_layer_states, save_layer_states  # noqa: E402


def test_layer_state_defaults():
    ls = LayerState(layer="extreme_risk", status="optimizing")
    assert ls.status == "optimizing"
    assert ls.pending_since_generation == -1
    assert ls.inspired_strategy_id == ""
    assert ls.retired_shaping_terms == []
    assert ls.candidate_deployed is False
    assert ls.candidate_status == "none"


def test_transition_optimizing_to_inspiration_pending():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    transition(states, "extreme_risk", "inspiration_pending", pending_since_generation=3)
    assert states["extreme_risk"].status == "inspiration_pending"
    assert states["extreme_risk"].pending_since_generation == 3


def test_transition_inspiration_pending_to_redesigned():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
    transition(states, "extreme_risk", "redesigned", inspired_strategy_id="NewStrategy-abc",
               retired_shaping_terms=["extreme_risk_contrib_penalty"], candidate_status="pending_review")
    assert states["extreme_risk"].status == "redesigned"
    assert states["extreme_risk"].inspired_strategy_id == "NewStrategy-abc"
    assert states["extreme_risk"].retired_shaping_terms == ["extreme_risk_contrib_penalty"]
    assert states["extreme_risk"].candidate_status == "pending_review"


def test_transition_timeout_back_to_optimizing():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
    transition(states, "extreme_risk", "optimizing")
    assert states["extreme_risk"].status == "optimizing"
    assert states["extreme_risk"].pending_since_generation == -1


def test_transition_redesigned_to_inspiration_pending_allowed():
    """spirit2 #3: redesigned → inspiration_pending allowed (二次启发)."""
    states = {"extreme_risk": LayerState("extreme_risk", "redesigned", candidate_status="deployed")}
    transition(states, "extreme_risk", "inspiration_pending", pending_since_generation=10)
    assert states["extreme_risk"].status == "inspiration_pending"


def test_transition_illegal_raises():
    """redesigned → optimizing directly is illegal."""
    import pytest
    states = {"extreme_risk": LayerState("extreme_risk", "redesigned")}
    with pytest.raises(ValueError, match="illegal"):
        transition(states, "extreme_risk", "optimizing")


def test_load_save_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3,
                                              candidate_status="pending_review")}
        save_layer_states(state_path, "Gold2", states)
        loaded = load_layer_states(state_path, "Gold2")
        assert loaded["extreme_risk"].status == "inspiration_pending"
        assert loaded["extreme_risk"].candidate_status == "pending_review"


def test_load_missing_strategy_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        loaded = load_layer_states(str(Path(d) / "state.json"), "Nonexistent")
        assert loaded == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_layer_state.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/inspiration/__init__.py`:
```python
"""Strategy inspiration loop. Spec docs/superpowers/specs/2026-07-13-strategy-inspiration-design.md."""
```

`Scripts/inspiration/layer_state.py`:
```python
"""LayerState + state machine + persistence. Spec §1.2, §2.3."""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

_LEGAL_TRANSITIONS = {
    ("optimizing", "inspiration_pending"),
    ("inspiration_pending", "redesigned"),
    ("inspiration_pending", "optimizing"),
    ("redesigned", "inspiration_pending"),
}


@dataclass
class LayerState:
    layer: str
    status: str = "optimizing"
    pending_since_generation: int = -1
    inspired_strategy_id: str = ""
    retired_shaping_terms: list = field(default_factory=list)
    candidate_deployed: bool = False
    candidate_status: str = "none"


def transition(states: dict, layer: str, new_status: str, **kwargs):
    ls = states.get(layer)
    if ls is None:
        ls = LayerState(layer=layer)
        states[layer] = ls
    if (ls.status, new_status) not in _LEGAL_TRANSITIONS:
        raise ValueError(f"illegal transition: {ls.status} → {new_status} for layer {layer}")
    ls.status = new_status
    if new_status == "optimizing":
        ls.pending_since_generation = -1
        ls.inspired_strategy_id = ""
    for k, v in kwargs.items():
        if hasattr(ls, k):
            setattr(ls, k, v)


def load_layer_states(state_path: str, strategy: str) -> dict:
    p = Path(state_path)
    if not p.exists():
        return {}
    try:
        state = json.loads(p.read_text())
    except Exception:
        return {}
    raw = state.get("layer_states", {}).get(strategy, {})
    return {layer: LayerState(**data) for layer, data in raw.items()}


def save_layer_states(state_path: str, strategy: str, states: dict):
    p = Path(state_path)
    state = {}
    if p.exists():
        try:
            state = json.loads(p.read_text())
        except Exception:
            state = {}
    state.setdefault("layer_states", {})[strategy] = {layer: asdict(ls) for layer, ls in states.items()}
    p.write_text(json.dumps(state, indent=2, default=str))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_layer_state.py -v
```
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/inspiration/__init__.py Scripts/inspiration/layer_state.py Tests/test_inspiration_layer_state.py
git commit -m "feat(inspiration): LayerState + state machine + load/save (§1.2, §2.3)"
```

---

## Task 2: generations 代际日志 log + read_history

**Files:**
- Create: `Scripts/inspiration/generations.py`
- Test: `Tests/test_inspiration_generations.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_generations.py`:
```python
"""Tests for generational log. Spec §2.2."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from generations import log, read_history  # noqa: E402


def test_log_writes_generation_file():
    with tempfile.TemporaryDirectory() as d:
        log("Gold2", 3, {"extreme_risk": {"pnl_pct_of_total": -0.32, "gap": 0.32}},
            {"extreme_risk_contrib_penalty": 2.13}, "pass", repo_root=d)
        gen_file = Path(d) / "Results" / "auto_optimize" / "Gold2" / "generation_3.json"
        assert gen_file.exists()
        doc = json.loads(gen_file.read_text())
        assert doc["strategy"] == "Gold2"
        assert doc["generation"] == 3
        assert doc["layer_gaps"]["extreme_risk"]["gap"] == 0.32
        assert doc["shaping_overrides"]["extreme_risk_contrib_penalty"] == 2.13


def test_read_history_returns_last_n():
    with tempfile.TemporaryDirectory() as d:
        for n in [1, 2, 3]:
            log("Gold2", n, {"extreme_risk": {"gap": 0.3 + n * 0.01}},
                {"extreme_risk_contrib_penalty": 1.0 + n}, "pass", repo_root=d)
        history = read_history("Gold2", 2, repo_root=d)
        assert len(history) == 2


def test_read_history_returns_all_when_fewer_than_n():
    with tempfile.TemporaryDirectory() as d:
        log("Gold2", 1, {"extreme_risk": {"gap": 0.32}}, {}, "pass", repo_root=d)
        history = read_history("Gold2", 3, repo_root=d)
        assert len(history) == 1


def test_read_history_empty_when_no_files():
    with tempfile.TemporaryDirectory() as d:
        history = read_history("Gold2", 3, repo_root=d)
        assert history == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_generations.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/inspiration/generations.py`:
```python
"""Generational log: log() + read_history(). Spec §2.2."""
import json
from datetime import datetime, timezone
from pathlib import Path


def _gen_dir(strategy: str, repo_root: str = ".") -> Path:
    return Path(repo_root) / "Results" / "auto_optimize" / strategy


def log(strategy: str, generation: int, layer_gaps: dict, shaping_overrides: dict,
        review_status: str, repo_root: str = "."):
    d = _gen_dir(strategy, repo_root)
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "strategy": strategy, "generation": generation,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "review_status": review_status, "layer_gaps": layer_gaps,
        "shaping_overrides": shaping_overrides,
    }
    (d / f"generation_{generation}.json").write_text(json.dumps(doc, indent=2, default=str))


def read_history(strategy: str, n: int, repo_root: str = ".") -> list:
    d = _gen_dir(strategy, repo_root)
    if not d.exists():
        return []
    files = sorted(d.glob("generation_*.json"),
                   key=lambda p: int(p.stem.split("_")[1]), reverse=True)
    out = []
    for f in files[:n]:
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_generations.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/inspiration/generations.py Tests/test_inspiration_generations.py
git commit -m "feat(inspiration): generational log log() + read_history() (§2.2)"
```

---

## Task 3: trigger.detect 连续 N 代不收敛(含 ceiling 判据)

**Files:**
- Create: `Scripts/inspiration/trigger.py`
- Test: `Tests/test_inspiration_trigger.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_trigger.py`:
```python
"""Tests for trigger.detect (continuous N-gen non-convergence + ceiling). Spec §2.4, spirit2 #1."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from layer_state import LayerState  # noqa: E402
from trigger import detect  # noqa: E402

_THRESHOLDS = {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1}
_CEILING = 3.0


def _gen(layer, gaps, weights):
    return [
        {"generation": i + 1, "layer_gaps": {layer: {"gap": g}},
         "shaping_overrides": {layer + "_contrib_penalty": w}}
        for i, (g, w) in enumerate(zip(gaps, weights))
    ]


def test_triggers_when_gap_persistent_and_weight_rising():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert "extreme_risk" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)


def test_no_trigger_when_weight_converged():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.32, 0.32], [1.0, 1.03, 1.05])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_no_trigger_when_fewer_than_min_generations():
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.33], [0.5, 1.5])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_ceiling_judges_nonconvergent():
    """spirit2 #1: weight pinned at ceiling [3.0,3.0,3.0] + gap over → non-convergent."""
    states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
    history = _gen("extreme_risk", [0.32, 0.40, 0.60], [3.0, 3.0, 3.0])
    assert "extreme_risk" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)


def test_no_trigger_when_layer_already_inspiration_pending():
    states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending")}
    history = _gen("extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING) == []


def test_generic_layer_name_not_hardcoded():
    states = {"my_custom_layer": LayerState("my_custom_layer", "optimizing")}
    history = _gen("my_custom_layer", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
    assert "my_custom_layer" in detect("Gold2", history, states, _THRESHOLDS, ceiling=_CEILING)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_trigger.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/inspiration/trigger.py`:
```python
"""trigger.detect: continuous N-gen non-convergence + ceiling判据. Spec §2.4, spirit2 #1."""
from .layer_state import LayerState


def detect(strategy: str, last_n_gens: list, layer_states: dict, thresholds: dict,
           ceiling: float = 3.0, eps: float = 0.01) -> list:
    """Return layer names that should enter inspiration_pending."""
    min_gens = thresholds.get("min_generations", 3)
    gap_thresh = thresholds.get("gap_threshold", 0.15)
    delta_thresh = thresholds.get("weight_nonconvergence_delta", 0.1)
    if len(last_n_gens) < min_gens:
        return []

    all_layers = set()
    for gen in last_n_gens:
        all_layers.update(gen.get("layer_gaps", {}).keys())

    to_inspire = []
    for layer in all_layers:
        ls = layer_states.get(layer)
        if ls and ls.status != "optimizing":
            continue

        gaps, weights = [], []
        for gen in last_n_gens:
            g = gen.get("layer_gaps", {}).get(layer, {}).get("gap", 0)
            gaps.append(g)
            w = 0.0
            for term, wv in gen.get("shaping_overrides", {}).items():
                if term.startswith(layer):
                    w = wv
                    break
            weights.append(w)

        if len(gaps) < min_gens or not all(g > gap_thresh for g in gaps):
            continue
        non_convergent = False
        if len(weights) >= 2:
            non_convergent = (weights[-1] - weights[0] > delta_thresh)
            non_convergent = non_convergent or (max(weights) - min(weights) > delta_thresh)
        at_ceiling = all(abs(w - ceiling) < eps for w in weights) if weights else False
        non_convergent = non_convergent or at_ceiling

        if non_convergent:
            to_inspire.append(layer)
    return to_inspire
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_trigger.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/inspiration/trigger.py Tests/test_inspiration_trigger.py
git commit -m "feat(inspiration): trigger.detect continuous N-gen non-convergence + ceiling (§2.4, spirit2 #1)"
```

---

## Task 4: hypothesize.run LLM 假设生成(mock LLM)

**Files:**
- Create: `Scripts/inspiration/hypothesize.py`
- Test: `Tests/test_inspiration_hypothesize.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_hypothesize.py`:
```python
"""Tests for hypothesize.run (mock LLM). Spec §3.1-3.3."""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from hypothesize import run, build_prompt  # noqa: E402

_REVIEW_DOC = {
    "layer_attribution": {
        "extreme_risk": {"pnl_abs": "-1000", "pnl_pct_of_total": -0.32, "n_trades": 50, "n_wins": 10},
    },
    "per_trade_narrative": [
        {"symbol": "518880", "layer_contributions": {"extreme_risk": "-200"}, "entry_time": "2021-01-01"},
    ],
    "drawdown_attribution": [],
}
_GEN_HISTORY = [
    {"generation": 1, "layer_gaps": {"extreme_risk": {"gap": 0.32}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 0.5}},
    {"generation": 2, "layer_gaps": {"extreme_risk": {"gap": 0.33}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 1.5}},
    {"generation": 3, "layer_gaps": {"extreme_risk": {"gap": 0.34}}, "shaping_overrides": {"extreme_risk_contrib_penalty": 2.13}},
]


def test_build_prompt_contains_strategy_and_layer():
    prompt = build_prompt("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                          layer_semantic="extreme risk cap", threshold=0.15)
    assert "Gold2" in prompt
    assert "extreme_risk" in prompt
    assert "0.32" in prompt
    assert "2.13" in prompt


def test_run_writes_markdown_with_mock_llm(tmp_path):
    mock_md = "# 受 Gold2 extreme_risk 层失效启发的策略假设\n\n## 根因诊断\ncap 是二元阈值..." + "x" * 100
    with patch("hypothesize._call_llm", return_value=mock_md):
        path = run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                   {"review": {"layer_names": ["trend", "vol_target", "extreme_risk", "realrate_cap"]}},
                   {"config_key": "code-generation-llm", "model": "glm-5.1", "temperature": 0.7},
                   hypothesis_dir=str(tmp_path))
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "根因诊断" in content
    assert len(content) > 100


def test_run_raises_on_empty_llm_output(tmp_path):
    with patch("hypothesize._call_llm", return_value="short"):
        import pytest
        with pytest.raises(ValueError, match="100"):
            run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                {"review": {"layer_names": ["extreme_risk"]}}, {}, hypothesis_dir=str(tmp_path))


def test_run_raises_on_llm_exception(tmp_path):
    with patch("hypothesize._call_llm", side_effect=RuntimeError("network error")):
        import pytest
        with pytest.raises(RuntimeError, match="network"):
            run("Gold2", "extreme_risk", _REVIEW_DOC, _GEN_HISTORY,
                {"review": {"layer_names": ["extreme_risk"]}}, {}, hypothesis_dir=str(tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_hypothesize.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/inspiration/hypothesize.py`:
```python
"""LLM hypothesis generation. Spec §3.1-3.3."""
import sys
from datetime import datetime, timezone
from pathlib import Path

_SYSTEM_PROMPT = """你是量化策略研究员。任务:基于一个量化策略某因子层的复盘归因 + 代际优化历史,
诊断该层"为什么 reward shaping 修不动",并提出一个替代设计假设(新策略思路)。

约束:
- 只针对指定的失效层提假设,不要泛泛给策略 idea
- 假设必须可落地为 A 股策略(518880/600xxx/000xxx/300xxx/ETF;T+1;100 股整手;不可做空)
- 假设要回答:"该层结构性失效的根因是什么" + "替代设计如何避免该根因"
- 不引入外部资料,只基于提供的复盘 + 代际数据推理
- 输出 Markdown,包含:根因诊断、替代设计假设、预期改善、A 股落地映射"""


def build_prompt(strategy_name: str, inspired_layer: str, review_doc: dict,
                 gen_history: list, layer_semantic: str, threshold: float) -> str:
    gen_table = "\n".join(
        f"  代 {g['generation']} | gap={g.get('layer_gaps',{}).get(inspired_layer,{}).get('gap',0):.2f} | "
        f"weight={g.get('shaping_overrides',{}).get(inspired_layer + '_contrib_penalty', 0):.2f} | "
        f"review_status={g.get('review_status','?')}"
        for g in gen_history
    ) or "  (无代际历史)"
    weights = [g.get("shaping_overrides", {}).get(inspired_layer + "_contrib_penalty", 0) for g in gen_history]
    w1 = weights[0] if weights else 0
    wN = weights[-1] if weights else 0
    layer_agg = review_doc.get("layer_attribution", {}).get(inspired_layer, {})
    neg_trades = [t for t in review_doc.get("per_trade_narrative", [])
                  if float(t.get("layer_contributions", {}).get(inspired_layer, 0)) < 0][:10]
    neg_trades_str = "\n".join(f"  {t.get('entry_time','?')}: {t.get('layer_contributions',{}).get(inspired_layer)}"
                               for t in neg_trades) or "  (无负贡献交易)"
    return f"""## 策略
{strategy_name}

## 失效层
{inspired_layer} (层语义: {layer_semantic})

## 该层代际历史(最近 {len(gen_history)} 代)
{gen_table}
代际观察: gap 连续 {len(gen_history)} 代超阈 {threshold}, shaping weight 从 {w1:.2f} 升到 {wN:.2f} 未收敛。

## 当前 review.json 该层归因细节
layer_attribution[{inspired_layer}]: {layer_agg}

## 该层 per-trade 负贡献样本(最多 10 笔)
{neg_trades_str}

## 输出要求
Markdown 文档,标题: "受 {strategy_name} {inspired_layer} 层失效启发的策略假设",
含 4 节: 根因诊断 / 替代设计假设 / 预期改善 / A 股落地映射。"""


def _call_llm(system_prompt: str, user_prompt: str, llm_cfg: dict) -> str:
    """Call glm-5.1 via soloquant_orchestrator HTTP client. Spec §3.1."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts"))
    import json as _json
    from soloquant_orchestrator import create_http_client_from_config, default_post_json
    config_path = Path(__file__).resolve().parents[2] / "Launcher" / "config" / "config-soloquant.json"
    config = _json.loads(config_path.read_text()) if config_path.exists() else {}
    client = create_http_client_from_config(config, post_json=default_post_json, use_code_generation_llm=True)
    model = llm_cfg.get("model", "glm-5.1")
    request_payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": llm_cfg.get("temperature", 0.7),
        "max_tokens": 4096,
    }
    response = client(request_payload)
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    return str(response)


def run(strategy_name: str, inspired_layer: str, review_doc: dict, gen_history: list,
        manifest_raw: dict, llm_cfg: dict,
        hypothesis_dir: str = "Results/soloquant/local-strategies") -> str:
    layer_names = manifest_raw.get("review", {}).get("layer_names", [])
    layer_semantic = inspired_layer
    threshold = manifest_raw.get("inspiration", {}).get("persistence", {}).get("gap_threshold", 0.15)
    user_prompt = build_prompt(strategy_name, inspired_layer, review_doc, gen_history, layer_semantic, threshold)
    markdown = _call_llm(_SYSTEM_PROMPT, user_prompt, llm_cfg)
    if len(markdown.strip()) < 100:
        raise ValueError(f"LLM output too short ({len(markdown)} chars < 100), not writing hypothesis file")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"{strategy_name}-{inspired_layer}-inspired-{ts}.md"
    out_dir = Path(hypothesis_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(markdown)
    return str(out_path)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_hypothesize.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/inspiration/hypothesize.py Tests/test_inspiration_hypothesize.py
git commit -m "feat(inspiration): hypothesize.run LLM hypothesis generation (§3.1-3.3)"
```

---

## Task 5: provenance.write + 层改善验证

**Files:**
- Create: `Scripts/inspiration/provenance.py`
- Test: `Tests/test_inspiration_provenance.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_provenance.py`:
```python
"""Tests for provenance.write + layer improvement verification. Spec §3.5, §3.7 (spirit2 #2)."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
from provenance import write, verify_layer_improvement  # noqa: E402


def test_write_adds_provenance_block(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"strategy_id": "NewStrategy-abc", "class_name": "NewStrategy"}))
    write(str(manifest_path), parent_strategy="Gold2",
          review_artifact="Results/gold2-betavol/review/review.json",
          inspired_layer="extreme_risk", hypothesis="path/to/hypothesis.md",
          inspired_at_generation=3, improvement_verified=True, layer_improvement=0.22,
          improvement_claim="该层贡献从 -32% → -10% 以内")
    doc = json.loads(manifest_path.read_text())
    assert doc["provenance"]["source"] == "review_inspiration"
    assert doc["provenance"]["parent_strategy"] == "Gold2"
    assert doc["provenance"]["improvement_verified"] is True
    assert doc["provenance"]["layer_improvement"] == 0.22


def test_verify_layer_improvement_pass():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
    assert result["improvement"] > 0
    assert result["improved"] is True


def test_verify_layer_improvement_fail():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
    assert result["improvement"] < 0
    assert result["improved"] is False


def test_verify_layer_improvement_against_claim():
    parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
    new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.15}}}
    result = verify_layer_improvement(parent_review, new_review, "extreme_risk", claim_target=-0.10)
    assert result["improved"] is True
    assert result["claim_met"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_provenance.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/inspiration/provenance.py`:
```python
"""provenance.write + layer improvement verification. Spec §3.5, §3.7 (spirit2 #2)."""
import json
from pathlib import Path


def write(manifest_path: str, parent_strategy: str, review_artifact: str,
          inspired_layer: str, hypothesis: str, inspired_at_generation: int,
          improvement_verified: bool, layer_improvement: float,
          improvement_claim: str = ""):
    p = Path(manifest_path)
    doc = json.loads(p.read_text()) if p.exists() else {}
    doc["provenance"] = {
        "source": "review_inspiration",
        "parent_strategy": parent_strategy,
        "review_artifact": review_artifact,
        "inspired_layer": inspired_layer,
        "hypothesis": hypothesis,
        "inspired_at_generation": inspired_at_generation,
        "improvement_verified": improvement_verified,
        "layer_improvement": layer_improvement,
        "improvement_claim": improvement_claim,
    }
    p.write_text(json.dumps(doc, indent=2, default=str))


def verify_layer_improvement(parent_review: dict, new_review: dict, inspired_layer: str,
                             claim_target: float = None) -> dict:
    """Verify inspired_layer improved in new strategy vs parent. Spec §3.7."""
    old_pct = parent_review.get("layer_attribution", {}).get(inspired_layer, {}).get("pnl_pct_of_total", 0)
    new_pct = new_review.get("layer_attribution", {}).get(inspired_layer, {}).get("pnl_pct_of_total", 0)
    improvement = new_pct - old_pct
    improved = improvement > 0
    claim_met = True
    if claim_target is not None:
        claim_met = new_pct >= claim_target
    return {"improvement": improvement, "improved": improved, "claim_met": claim_met}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_provenance.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/inspiration/provenance.py Tests/test_inspiration_provenance.py
git commit -m "feat(inspiration): provenance.write + layer improvement verification (§3.5, §3.7 spirit2 #2)"
```

---

## Task 6: gold2 manifest `inspiration:` 段

**Files:**
- Modify: `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`
- Test: `Tests/test_inspiration_manifest_gold2.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_manifest_gold2.py`:
```python
"""Tests for gold2 manifest inspiration: block. Spec §2.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_inspiration_block():
    m = load_manifest(MANIFEST)
    insp = m.raw.get("inspiration", {})
    assert insp, "inspiration: block missing"
    assert insp["persistence"]["min_generations"] == 3
    assert insp["persistence"]["gap_threshold"] == 0.15
    assert insp["persistence"]["weight_nonconvergence_delta"] == 0.1
    assert insp["timeout_generations"] == 5
    assert insp["llm"]["config_key"] == "code-generation-llm"
    assert insp["llm"]["model"] == "glm-5.1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_manifest_gold2.py -v
```
Expected: FAIL — inspiration block missing.

- [ ] **Step 3: Write minimal implementation**

Append to end of `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`:
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

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_manifest_gold2.py -v
```
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml Tests/test_inspiration_manifest_gold2.py
git commit -m "feat(gold2): manifest inspiration: block (§2.5)"
```

---

## Task 7: feedback adapter 签名加 layer_states(per-layer 互斥)

**Files:**
- Modify: `Scripts/feedback/adapters/base.py`
- Modify: `Scripts/feedback/adapters/gold2.py`
- Modify: `Scripts/feedback/signals.py`
- Test: `Tests/test_feedback_gold2.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `Tests/test_feedback_gold2.py`:
```python
def test_layer_states_skips_inspiration_pending():
    """Spec §1.4: layer in inspiration_pending → review_drift skips its gap判定."""
    ad = Gold2FeedbackAdapter()
    class _LS:
        status = "inspiration_pending"
    layer_states = {"extreme_risk": _LS()}
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [], layer_states=layer_states)
    assert "extreme_risk" not in action.trigger_reason
    assert "extreme_risk_contrib_penalty" not in action.shaping_overrides


def test_layer_states_none_is_backward_compatible():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert "extreme_risk" in action.trigger_reason
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py::test_layer_states_skips_inspiration_pending -v
```
Expected: FAIL — `feedback_signal` doesn't accept `layer_states`.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/feedback/adapters/base.py` `feedback_signal` signature:
```python
    @abstractmethod
    def feedback_signal(self, manifest, review_doc: dict, state_trace: list, layer_states: dict = None) -> FeedbackAction:
        """Read review.json + state_trace → FeedbackAction. Pure function.
        layer_states: per-layer state dict (inspiration互斥); None = all optimizing."""
```

Modify `Scripts/feedback/adapters/gold2.py` — add `layer_states=None` param to `feedback_signal` + `_check_triggers` + `_compute_shaping_overrides`; in both, skip layers where `ls.status != "optimizing"`:
```python
    def feedback_signal(self, manifest, review_doc, state_trace, layer_states=None):
        # ... (existing) ...
        trigger, reasons = self._check_triggers(review_doc, thresholds, layer_states)
        shaping = self._compute_shaping_overrides(review_doc, thresholds, term_map, layer_states)
        # ... (rest unchanged) ...

    def _check_triggers(self, review_doc, thresholds, layer_states=None):
        # ... (existing review_status + min_trades) ...
        # in the layer_gap loop:
            ls = layer_states.get(layer) if layer_states else None
            if ls and getattr(ls, "status", "optimizing") != "optimizing":
                continue
            # ... gap判定 ...

    def _compute_shaping_overrides(self, review_doc, thresholds, term_map, layer_states=None):
        # ... in the loop:
            ls = layer_states.get(layer) if layer_states else None
            if ls and getattr(ls, "status", "optimizing") != "optimizing":
                continue
            # ... weight计算 ...
```

Modify `Scripts/feedback/signals.py` `orchestrate`:
```python
def orchestrate(manifest, results_dir, layer_states=None):
    # ... (existing) ...
    return adapter.feedback_signal(manifest, review_doc, state_trace, layer_states)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py Tests/test_feedback_orchestrate.py -v 2>&1 | tail -10
```
Expected: PASS (all incl 2 new).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/adapters/base.py Scripts/feedback/adapters/gold2.py Scripts/feedback/signals.py Tests/test_feedback_gold2.py
git commit -m "feat(feedback): feedback_signal + layer_states param (per-layer mutex §1.4)"
```

---

## Task 8: evolution_scheduler 第 6 触发器 + 代际日志 + hypothesize + 超时回收

**Files:**
- Modify: `Scripts/auto_optimize/evolution_scheduler.py`
- Test: `Tests/test_inspiration_trigger_e2e.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_trigger_e2e.py`:
```python
"""Tests for evolution_scheduler 6th trigger + generation log + timeout. Spec §4."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import check_triggers  # noqa: E402


class _FakeManifest:
    strategy_name = "Gold2"
    raw = {
        "feedback": {"adapter_module": "adapters.gold2", "adapter_class": "Gold2FeedbackAdapter",
            "trigger_thresholds": {"review_status_fail": True, "max_layer_attribution_gap": 0.15, "min_narrative_trades": 20}},
        "inspiration": {"persistence": {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1},
                        "timeout_generations": 5},
    }


def _write_gen_history(repo_root, strategy, layer, gaps, weights):
    gen_dir = Path(repo_root) / "Results" / "auto_optimize" / strategy
    gen_dir.mkdir(parents=True, exist_ok=True)
    for i, (g, w) in enumerate(zip(gaps, weights), 1):
        (gen_dir / f"generation_{i}.json").write_text(json.dumps({
            "strategy": strategy, "generation": i, "review_status": "pass",
            "layer_gaps": {layer: {"gap": g}}, "shaping_overrides": {layer + "_contrib_penalty": w},
        }))


def test_inspiration_trigger_true_when_3_gen_nonconvergent():
    with tempfile.TemporaryDirectory() as d:
        _write_gen_history(d, "Gold2", "extreme_risk", [0.32, 0.33, 0.34], [0.5, 1.5, 2.13])
        state_path = str(Path(d) / "state.json")
        Path(state_path).write_text(json.dumps({"generation_count": 3}))
        state = {"_state_path": state_path, "generation_count": 3, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("inspiration") is True


def test_inspiration_false_when_fewer_than_3_gen():
    with tempfile.TemporaryDirectory() as d:
        _write_gen_history(d, "Gold2", "extreme_risk", [0.32, 0.33], [0.5, 1.5])
        state = {"_state_path": str(Path(d) / "state.json"), "generation_count": 2, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("inspiration") is False


def test_timeout_recycles_inspiration_pending_to_optimizing():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        Path(state_path).write_text(json.dumps({
            "generation_count": 10,
            "layer_states": {"Gold2": {"extreme_risk": {"status": "inspiration_pending", "pending_since_generation": 1,
                "inspired_strategy_id": "", "retired_shaping_terms": [], "candidate_deployed": False, "candidate_status": "none"}}}
        }))
        state = {"_state_path": state_path, "generation_count": 10, "last_optimize_date": "2026-07-13T00:00:00"}
        config = {"ppo_training": {"train_window_days": 504}}
        check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        from layer_state import load_layer_states
        states = load_layer_states(state_path, "Gold2")
        assert states["extreme_risk"].status == "optimizing"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_trigger_e2e.py -v
```
Expected: FAIL — `inspiration` trigger not present.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/evolution_scheduler.py` — add 6th trigger in `check_triggers` (before `return triggers`):
```python
    # 6. inspiration (启发新策略触发器, spec §4.2)
    triggers["inspiration"] = False
    if results_dir and manifest:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
            from trigger import detect as detect_inspiration
            from layer_state import load_layer_states, transition, save_layer_states
            from generations import read_history
            _insp_cfg = (manifest.raw if manifest else {}).get("inspiration", {})
            if _insp_cfg:
                _strat = manifest.strategy_name
                _sp = state.get("_state_path", "")
                _states = load_layer_states(_sp, _strat)
                _min_gens = _insp_cfg.get("persistence", {}).get("min_generations", 3)
                _history = read_history(_strat, _min_gens, repo_root=str(_REPO_ROOT))
                _to_inspire = detect_inspiration(_strat, _history, _states,
                    _insp_cfg.get("persistence", {}), ceiling=3.0)
                for _layer in _to_inspire:
                    transition(_states, _layer, "inspiration_pending",
                               pending_since_generation=state.get("generation_count", 0))
                    print(f"  [inspiration] layer '{_layer}' → inspiration_pending")
                # timeout回收
                _timeout = _insp_cfg.get("timeout_generations", 5)
                _gen_now = state.get("generation_count", 0)
                for _layer, _ls in list(_states.items()):
                    if _ls.status == "inspiration_pending":
                        if _gen_now - _ls.pending_since_generation >= _timeout:
                            transition(_states, _layer, "optimizing")
                            print(f"  [inspiration] layer '{_layer}' timeout, 打回 optimizing")
                save_layer_states(_sp, _strat, _states)
                if _to_inspire:
                    triggers["inspiration"] = True
                    state["inspired_layers"] = _to_inspire
        except Exception as ex:
            print(f"  [inspiration] detect failed: {ex}")
    return triggers
```

Add generation log + hypothesize call in `fire_optimization` (before `return True`):
```python
    # Spec §4.1: 代际日志
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
        from generations import log as log_generation
        _m = load_manifest(manifest_path)
        _fb_action = _load_feedback_action(state_path + ".feedback.json")
        _shaping = _fb_action.shaping_overrides if _fb_action else {}
        _layer_gaps = {}
        _review_status = "unknown"
        for _cand in [_REPO_ROOT / "Results" / _m.strategy_name / "review" / "review.json",
                      _REPO_ROOT / "Results" / "gold2-betavol" / "review" / "review.json"]:
            if _cand.exists():
                _rj = json.loads(_cand.read_text())
                _review_status = "pass"
                for _layer, _agg in _rj.get("layer_attribution", {}).items():
                    _layer_gaps[_layer] = {"pnl_pct_of_total": _agg.get("pnl_pct_of_total", 0),
                                            "gap": abs(_agg.get("pnl_pct_of_total", 0))}
                break
        _gen_n = state.get("generation_count", 0) + 1
        state["generation_count"] = _gen_n
        log_generation(_m.strategy_name, _gen_n, _layer_gaps, _shaping, _review_status, repo_root=str(_REPO_ROOT))
    except Exception as ex:
        print(f"  [generations] non-blocking failure: {ex}")

    # Spec §4.4: inspiration hypothesize
    _inspired = state.get("inspired_layers", [])
    if _inspired:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "inspiration"))
            from hypothesize import run as run_hypothesize
            from generations import read_history
            _m = load_manifest(manifest_path)
            _insp_cfg = _m.raw.get("inspiration", {})
            _min_gens = _insp_cfg.get("persistence", {}).get("min_generations", 3)
            _review_doc = {}
            for _cand in [_REPO_ROOT / "Results" / _m.strategy_name / "review" / "review.json",
                          _REPO_ROOT / "Results" / "gold2-betavol" / "review" / "review.json"]:
                if _cand.exists():
                    _review_doc = json.loads(_cand.read_text())
                    break
            _history = read_history(_m.strategy_name, _min_gens, repo_root=str(_REPO_ROOT))
            for _layer in _inspired:
                try:
                    _md_path = run_hypothesize(_m.strategy_name, _layer, _review_doc, _history,
                                               _m.raw, _insp_cfg.get("llm", {}))
                    print(f"  [inspiration] wrote hypothesis: {_md_path}")
                except Exception as ex:
                    print(f"  [inspiration] hypothesize layer '{_layer}' failed: {ex}")
            state["inspired_layers"] = []
        except Exception as ex:
            print(f"  [inspiration] non-blocking failure: {ex}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_trigger_e2e.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/evolution_scheduler.py Tests/test_inspiration_trigger_e2e.py
git commit -m "feat(inspiration): evolution_scheduler 6th trigger + generation log + hypothesize + timeout (§4)"
```

---

## Task 9: soloquant_orchestrator origin enum + deploy checklist 待审提醒

**Files:**
- Modify: `Scripts/soloquant_orchestrator.py`
- Modify: `Scripts/auto_optimize/evolution_scheduler.py`
- Test: `Tests/test_inspiration_origin_enum.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_origin_enum.py`:
```python
"""Tests for origin enum review_inspiration. Spec §3.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts"))
from soloquant_orchestrator import normalize_origin  # noqa: E402


def test_review_inspiration_origin():
    assert normalize_origin("review_inspiration") == "review_inspiration"


def test_existing_origins_unchanged():
    assert normalize_origin("local") == "local"
    assert normalize_origin("cli") == "cli"
    assert normalize_origin("arxiv q-fin") == "web"
    assert normalize_origin(None) == "web"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_origin_enum.py -v
```
Expected: FAIL — `normalize_origin("review_inspiration")` returns "web".

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/soloquant_orchestrator.py` `normalize_origin` — add `review_inspiration` branch:
```python
    if src == "review_inspiration":
        return "review_inspiration"
```
(Insert after the `if src == "cli"` branch, before `data_driven_` check.)

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_origin_enum.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/soloquant_orchestrator.py Tests/test_inspiration_origin_enum.py
git commit -m "feat(inspiration): origin enum review_inspiration (§3.5)"
```

---

## Task 10: 端到端集成验证

**Files:**
- Test: `Tests/test_inspiration_e2e.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_inspiration_e2e.py`:
```python
"""End-to-end: 3 gen non-convergent → trigger → hypothesis MD → provenance → redesigned. Spec §5.2."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "inspiration"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from layer_state import load_layer_states, LayerState, transition, save_layer_states  # noqa: E402
from hypothesize import run as run_hypothesize  # noqa: E402
from provenance import write, verify_layer_improvement  # noqa: E402


def test_e2e_trigger_to_hypothesis():
    with tempfile.TemporaryDirectory() as d:
        gen_dir = Path(d) / "Results" / "auto_optimize" / "Gold2"
        gen_dir.mkdir(parents=True)
        for i, (g, w) in enumerate([(0.32, 0.5), (0.33, 1.5), (0.34, 2.13)], 1):
            (gen_dir / f"generation_{i}.json").write_text(json.dumps({
                "strategy": "Gold2", "generation": i, "review_status": "pass",
                "layer_gaps": {"extreme_risk": {"gap": g}},
                "shaping_overrides": {"extreme_risk_contrib_penalty": w},
            }))
        from trigger import detect
        from generations import read_history
        states = {"extreme_risk": LayerState("extreme_risk", "optimizing")}
        hist = read_history("Gold2", 3, repo_root=d)
        to_inspire = detect("Gold2", hist, states, {"min_generations": 3, "gap_threshold": 0.15, "weight_nonconvergence_delta": 0.1})
        assert "extreme_risk" in to_inspire

        mock_md = "# 受 Gold2 extreme_risk 层失效启发的策略假设\n\n## 根因诊断\ncap失效..." + "x" * 100
        hypo_dir = str(Path(d) / "local-strategies")
        with patch("hypothesize._call_llm", return_value=mock_md):
            md_path = run_hypothesize("Gold2", "extreme_risk", {}, hist,
                                      {"review": {"layer_names": ["extreme_risk"]},
                                       "inspiration": {"persistence": {"gap_threshold": 0.15}}},
                                      {"model": "glm-5.1", "temperature": 0.7}, hypothesis_dir=hypo_dir)
        assert Path(md_path).exists()
        assert len(Path(md_path).read_text()) > 100


def test_e2e_provenance_and_layer_state_redesigned():
    with tempfile.TemporaryDirectory() as d:
        state_path = str(Path(d) / "state.json")
        states = {"extreme_risk": LayerState("extreme_risk", "inspiration_pending", pending_since_generation=3)}
        save_layer_states(state_path, "Gold2", states)
        parent_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.32}}}
        new_review = {"layer_attribution": {"extreme_risk": {"pnl_pct_of_total": -0.10}}}
        result = verify_layer_improvement(parent_review, new_review, "extreme_risk")
        assert result["improved"] is True
        manifest_path = Path(d) / "new_manifest.json"
        manifest_path.write_text(json.dumps({"strategy_id": "NewStrategy-abc"}))
        write(str(manifest_path), "Gold2", "review.json", "extreme_risk", "hypothesis.md", 3,
              improvement_verified=True, layer_improvement=result["improvement"])
        states = load_layer_states(state_path, "Gold2")
        transition(states, "extreme_risk", "redesigned", inspired_strategy_id="NewStrategy-abc",
                   retired_shaping_terms=["extreme_risk_contrib_penalty"], candidate_status="pending_review")
        save_layer_states(state_path, "Gold2", states)
        loaded = load_layer_states(state_path, "Gold2")
        assert loaded["extreme_risk"].status == "redesigned"
        assert loaded["extreme_risk"].candidate_status == "pending_review"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_e2e.py -v
```
Expected: FAIL — wiring incomplete.

- [ ] **Step 3: Verify wiring (no new code)**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_*.py -v 2>&1 | tail -15
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_e2e.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Final full suite**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_inspiration_*.py Tests/test_feedback_gold2.py Tests/test_feedback_orchestrate.py 2>&1 | tail -5
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add Tests/test_inspiration_e2e.py
git commit -m "feat(inspiration): e2e integration — trigger→hypothesis→provenance→redesigned"
```

---

## Self-Review(已执行)

**1. Spec coverage:**
- §1.2 LayerState + 状态机 → Task 1
- §1.4 per-layer 互斥 → Task 7
- §2.1 manifest inspiration 段 → Task 6
- §2.2 代际日志 → Task 2
- §2.3 状态机持久化 → Task 1
- §2.4 trigger 不收敛(含 ceiling) → Task 3
- §3.1-3.3 hypothesize → Task 4
- §3.5 provenance → Task 5
- §3.7 层改善验证 → Task 5
- §4.1 代际日志写入 → Task 8
- §4.2 第 6 触发器 → Task 8
- §4.3 per-layer 互斥 → Task 7
- §4.4 hypothesize 调用 → Task 8
- §4.5 超时回收 → Task 8
- §4.6 redesigned 回写 → Task 10(e2e)
- spirit2 #1 ceiling → Task 3
- spirit2 #2 层改善验证 → Task 5
- spirit2 #3 candidate_status → Task 1
- origin enum → Task 9

**2. Placeholder scan:** 无 TBD/TODO。所有代码完整。

**3. Type consistency:**
- `LayerState` 字段跨 task 一致
- `detect(strategy, last_n_gens, layer_states, thresholds, ceiling=3.0, eps=0.01)` 跨 Task 3/8 一致
- `run(strategy_name, inspired_layer, review_doc, gen_history, manifest_raw, llm_cfg, hypothesis_dir)` 跨 Task 4/8 一致
- `write(manifest_path, ..., improvement_verified, layer_improvement, improvement_claim)` 跨 Task 5/10 一致
- `orchestrate(manifest, results_dir, layer_states=None)` 跨 Task 7 一致

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-strategy-inspiration-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
