# 复盘反馈优化环路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 StrategyFeedbackAdapter ABC + manifest `feedback:` 段 + 三层全闭环(review_drift 触发器 / layer_contrib_penalty reward shaping / observation 扩展)+ deploy_gate checklist,gold2 首个 adapter,零 LEAN native 改动。

**Architecture:** Python feedback 层在 `Scripts/feedback/`,orchestrator 合并 review.json+sidecar→review_doc,adapter 产 FeedbackAction;evolution_scheduler 第 5 触发器读 sidecar;offline_rl_trainer reward 源统一到 manifest shaping + 合并 feedback overrides;trace_to_mdp_dataset observation/reward 读 manifest 字段。gold2 策略侧补 drawdown/pnl/close 3 字段。

**Tech Stack:** Python 3(.11)、stdlib `importlib`/`json`/`pathlib`、`numpy`、`d3rlpy`、C# .NET 10(仅 gold2 SerializeRlState 补字段)。

**关键事实(实现者必读,源自真实代码核查):**
- `trace_to_mdp_dataset.py:14-22` `_encode_state(s, t)` 硬编码 8 维,签名 `(s, t)`(t 是 bar index)。
- `trace_to_mdp_dataset.py:25-37` `_compute_reward(s_next, alpha, reward_terms, var_budget, max_dd)` — if/elif chain 注册 4 term。
- `gym_env.py:9` `RlRiskEnv.__init__(self, trace_path, reward_terms, var_budget, max_dd, episode_dsr=0.0)`;`gym_env.py:68` `eval_term(term_name, alpha, scaled_pnl, var_excess, dd_excess)`。
- `offline_rl_trainer.py:81-101` CLI:`--trace --output --algorithm --n-steps`,line 92-94 读 `config.yaml:offline_rl.{reward_terms, var_budget, max_dd}`。
- `ppo_trainer.py:9` 读 `manifest.reward_config.shaping`。
- `config.yaml:33-42` `offline_rl` block:`algorithm/var_budget/max_dd/reward_terms(4)/fqe_n_steps`。
- `policy_exporter.py:108` `--obs-dim` default 8;`export_policy_onnx(policy_model, obs_dim, onnx_path)`。
- `evolution_scheduler.py:32-40` `_read_live_metrics`;`check_triggers(config, state, metrics_path)` 现有 4 触发器。
- gold2 `SerializeRlState`(`Gold2BetaVolTargetStrategy.cs:168-184`)当前写 11 标量字段。**缺 `_peakTpv/_prevTpv/_lastClose`**(镜像 OptionVolArb5LayerStrategy.cs:252-265)。
- gold2 已有 `_rlTracePath/_trendDisabled/_extremeCap/_trendAlpha/_portfolio`;OnData 末尾已调 `WriteRlStateTraceIfNeeded`(line 137)。
- review sidecar `review.last_review.json` 在 `Results/<strategy>/review/`,含 `last_review/review_status/review_artifact_path/backtest_id`。
- review.json 含 `run_meta/layer_attribution/per_trade_narrative/drawdown_attribution/tca`。

---

## File Structure

### 新建 — Python feedback 层

| 文件 | 职责 |
|---|---|
| `Scripts/feedback/__init__.py` | package marker |
| `Scripts/feedback/adapters/__init__.py` | adapters subpackage marker |
| `Scripts/feedback/adapters/base.py` | `StrategyFeedbackAdapter` ABC + `FeedbackAction` dataclass |
| `Scripts/feedback/adapters/gold2.py` | `Gold2FeedbackAdapter`(触发器 + per-bar 下采样 + shaping_overrides) |
| `Scripts/feedback/signals.py` | `orchestrate(manifest, results_dir)` 合并 review.json+sidecar+state_trace,dispatch adapter |
| `Tests/test_feedback_base.py` | ABC 合规 + FeedbackAction |
| `Tests/test_feedback_gold2.py` | 触发器 + per-bar 下采样 + shaping_overrides + min_trades gate |
| `Tests/test_feedback_orchestrate.py` | orchestrate 路径约定 + 降级 |
| `Tests/test_reward_injection.py` | reward 源统一 + shaping 合并 |
| `Tests/test_observation_injection.py` | observation_fields 注入 |
| `Tests/test_review_drift_trigger.py` | evolution_scheduler 第 5 触发器 |
| `Tests/test_deploy_checklist.py` | deploy_gate checklist 打印 |
| `Tests/test_feedback_e2e.py` | 全栈集成 |

### 修改 — gold2 策略侧 C#(非 LEAN core)

| 文件 | 改动 |
|---|---|
| `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs` | 加 `_peakTpv/_prevTpv/_lastClose` 字段 + OnData 更新 + SerializeRlState 补 `drawdown/pnl/close` |
| `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` | 加 `feedback:` 段 + state_schema 补 drawdown/pnl + dim_hint |

### 修改 — 管线侧 Python

| 文件 | 改动 |
|---|---|
| `Scripts/auto_optimize/evolution_scheduler.py` | check_triggers 加第 5 触发器 + fire_optimization 打印 checklist |
| `Scripts/auto_optimize/offline_rl_trainer.py` | reward 源改读 manifest shaping + 合并 feedback overrides |
| `Scripts/auto_optimize/trace_to_mdp_dataset.py` | `_encode_state` 读 observation_fields + `_compute_reward` 注册 `layer_contrib_penalty` + `merge_shaping_overrides` |
| `Scripts/auto_optimize/gym_env.py` | `eval_term` 注册 `layer_contrib_penalty` |
| `Scripts/auto_optimize/policy_exporter.py` | `--obs-dim` 默认读 manifest |
| `Scripts/auto_optimize/config.yaml` | 删 `offline_rl.reward_terms` |

### 不改

LEAN core、Layer A Bayesian、review 层、现有 Grafana dashboard。

---

## Task 1: StrategyFeedbackAdapter ABC + FeedbackAction

**Files:**
- Create: `Scripts/feedback/__init__.py`
- Create: `Scripts/feedback/adapters/__init__.py`
- Create: `Scripts/feedback/adapters/base.py`
- Test: `Tests/test_feedback_base.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_feedback_base.py`:
```python
"""Tests for StrategyFeedbackAdapter ABC + FeedbackAction. Spec §3.1."""
import sys
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))

from adapters.base import StrategyFeedbackAdapter, FeedbackAction  # noqa: E402


def test_feedback_action_fields():
    a = FeedbackAction(trigger=True, trigger_reason="review_status=fail",
                       shaping_overrides={"extreme_risk_contrib_penalty": 2.0},
                       observation_fields=["tpv", "w_smooth"], attribution_method="telescoping",
                       per_bar_layer_contrib=[{"trend": 1.0}])
    assert a.trigger is True
    assert a.shaping_overrides["extreme_risk_contrib_penalty"] == 2.0
    assert a.observation_fields == ["tpv", "w_smooth"]
    assert a.attribution_method == "telescoping"


def test_abc_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        StrategyFeedbackAdapter()


class _GoodAdapter(StrategyFeedbackAdapter):
    LAYERS = ["trend", "vol_target"]
    def feedback_signal(self, manifest, review_doc, state_trace):
        return FeedbackAction(trigger=False, trigger_reason="",
                              shaping_overrides={}, observation_fields=[],
                              attribution_method="residual", per_bar_layer_contrib=[])


def test_good_adapter_returns_feedback_action():
    ad = _GoodAdapter()
    action = ad.feedback_signal(None, {"review_status": "pass"}, [])
    assert isinstance(action, FeedbackAction)
    assert action.trigger is False


def test_feedback_action_default_per_bar_empty():
    a = FeedbackAction(trigger=False, trigger_reason="", shaping_overrides={},
                       observation_fields=[], attribution_method="residual")
    assert a.per_bar_layer_contrib == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_base.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/feedback/__init__.py`:
```python
"""Review-feedback optimization loop. Spec docs/superpowers/specs/2026-07-12-review-feedback-loop-design.md."""
```

`Scripts/feedback/adapters/__init__.py`:
```python
"""Strategy feedback adapters."""
```

`Scripts/feedback/adapters/base.py`:
```python
"""StrategyFeedbackAdapter ABC + FeedbackAction. Spec §3.1.

Generic cross-strategy protocol. feedback_signal() returns a FeedbackAction
driving the 3-layer closed loop: (1) evolution_scheduler review_drift trigger,
(2) reward shaping term layer_contrib_penalty, (3) CQL observation_fields.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FeedbackAction:
    """Output of adapter.feedback_signal; drives the 3-layer closed loop."""
    trigger: bool
    trigger_reason: str
    shaping_overrides: dict
    observation_fields: list
    attribution_method: str
    per_bar_layer_contrib: list = field(default_factory=list)


class StrategyFeedbackAdapter(ABC):
    LAYERS: list

    @abstractmethod
    def feedback_signal(self, manifest, review_doc: dict, state_trace: list) -> FeedbackAction:
        """Read review.json + state_trace → FeedbackAction. Pure function."""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_base.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/__init__.py Scripts/feedback/adapters/__init__.py Scripts/feedback/adapters/base.py Tests/test_feedback_base.py
git commit -m "feat(feedback): StrategyFeedbackAdapter ABC + FeedbackAction"
```

---

## Task 2: gold2 manifest `feedback:` 段 + state_schema 补 drawdown/pnl

**Files:**
- Modify: `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`
- Test: `Tests/test_feedback_manifest_gold2.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_feedback_manifest_gold2.py`:
```python
"""Tests for gold2 manifest feedback: block + state_schema drawdown/pnl. Spec §2.4, §3.5."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from manifest_loader import load_manifest  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"


def test_manifest_has_feedback_block():
    m = load_manifest(MANIFEST)
    fb = m.raw.get("feedback", {})
    assert fb, "feedback: block missing"
    assert fb["adapter_module"] == "feedback.adapters.gold2"
    assert fb["adapter_class"] == "Gold2FeedbackAdapter"
    assert fb["trigger_thresholds"]["max_layer_attribution_gap"] == 0.15
    assert fb["trigger_thresholds"]["min_narrative_trades"] == 20
    assert "extreme_risk" in fb["shaping_term_map"]
    assert "realrate_cap" in fb["shaping_term_map"]


def test_state_schema_has_drawdown_pnl():
    m = load_manifest(MANIFEST)
    names = {f.name for f in m.state_schema.fields}
    assert "drawdown" in names
    assert "pnl" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_manifest_gold2.py -v
```
Expected: FAIL — feedback block missing, drawdown/pnl missing.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`:

(a) Extend `state_schema.fields` — add `drawdown` and `pnl` after `trend_disabled`:
```yaml
    - {name: trend_disabled, type: bool}
    - {name: drawdown, type: float}
    - {name: pnl, type: float}
```
Update `dim_hint` to the new count (original 9 + 4 review + 2 = 15): `dim_hint: 15`.

(b) Add `feedback:` block at end of file (after `review:` block):
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

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_manifest_gold2.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml Tests/test_feedback_manifest_gold2.py
git commit -m "feat(gold2): manifest feedback: block + state_schema drawdown/pnl"
```

---

## Task 3: gold2 C# SerializeRlState 补 drawdown/pnl/close + _peakTpv/_prevTpv/_lastClose

**Files:**
- Modify: `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`
- Test: `Tests/Algorithm/Gold2/Gold2FeedbackStateExportTests.cs`

> 非 LEAN core。镜像 OptionVolArb5LayerStrategy.cs:252-265 的 `_peakTpv/_prevTpv` 模式。

- [ ] **Step 1: Write the failing test**

`Tests/Algorithm/Gold2/Gold2FeedbackStateExportTests.cs`:
```csharp
using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;
using QuantConnect.Algorithm.CSharp.Models.Gold2;

namespace QuantConnect.Tests.Algorithm.Gold2
{
    [TestFixture]
    public class Gold2FeedbackStateExportTests
    {
        [Test]
        public void SerializeRlState_Exists_And_Has_PeakPrevClose_Fields()
        {
            var t = typeof(Gold2BetaVolTargetStrategy);
            Assert.IsNotNull(t.GetMethod("SerializeRlState"), "SerializeRlState missing");
            Assert.IsNotNull(t.GetField("_peakTpv",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_peakTpv field missing");
            Assert.IsNotNull(t.GetField("_prevTpv",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_prevTpv field missing");
            Assert.IsNotNull(t.GetField("_lastClose",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance),
                "_lastClose field missing");
        }

        [Test]
        public void TrendAlphaModel_Still_Exposes_LastDirCoef()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, Symbol.Empty, 0.2m, false);
            Assert.AreEqual(0m, model.LastDirCoef);
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && export PATH="/usr/local/dotnet:$PATH" && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2FeedbackStateExportTests" 2>&1 | tail -5
```
Expected: FAIL — `_peakTpv`/`_prevTpv`/`_lastClose` not found.

- [ ] **Step 3: Write minimal implementation**

Modify `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`:

(a) Add fields after `_portfolio` field (near line 46):
```csharp
        // Feedback-loop fields (spec §3.5): drawdown + per-bar pnl + close for per-bar telescoping.
        // Mirrors OptionVolArb5LayerStrategy.cs:252-265.
        private decimal _peakTpv;
        private decimal _prevTpv;
        private decimal _lastClose;
```

(b) In `OnData`, in the gold bar handling block (where `bar518880.Close` is read, ~line 87), store `_lastClose`:
```csharp
            if (data.Bars.TryGetValue(_gold, out var bar518880))
            {
                _trend.Update518880(bar518880.Close, bar518880.EndTime);
                _vol.Update(bar518880.Close, bar518880.EndTime);
                _lastClose = bar518880.Close;   // Feedback §3.5: for per-bar telescoping
                // ... existing RVol logic ...
            }
```

(c) At END of `OnData` (after `WriteRlStateTraceIfNeeded(data.Time);` at line 137), add peak/prev update:
```csharp
            WriteRlStateTraceIfNeeded(data.Time);
            // Feedback §3.5: update peak/prev for drawdown + per-bar pnl.
            var tpvNow = Portfolio.TotalPortfolioValue;
            if (tpvNow > _peakTpv) _peakTpv = tpvNow;
            _prevTpv = tpvNow;
```

(d) Extend `SerializeRlState` — add `drawdown`, `pnl`, `close`:
```csharp
            var tpv = Portfolio.TotalPortfolioValue;
            var drawdown = _peakTpv > 0 ? Math.Max(0m, (_peakTpv - tpv) / _peakTpv) : 0m;
            var pnl = _prevTpv > 0 ? (tpv - _prevTpv) / _prevTpv : 0m;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new { sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv }).ToList();
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "Gold2BetaVolTargetStrategy",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                w_smooth = _vol.Compute(_gold, algo.Time).Value,
                trend_dir = (int)_trend.Compute(_gold, algo.Time).Value,
                extreme_triggered = (int)_ext.Compute(_gold, algo.Time).Value == 1,
                realrate_cap = _realrate.Compute(_gold, algo.Time).Value,
                dir_coef = _trendAlpha.LastDirCoef,
                w_after_vol = _portfolio.LastActualWeight,
                extreme_cap = _extremeCap,
                trend_disabled = _trendDisabled,
                drawdown, pnl, close = _lastClose
            });
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && export PATH="/usr/local/dotnet:$PATH" && dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj 2>&1 | tail -3
cd /home/project/hope/Lean && export PATH="/usr/local/dotnet:$PATH" && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2FeedbackStateExportTests" 2>&1 | tail -5
```
Expected: build 0 errors; 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs Tests/Algorithm/Gold2/Gold2FeedbackStateExportTests.cs
git commit -m "feat(gold2): SerializeRlState +drawdown/pnl/close + _peakTpv/_prevTpv/_lastClose (feedback §3.5)"
```

---

## Task 4: Gold2FeedbackAdapter — 触发器 + min_trades gate + shaping_overrides

**Files:**
- Create: `Scripts/feedback/adapters/gold2.py`
- Test: `Tests/test_feedback_gold2.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_feedback_gold2.py`:
```python
"""Tests for Gold2FeedbackAdapter trigger + min_trades gate + shaping_overrides. Spec §3.2, §3.3."""
import sys
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from adapters.base import FeedbackAction  # noqa: E402
from adapters.gold2 import Gold2FeedbackAdapter  # noqa: E402


def _manifest(thresholds=None, term_map=None, obs_fields=None):
    class _M:
        raw = {
            "feedback": {
                "trigger_thresholds": thresholds or {"review_status_fail": True,
                    "max_layer_attribution_gap": 0.15, "min_narrative_trades": 20},
                "shaping_term_map": term_map or {"extreme_risk": "extreme_risk_contrib_penalty",
                                                  "realrate_cap": "realrate_cap_contrib_penalty"},
                "observation_fields": obs_fields or ["tpv", "w_smooth"],
            }
        }
    return _M()


def _review(status="pass", layer_pcts=None, n_trades=100):
    return {
        "review_status": status,
        "layer_attribution": {
            "trend": {"pnl_pct_of_total": (layer_pcts or {}).get("trend", 0.5)},
            "vol_target": {"pnl_pct_of_total": (layer_pcts or {}).get("vol_target", 0.3)},
            "extreme_risk": {"pnl_pct_of_total": (layer_pcts or {}).get("extreme_risk", -0.1)},
            "realrate_cap": {"pnl_pct_of_total": (layer_pcts or {}).get("realrate_cap", 0.2)},
        },
        "per_trade_narrative": list(range(n_trades)),
        "run_meta": {"attribution_method": "telescoping"},
    }


def test_layers_constant():
    assert Gold2FeedbackAdapter.LAYERS == ["trend", "vol_target", "extreme_risk", "realrate_cap"]


def test_review_status_fail_triggers():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(status="fail"), [])
    assert action.trigger is True
    assert "review_status=fail" in action.trigger_reason


def test_layer_gap_triggers():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is True
    assert "extreme_risk" in action.trigger_reason


def test_no_trigger_when_pass_small_gap():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.05}), [])
    assert action.trigger is False


def test_min_trades_gate_skips_layer_gap():
    """Spec §3.2 eval-update2 #3: n_trades<min → layer_gap skipped."""
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(n_trades=5, layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is False
    assert "small-sample" in action.trigger_reason or "skipped" in action.trigger_reason


def test_min_trades_gate_keeps_review_status_fail():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(status="fail", n_trades=5, layer_pcts={"extreme_risk": -0.32}), [])
    assert action.trigger is True
    assert "review_status=fail" in action.trigger_reason


def test_shaping_overrides_weight():
    """Spec §3.3: weight = clamp(gap/0.15, 0.5, 3.0). extreme_risk gap=0.32 → 2.13."""
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.32}), [])
    assert "extreme_risk_contrib_penalty" in action.shaping_overrides
    assert abs(action.shaping_overrides["extreme_risk_contrib_penalty"] - 2.13) < 0.01


def test_shaping_overrides_empty_when_no_gap():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(layer_pcts={"extreme_risk": -0.05}), [])
    assert action.shaping_overrides == {}


def test_no_state_trace_per_bar_empty():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(), [])
    assert action.per_bar_layer_contrib == []


def test_attribution_method_passthrough():
    ad = Gold2FeedbackAdapter()
    action = ad.feedback_signal(_manifest(), _review(), [])
    assert action.attribution_method == "telescoping"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters.gold2'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/feedback/adapters/gold2.py`:
```python
"""Gold2FeedbackAdapter — trigger + min_trades gate + shaping_overrides. Spec §3.2, §3.3.

per-bar 下采样 (§3.3) 在 Task 5 实现;本 task 做触发器 + shaping_overrides + observation_fields 透传。
"""
from .base import StrategyFeedbackAdapter, FeedbackAction


class Gold2FeedbackAdapter(StrategyFeedbackAdapter):
    LAYERS = ["trend", "vol_target", "extreme_risk", "realrate_cap"]

    def feedback_signal(self, manifest, review_doc, state_trace):
        fb = (manifest.raw if manifest else {}).get("feedback", {})
        thresholds = fb.get("trigger_thresholds", {})
        term_map = fb.get("shaping_term_map", {})
        obs_fields = fb.get("observation_fields", [])

        trigger, reasons = self._check_triggers(review_doc, thresholds)
        shaping = self._compute_shaping_overrides(review_doc, thresholds, term_map)
        per_bar = self._downsample_per_bar(state_trace) if state_trace else []

        return FeedbackAction(
            trigger=trigger,
            trigger_reason="; ".join(reasons),
            shaping_overrides=shaping,
            observation_fields=obs_fields,
            attribution_method=review_doc.get("run_meta", {}).get("attribution_method", "residual"),
            per_bar_layer_contrib=per_bar,
        )

    def _check_triggers(self, review_doc, thresholds):
        """Spec §3.2: review_status=fail (hard) + layer_gap (gated by min_trades)."""
        trigger = False
        reasons = []
        if review_doc.get("review_status") == "fail" and thresholds.get("review_status_fail", True):
            trigger = True
            reasons.append("review_status=fail")
        n_trades = len(review_doc.get("per_trade_narrative", []))
        min_trades = thresholds.get("min_narrative_trades", 20)
        if n_trades < min_trades:
            reasons.append(f"warn: n_trades={n_trades}<{min_trades}, layer_gap skipped (small-sample)")
        else:
            gap_thresh = thresholds.get("max_layer_attribution_gap", 0.15)
            for layer, agg in review_doc.get("layer_attribution", {}).items():
                pct = abs(agg.get("pnl_pct_of_total", 0))
                if pct > gap_thresh:
                    trigger = True
                    reasons.append(f"layer_gap {layer}={agg.get('pnl_pct_of_total')}>{gap_thresh}")
        return trigger, reasons

    def _compute_shaping_overrides(self, review_doc, thresholds, term_map):
        """Spec §3.3: weight = clamp(gap/threshold, 0.5, 3.0) for layers exceeding gap."""
        shaping = {}
        gap_thresh = thresholds.get("max_layer_attribution_gap", 0.15)
        for layer, term in term_map.items():
            agg = review_doc.get("layer_attribution", {}).get(layer, {})
            gap = abs(agg.get("pnl_pct_of_total", 0))
            if gap > gap_thresh:
                weight = gap / gap_thresh
                shaping[term] = max(0.5, min(weight, 3.0))
        return shaping

    def _downsample_per_bar(self, state_trace):
        """Spec §3.3: per-bar telescoping. Implemented in Task 5."""
        return []
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py -v
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/adapters/gold2.py Tests/test_feedback_gold2.py
git commit -m "feat(feedback): Gold2FeedbackAdapter trigger + min_trades gate + shaping_overrides"
```

---

## Task 5: gold2 per-bar 层贡献下采样(telescoping)

**Files:**
- Modify: `Scripts/feedback/adapters/gold2.py`
- Test: `Tests/test_feedback_gold2.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `Tests/test_feedback_gold2.py`:
```python
def test_per_bar_downsample_sums_to_telescoping():
    """Spec §3.3: per-bar layer contributions (telescoping)."""
    ad = Gold2FeedbackAdapter()
    # close: 10→11→12 (Δp=+1 each); tpv=1000 → scale=1000/10=100
    # w_trend=1.0, w_vol=0.5, w_ext=0.5 (no trigger), w_real=0.5
    state_trace = [
        {"ts": "2020-07-01T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 10.0},
        {"ts": "2020-07-02T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 11.0},
        {"ts": "2020-07-03T00:00:00", "tpv": 1000.0, "dir_coef": 1.0, "w_after_vol": 0.5,
         "extreme_triggered": False, "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 12.0},
    ]
    per_bar = ad._downsample_per_bar(state_trace)
    assert len(per_bar) == 2  # 2 transitions
    # bar 0→1: Δp=1, scale=100 → C_trend=1.0*100*1=100; C_vol=(0.5-1.0)*100*1=-50; C_ext=0; C_real=0
    assert per_bar[0]["trend"] == 100.0
    assert per_bar[0]["vol_target"] == -50.0
    assert per_bar[0]["extreme_risk"] == 0.0
    assert per_bar[0]["realrate_cap"] == 0.0


def test_per_bar_downsample_empty_on_missing_fields():
    ad = Gold2FeedbackAdapter()
    state_trace = [{"ts": "2020-07-01"}]
    per_bar = ad._downsample_per_bar(state_trace)
    assert per_bar == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py::test_per_bar_downsample_sums_to_telescoping -v
```
Expected: FAIL — `_downsample_per_bar` returns `[]`.

- [ ] **Step 3: Write minimal implementation**

Replace `_downsample_per_bar` stub in `Scripts/feedback/adapters/gold2.py`:
```python
    def _downsample_per_bar(self, state_trace):
        """Spec §3.3: per-bar telescoping layer contributions from state_trace.
        For each bar transition t-1→t:
          scale = tpv_t / close_{t-1}; dp = close_t - close_{t-1}
          C_trend = w_trend * scale * dp
          C_vol_target = (w_vol - w_trend) * scale * dp
          C_extreme_risk = (w_ext - w_vol) * scale * dp
          C_realrate = (w_real - w_ext) * scale * dp
        """
        if not state_trace or len(state_trace) < 2:
            return []
        per_bar = []
        for i in range(1, len(state_trace)):
            prev = state_trace[i - 1]
            curr = state_trace[i]
            try:
                close_prev = float(prev["close"])
                close_curr = float(curr["close"])
                tpv = float(curr["tpv"])
                if close_prev <= 0:
                    continue
                scale = tpv / close_prev
                dp = close_curr - close_prev
                dir_coef = float(curr.get("dir_coef", 1.0))
                w_vol = float(curr.get("w_after_vol", 0.0))
                extreme_triggered = bool(curr.get("extreme_triggered", False))
                extreme_cap = float(curr.get("extreme_cap", 0.3))
                realrate_cap = float(curr.get("realrate_cap", 0.6))
                w_trend = dir_coef
                w_ext = min(w_vol, extreme_cap) if extreme_triggered else w_vol
                w_real = min(w_ext, realrate_cap)
                per_bar.append({
                    "trend": w_trend * scale * dp,
                    "vol_target": (w_vol - w_trend) * scale * dp,
                    "extreme_risk": (w_ext - w_vol) * scale * dp,
                    "realrate_cap": (w_real - w_ext) * scale * dp,
                })
            except (KeyError, ValueError, TypeError):
                continue
        return per_bar
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_gold2.py -v
```
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/adapters/gold2.py Tests/test_feedback_gold2.py
git commit -m "feat(feedback): gold2 per-bar telescoping layer contribution downsample"
```

---

## Task 6: signals.orchestrate(manifest, results_dir) — 路径约定 + 降级

**Files:**
- Create: `Scripts/feedback/signals.py`
- Test: `Tests/test_feedback_orchestrate.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_feedback_orchestrate.py`:
```python
"""Tests for signals.orchestrate. Spec §1.4, §3.2."""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from signals import orchestrate  # noqa: E402


class _FakeManifest:
    def __init__(self, strategy_name, feedback_cfg):
        self.strategy_name = strategy_name
        self.raw = {"feedback": feedback_cfg} if feedback_cfg else {}


def _write_review(results_dir, strategy, review_doc, sidecar):
    rdir = Path(results_dir) / strategy / "review"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "review.json").write_text(json.dumps(review_doc))
    (rdir / "review.last_review.json").write_text(json.dumps(sidecar))


def test_orchestrate_merges_review_and_sidecar():
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}, "per_trade_narrative": [],
                                    "run_meta": {"attribution_method": "telescoping"}},
                      {"review_status": "fail", "last_review": "2026-07-12T00:00:00Z"})
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter"})
        action = orchestrate(m, d)
        assert action.trigger is True
        assert action.attribution_method == "telescoping"


def test_orchestrate_no_sidecar_returns_none():
    with tempfile.TemporaryDirectory() as d:
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter"})
        action = orchestrate(m, d)
        assert action is None


def test_orchestrate_no_feedback_block_returns_none():
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}}, {"review_status": "pass"})
        m = _FakeManifest("Gold2", None)
        action = orchestrate(m, d)
        assert action is None


def test_orchestrate_reads_state_trace_by_convention():
    """Spec §1.4: state_trace at Results/<strategy>/state_trace.jsonl."""
    with tempfile.TemporaryDirectory() as d:
        _write_review(d, "Gold2", {"layer_attribution": {}, "per_trade_narrative": list(range(50)),
                                    "run_meta": {"attribution_method": "telescoping"}},
                      {"review_status": "pass"})
        stpath = Path(d) / "Gold2" / "state_trace.jsonl"
        stpath.parent.mkdir(parents=True, exist_ok=True)
        stpath.write_text(json.dumps({"ts": "2020-07-01", "tpv": 1000.0, "dir_coef": 1.0,
                                       "w_after_vol": 0.5, "extreme_triggered": False,
                                       "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 10.0}) + "\n" +
                          json.dumps({"ts": "2020-07-02", "tpv": 1000.0, "dir_coef": 1.0,
                                       "w_after_vol": 0.5, "extreme_triggered": False,
                                       "extreme_cap": 0.3, "realrate_cap": 0.6, "close": 11.0}))
        m = _FakeManifest("Gold2", {"adapter_module": "adapters.gold2",
                                     "adapter_class": "Gold2FeedbackAdapter",
                                     "trigger_thresholds": {"min_narrative_trades": 20,
                                       "max_layer_attribution_gap": 0.15, "review_status_fail": True}})
        action = orchestrate(m, d)
        assert action is not None
        assert len(action.per_bar_layer_contrib) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_orchestrate.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'signals'`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/feedback/signals.py`:
```python
"""orchestrate(manifest, results_dir): merge review.json + sidecar + state_trace → FeedbackAction. Spec §1.4, §3.2.

Path convention (§1.4): state_trace at Results/<strategy>/state_trace.jsonl (NOT env var).
review.json + sidecar at Results/<strategy>/review/.
"""
import importlib
import json
import sys
from pathlib import Path


def _load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def orchestrate(manifest, results_dir):
    """Merge review.json + sidecar → review_doc, load state_trace by convention,
    dispatch adapter.feedback_signal. Returns FeedbackAction or None."""
    fb = (manifest.raw if manifest else {}).get("feedback", {})
    if not fb:
        return None
    strategy = manifest.strategy_name
    review_dir = Path(results_dir) / strategy / "review"
    review_json = _load_json(review_dir / "review.json")
    sidecar = _load_json(review_dir / "review.last_review.json")
    if not review_json or not sidecar:
        return None
    review_doc = {**review_json, "review_status": sidecar.get("review_status"),
                  "last_review": sidecar.get("last_review")}
    state_trace = []
    st_path = Path(results_dir) / strategy / "state_trace.jsonl"
    if st_path.exists():
        for line in st_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    state_trace.append(json.loads(line))
                except Exception:
                    continue
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    mod = importlib.import_module(fb["adapter_module"])
    adapter = getattr(mod, fb["adapter_class"])()
    return adapter.feedback_signal(manifest, review_doc, state_trace)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_orchestrate.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/signals.py Tests/test_feedback_orchestrate.py
git commit -m "feat(feedback): signals.orchestrate — merge review+sidecar+state_trace (path convention)"
```

---

## Task 7: reward 源统一 + layer_contrib_penalty term 注册

**Files:**
- Modify: `Scripts/auto_optimize/trace_to_mdp_dataset.py`
- Modify: `Scripts/auto_optimize/gym_env.py`
- Modify: `Scripts/auto_optimize/offline_rl_trainer.py`
- Modify: `Scripts/auto_optimize/config.yaml`
- Test: `Tests/test_reward_injection.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_reward_injection.py`:
```python
"""Tests for reward source unification + layer_contrib_penalty. Spec §2.3, §3.3."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from trace_to_mdp_dataset import _compute_reward, merge_shaping_overrides  # noqa: E402


def test_layer_contrib_penalty_registered():
    s_next = {"pnl": 0.01, "_per_bar": [{"extreme_risk": -0.05, "trend": 0.1}]}
    reward_terms = [{"term": "scaled_pnl", "weight": 1.0},
                    {"term": "layer_contrib_penalty", "weight": 2.0}]
    r = _compute_reward(s_next, 1.0, reward_terms, 0.5, 0.05)
    # scaled_pnl=0.01; layer_contrib_penalty=2.0*(-(-0.05))=0.1
    assert abs(r - (0.01 + 0.1)) < 1e-6


def test_layer_contrib_penalty_zero_when_no_neg():
    s_next = {"pnl": 0.01, "_per_bar": [{"trend": 0.1, "extreme_risk": 0.05}]}
    reward_terms = [{"term": "scaled_pnl", "weight": 1.0},
                    {"term": "layer_contrib_penalty", "weight": 2.0}]
    r = _compute_reward(s_next, 1.0, reward_terms, 0.5, 0.05)
    assert abs(r - 0.01) < 1e-6


def test_merge_shaping_overrides_new_term():
    base = [{"term": "scaled_pnl", "weight": 1.0}, {"term": "drawdown_excess_penalty", "weight": 2.0}]
    overrides = {"extreme_risk_contrib_penalty": 2.13}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"]: t["weight"] for t in merged}
    assert terms["extreme_risk_contrib_penalty"] == 2.13
    assert terms["scaled_pnl"] == 1.0


def test_merge_shaping_overrides_overrides_weight():
    base = [{"term": "scaled_pnl", "weight": 1.0}, {"term": "drawdown_excess_penalty", "weight": 2.0}]
    overrides = {"drawdown_excess_penalty": 5.0}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"]: t["weight"] for t in merged}
    assert terms["drawdown_excess_penalty"] == 5.0
    assert len(merged) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_reward_injection.py -v
```
Expected: FAIL — `merge_shaping_overrides` not found; `layer_contrib_penalty` not registered.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/trace_to_mdp_dataset.py`:

(a) Add `merge_shaping_overrides` (after imports):
```python
def merge_shaping_overrides(base_shaping, overrides):
    """Spec §2.3: merge manifest reward_config.shaping with feedback shaping_overrides.
    Overrides replace same-term weight; new terms appended. No duplicates."""
    merged = list(base_shaping)
    for term, weight in overrides.items():
        found = False
        for s in merged:
            if s["term"] == term:
                s["weight"] = weight
                found = True
                break
        if not found:
            merged.append({"term": term, "weight": weight})
    return merged
```

(b) Extend `_compute_reward` — add `per_bar_layer_contrib` param + `layer_contrib_penalty` branch:
```python
def _compute_reward(s_next, alpha, reward_terms, var_budget, max_dd, per_bar_layer_contrib=None):
    scaled_pnl = float(s_next.get("pnl", 0)) * alpha
    r = 0.0
    for term in reward_terms:
        if term["term"] == "scaled_pnl":
            r += term["weight"] * scaled_pnl
        elif term["term"] == "var_excess_penalty":
            r -= term["weight"] * max(0, float(s_next.get("var_1d99", 0)) - var_budget)
        elif term["term"] == "drawdown_excess_penalty":
            r -= term["weight"] * max(0, float(s_next.get("drawdown", 0)) - max_dd)
        elif term["term"] == "over_clearance_penalty":
            if alpha < 0.1: r -= term["weight"]
        elif term["term"] == "layer_contrib_penalty":
            pbar = per_bar_layer_contrib or s_next.get("_per_bar", [])
            if pbar:
                neg_sum = sum(-v for row in pbar for v in row.values() if v < 0)
                r += term["weight"] * neg_sum
    return r
```

Modify `Scripts/auto_optimize/gym_env.py` `eval_term` — add `layer_contrib_penalty`:
```python
def eval_term(term_name, alpha, scaled_pnl, var_excess, dd_excess, per_bar=None):
    if term_name == "layer_contrib_penalty":
        if per_bar:
            neg_sum = sum(-v for row in per_bar for v in row.values() if v < 0)
            return neg_sum
        return 0
    return {
        "scaled_pnl": scaled_pnl,
        "var_excess_penalty": -max(0, var_excess),
        "drawdown_excess_penalty": -max(0, dd_excess),
        "over_clearance_penalty": -0.1 if alpha < 0.1 else 0,
    }.get(term_name, 0)
```

Modify `Scripts/auto_optimize/offline_rl_trainer.py` CLI (line 92-94) — read manifest shaping + merge feedback:
```python
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from manifest_loader import load_manifest
    from trace_to_mdp_dataset import merge_shaping_overrides
    manifest_path = pathlib.Path("Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml")
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        base_shaping = [{"term": t.term, "weight": t.weight} for t in manifest.reward_config.shaping]
    else:
        base_shaping = orl.get("reward_terms", [{"term": "scaled_pnl", "weight": 1.0}])
    fb_path = pathlib.Path(args.trace + ".feedback.json")
    overrides = {}
    if fb_path.exists():
        import json as _json
        try:
            overrides = _json.loads(fb_path.read_text()).get("shaping_overrides", {})
        except Exception:
            pass
    reward_terms = merge_shaping_overrides(base_shaping, overrides)
    trans = trace_to_transitions(args.trace, reward_terms, orl.get("var_budget", 0.02), orl.get("max_dd", 0.2))
```

Modify `Scripts/auto_optimize/config.yaml` — delete `reward_terms` from `offline_rl` block (keep `algorithm/var_budget/max_dd/fqe_n_steps`):
```yaml
offline_rl:
  algorithm: cql
  var_budget: 0.50
  max_dd: 0.05
  fqe_n_steps: 500
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_reward_injection.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/trace_to_mdp_dataset.py Scripts/auto_optimize/gym_env.py Scripts/auto_optimize/offline_rl_trainer.py Scripts/auto_optimize/config.yaml Tests/test_reward_injection.py
git commit -m "feat(feedback): reward source unified to manifest + layer_contrib_penalty term"
```

---

## Task 8: observation 注入(_encode_state 读 observation_fields)

**Files:**
- Modify: `Scripts/auto_optimize/trace_to_mdp_dataset.py`
- Modify: `Scripts/auto_optimize/policy_exporter.py`
- Test: `Tests/test_observation_injection.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_observation_injection.py`:
```python
"""Tests for observation_fields injection. Spec §3.4."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from trace_to_mdp_dataset import _encode_state  # noqa: E402


def test_encode_state_default_8_dim():
    s = {"tpv": 1.0, "cash_pct": 0.5, "var_1d99": 0.7, "var_regime": 0.3,
         "drawdown": 0.1, "n_open_positions": 1, "pnl": 0.01}
    arr = _encode_state(s, 0)
    assert len(arr) == 8


def test_encode_state_custom_fields():
    s = {"tpv": 1000.0, "w_smooth": 0.5, "trend_dir": 1, "extreme_triggered": False}
    arr = _encode_state(s, 0, observation_fields=["tpv", "w_smooth", "trend_dir", "extreme_triggered"])
    assert len(arr) == 4
    assert arr[0] == 1000.0
    assert arr[2] == 1
    assert arr[3] == 0


def test_encode_state_missing_field_fills_zero():
    s = {"tpv": 1000.0}
    arr = _encode_state(s, 0, observation_fields=["tpv", "w_smooth", "drawdown"])
    assert len(arr) == 3
    assert arr[1] == 0
    assert arr[2] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_observation_injection.py -v
```
Expected: FAIL — `_encode_state` doesn't accept `observation_fields`.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/trace_to_mdp_dataset.py` `_encode_state` + `trace_to_transitions`:
```python
def _encode_state(s, t, observation_fields=None):
    """Spec §3.4: observation_fields overrides hardcoded 8-dim. Default = original behavior."""
    if observation_fields is None:
        return np.array([
            float(s.get("tpv", 0)), float(s.get("cash_pct", 0)),
            float(s.get("var_1d99", 0)), float(s.get("var_regime", 0)),
            float(s.get("drawdown", 0)), float(s.get("n_open_positions", 0)),
            float(s.get("pnl", 0)), float(t),
        ], dtype=np.float32)
    return np.array([float(s.get(f, 0)) for f in observation_fields], dtype=np.float32)


def trace_to_transitions(trace_path, reward_terms, var_budget, max_dd,
                         observation_fields=None, per_bar_layer_contrib=None):
    states, alphas = [], []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip(): continue
        s = json.loads(line)
        states.append(s)
        alphas.append(float(s.get("alpha", 1.0)))
    trans = []
    for i in range(len(states) - 1):
        s = _encode_state(states[i], i, observation_fields)
        s_next_state = _encode_state(states[i+1], i+1, observation_fields)
        a = alphas[i]
        r = _compute_reward(states[i+1], a, reward_terms, var_budget, max_dd, per_bar_layer_contrib)
        done = (i == len(states) - 2)
        trans.append(Transition(state=s, action=a, reward=r, next_state=s_next_state, done=done))
    return trans
```

Modify `Scripts/auto_optimize/policy_exporter.py` CLI (line 108) — `--obs-dim` default from manifest:
```python
    ap.add_argument("--obs-dim", type=int, default=None)
    # after args = ap.parse_args():
    obs_dim = args.obs_dim
    if obs_dim is None:
        import pathlib as _p
        mp = _p.Path("Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml")
        if mp.exists():
            import yaml as _yaml
            mdoc = _yaml.safe_load(mp.read_text())
            obs_dim = len(mdoc.get("feedback", {}).get("observation_fields", [])) or 8
        else:
            obs_dim = 8
    info = export_policy_onnx(model, obs_dim, args.output)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_observation_injection.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/trace_to_mdp_dataset.py Scripts/auto_optimize/policy_exporter.py Tests/test_observation_injection.py
git commit -m "feat(feedback): _encode_state reads observation_fields (gold2 13-dim, default 8 fallback)"
```

---

## Task 9: evolution_scheduler 第 5 触发器 review_drift

**Files:**
- Modify: `Scripts/auto_optimize/evolution_scheduler.py`
- Test: `Tests/test_review_drift_trigger.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_review_drift_trigger.py`:
```python
"""Tests for evolution_scheduler review_drift 5th trigger. Spec §4.1."""
import json
import sys
import tempfile
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import check_triggers, _write_feedback_action, _load_feedback_action  # noqa: E402


class _FakeManifest:
    strategy_name = "Gold2"
    raw = {"feedback": {"adapter_module": "adapters.gold2", "adapter_class": "Gold2FeedbackAdapter",
            "trigger_thresholds": {"review_status_fail": True, "max_layer_attribution_gap": 0.15,
                                   "min_narrative_trades": 20}}}


def test_review_drift_false_when_no_sidecar():
    with tempfile.TemporaryDirectory() as d:
        config = {"ppo_training": {"train_window_days": 504}}
        state = {"last_optimize_date": "2026-07-01T00:00:00"}
        triggers = check_triggers(config, state, "nonexistent_metrics.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("review_drift") is False


def test_review_drift_true_when_review_status_fail():
    with tempfile.TemporaryDirectory() as d:
        rdir = Path(d) / "Gold2" / "review"
        rdir.mkdir(parents=True)
        (rdir / "review.json").write_text(json.dumps({"layer_attribution": {}, "per_trade_narrative": list(range(50)), "run_meta": {}}))
        (rdir / "review.last_review.json").write_text(json.dumps({"review_status": "fail", "last_review": "x"}))
        config = {"ppo_training": {"train_window_days": 504}}
        state = {"last_optimize_date": "2026-07-10T00:00:00"}
        triggers = check_triggers(config, state, "nonexistent.json", results_dir=d, manifest=_FakeManifest)
        assert triggers.get("review_drift") is True


def test_write_and_load_feedback_action():
    with tempfile.TemporaryDirectory() as d:
        from adapters.base import FeedbackAction
        action = FeedbackAction(trigger=True, trigger_reason="x",
                                shaping_overrides={"extreme_risk_contrib_penalty": 2.0},
                                observation_fields=["tpv"], attribution_method="telescoping",
                                per_bar_layer_contrib=[])
        path = Path(d) / "state.feedback.json"
        _write_feedback_action(action, str(path))
        loaded = _load_feedback_action(str(path))
        assert loaded.trigger is True
        assert loaded.shaping_overrides["extreme_risk_contrib_penalty"] == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_drift_trigger.py -v
```
Expected: FAIL — `check_triggers` doesn't accept `results_dir`/`manifest`; `_write_feedback_action` not found.

- [ ] **Step 3: Write minimal implementation**

Modify `Scripts/auto_optimize/evolution_scheduler.py`:

(a) Add helpers (after `_read_live_metrics`):
```python
def _write_feedback_action(action, path):
    """Spec §4.1: write FeedbackAction to <state_path>.feedback.json."""
    import json as _json
    from dataclasses import asdict
    try:
        pathlib.Path(path).write_text(_json.dumps(asdict(action), default=str))
    except Exception as ex:
        print(f"  [review_drift] write feedback action failed: {ex}")


def _load_feedback_action(path):
    """Spec §4.2: load FeedbackAction for fire_optimization."""
    import json as _json
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        d = _json.loads(p.read_text())
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
        from adapters.base import FeedbackAction
        return FeedbackAction(**d)
    except Exception:
        return None
```

(b) Extend `check_triggers` signature + add 5th trigger (after the existing 4, before `return triggers`):
```python
def check_triggers(config: dict, state: dict, metrics_path: str, results_dir: str = None, manifest=None) -> dict:
    """检查 5 个触发器 (review_drift 新增, spec §4.1)."""
    triggers = {}
    ppo_cfg = config.get("ppo_training", {})
    # ... existing 4 triggers (data_accumulation, sharpe_decay, weekly, kill_switch) unchanged ...
    # 5. review_drift (复盘反馈触发器, spec §4.1)
    triggers["review_drift"] = False
    if results_dir and manifest:
        try:
            import sys as _sys
            _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
            from signals import orchestrate as orchestrate_feedback
            action = orchestrate_feedback(manifest, results_dir)
            if action is not None:
                triggers["review_drift"] = action.trigger
                if action.trigger:
                    state_path = state.get("_state_path", str(_REPO_ROOT / "Results" / "auto_optimize" / "evolution_state.json"))
                    _write_feedback_action(action, state_path + ".feedback.json")
        except Exception as ex:
            print(f"  [review_drift] orchestrate failed: {ex}")
    return triggers
```

(c) Update `_check_and_fire` in `main()` to pass `results_dir` + `manifest`:
```python
    def _check_and_fire():
        state = json.loads(pathlib.Path(args.state_path).read_text()) if pathlib.Path(args.state_path).exists() else {}
        state["_state_path"] = args.state_path
        from manifest_loader import load_manifest
        manifest = load_manifest(args.manifest)
        triggers = check_triggers(config, state, args.metrics_path,
                                  results_dir=str(_REPO_ROOT / "Results"), manifest=manifest)
        # ... rest unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_review_drift_trigger.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/evolution_scheduler.py Tests/test_review_drift_trigger.py
git commit -m "feat(feedback): evolution_scheduler 5th trigger review_drift + feedback action write/load"
```

---

## Task 10: deploy_gate checklist 打印(§5.3.1)

**Files:**
- Modify: `Scripts/auto_optimize/evolution_scheduler.py`
- Test: `Tests/test_deploy_checklist.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_deploy_checklist.py`:
```python
"""Tests for deploy_gate checklist printing. Spec §5.3.1."""
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
from evolution_scheduler import _print_deploy_checklist  # noqa: E402
from adapters.base import FeedbackAction  # noqa: E402


def test_print_deploy_checklist_outputs_3_items():
    action = FeedbackAction(trigger=True, trigger_reason="layer_gap extreme_risk=-0.32>0.15",
                            shaping_overrides={"extreme_risk_contrib_penalty": 2.13},
                            observation_fields=["tpv", "w_smooth", "trend_dir"],
                            attribution_method="telescoping", per_bar_layer_contrib=[])
    out = _print_deploy_checklist(action, onnx_path="/tmp/policy.onnx",
                                  manifest_path="manifest.yaml",
                                  prev_gen_shaping={"extreme_risk_contrib_penalty": 1.0})
    assert "[deploy_gate checklist]" in out
    assert "extreme_risk_contrib_penalty" in out
    assert "2.13" in out
    assert "obs_dim" in out


def test_print_deploy_checklist_flags_weight_jump():
    action = FeedbackAction(trigger=True, trigger_reason="",
                            shaping_overrides={"extreme_risk_contrib_penalty": 3.0},
                            observation_fields=["tpv"], attribution_method="residual", per_bar_layer_contrib=[])
    out = _print_deploy_checklist(action, onnx_path="x", manifest_path="m",
                                  prev_gen_shaping={"extreme_risk_contrib_penalty": 1.0})
    assert "jump" in out.lower() or "跳变" in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_deploy_checklist.py -v
```
Expected: FAIL — `_print_deploy_checklist` not found.

- [ ] **Step 3: Write minimal implementation**

Add to `Scripts/auto_optimize/evolution_scheduler.py`:
```python
def _print_deploy_checklist(action, onnx_path, manifest_path, prev_gen_shaping=None):
    """Spec §5.3.1: print deploy_gate manual checklist (pure print, non-blocking).
    3 items: (1) out-of-attribution-window comparison, (2) shaping weight delta,
    (3) ONNX obs_dim check."""
    prev = prev_gen_shaping or {}
    lines = ["[deploy_gate checklist] (spec §5.3.1 — manual review before deploy)"]
    lines.append("1. 归因窗口外近期数据对比 (out-of-attribution-window):")
    lines.append("   手动跑 challenger vs champion FQE + 1-day 回测 on 近期数据 (非 state_trace 期):")
    lines.append(f"     python3 ope_evaluator.py --policy {onnx_path} --observations <recent>...")
    lines.append(f"     dotnet run --project Launcher --config config-<strategy>-challenger-recent.json")
    lines.append("   确认 challenger 在归因窗口外不劣化 (防 reward 过拟合单次路径).")
    lines.append("2. 代际 shaping 权重核对 (generation_<N>_shaping.json):")
    for term, w in action.shaping_overrides.items():
        prev_w = prev.get(term)
        if prev_w is not None and abs(w - prev_w) > 2 * abs(prev_w):
            lines.append(f"   ⚠ {term}: {prev_w}→{w} jump >2x — 确认 review gap 真实 vs 噪声")
        else:
            lines.append(f"   {term}: {w} (prev: {prev_w})")
    lines.append("3. ONNX obs_dim 校验:")
    lines.append(f"   challenger ONNX obs_dim == manifest feedback.observation_fields 长度 ({len(action.observation_fields)})")
    lines.append(f"   manifest: {manifest_path}")
    out = "\n".join(lines)
    print(out)
    return out
```

Call it in `fire_optimization` after ONNX export (before `return True`):
```python
    # Spec §5.3.1: deploy_gate checklist (manual review before deploy)
    try:
        import sys as _sys
        _sys.path.insert(0, str(_REPO_ROOT / "Scripts" / "feedback"))
        fb_action = _load_feedback_action(state_path + ".feedback.json")
        if fb_action:
            prev_gen_path = pathlib.Path(state_path + ".feedback.json.prev")
            prev_shaping = {}
            if prev_gen_path.exists():
                import json as _json
                prev_shaping = _json.loads(prev_gen_path.read_text()).get("shaping_overrides", {})
            _print_deploy_checklist(fb_action, onnx_path=policy_pt.replace(".pt", ".onnx"),
                                    manifest_path=manifest_path, prev_gen_shaping=prev_shaping)
    except Exception as ex:
        print(f"  [deploy_checklist] non-blocking failure: {ex}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_deploy_checklist.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/evolution_scheduler.py Tests/test_deploy_checklist.py
git commit -m "feat(feedback): deploy_gate manual checklist (§5.3.1)"
```

---

## Task 11: 端到端集成验证

**Files:**
- Test: `Tests/test_feedback_e2e.py`

- [ ] **Step 1: Write the failing test**

`Tests/test_feedback_e2e.py`:
```python
"""End-to-end: review.json + sidecar + state_trace → FeedbackAction → reward/observation. Spec §5.2."""
import json
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "feedback"))
sys.path.insert(0, str(_REPO / "Scripts" / "auto_optimize"))
from signals import orchestrate  # noqa: E402
from manifest_loader import load_manifest  # noqa: E402
from trace_to_mdp_dataset import merge_shaping_overrides, _encode_state, _compute_reward  # noqa: E402

MANIFEST = _REPO / "Scripts" / "auto_optimize" / "strategies" / "gold2_beta_vol_target" / "manifest.yaml"
RESULTS = _REPO / "Results" / "gold2-betavol"


def test_e2e_orchestrate_on_real_gold2_review():
    if not (RESULTS / "review" / "review.json").exists():
        import pytest
        pytest.skip("gold2 review artifacts absent")
    m = load_manifest(MANIFEST)
    action = orchestrate(m, str(RESULTS.parent))
    assert action is not None
    assert action.attribution_method in ("telescoping", "residual")
    assert isinstance(action.shaping_overrides, dict)
    assert isinstance(action.observation_fields, list)
    assert "tpv" in action.observation_fields
    assert "drawdown" in action.observation_fields


def test_e2e_reward_injection_with_feedback_overrides():
    m = load_manifest(MANIFEST)
    base = [{"term": t.term, "weight": t.weight} for t in m.reward_config.shaping]
    overrides = {"extreme_risk_contrib_penalty": 2.13}
    merged = merge_shaping_overrides(base, overrides)
    terms = {t["term"] for t in merged}
    assert "extreme_risk_contrib_penalty" in terms
    s_next = {"pnl": 0.01, "_per_bar": [{"extreme_risk": -0.05}]}
    r = _compute_reward(s_next, 1.0, merged, 0.5, 0.05)
    assert r != 0


def test_e2e_observation_dim_matches_manifest():
    m = load_manifest(MANIFEST)
    obs_fields = m.raw["feedback"]["observation_fields"]
    s = {"tpv": 1000.0, "w_smooth": 0.5, "trend_dir": 1, "drawdown": 0.1, "pnl": 0.01}
    arr = _encode_state(s, 0, observation_fields=obs_fields)
    assert len(arr) == len(obs_fields)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_e2e.py -v
```
Expected: FAIL — wiring not complete until Tasks 1-10 done.

- [ ] **Step 3: Verify wiring (no new code)**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_*.py Tests/test_reward_injection.py Tests/test_observation_injection.py Tests/test_review_drift_trigger.py Tests/test_deploy_checklist.py -v 2>&1 | tail -20
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_e2e.py -v
```
Expected: PASS (3 tests; first skips if artifacts absent).

- [ ] **Step 5: Final full suite + C# build**

```bash
cd /home/project/hope/Lean && python3 -m pytest Tests/test_feedback_*.py Tests/test_reward_injection.py Tests/test_observation_injection.py Tests/test_review_drift_trigger.py Tests/test_deploy_checklist.py -v 2>&1 | tail -10
cd /home/project/hope/Lean && export PATH="/usr/local/dotnet:$PATH" && dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj 2>&1 | tail -3
cd /home/project/hope/Lean && export PATH="/usr/local/dotnet:$PATH" && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2FeedbackStateExportTests" 2>&1 | tail -3
```
Expected: all feedback tests PASS; C# build 0 errors; gold2 state-export tests PASS.

- [ ] **Step 6: Commit**

```bash
git add Tests/test_feedback_e2e.py
git commit -m "feat(feedback): e2e integration test — orchestrate + reward + observation wired"
```

---

## Self-Review(已执行)

**1. Spec coverage:**
- §1 架构/数据流 → Task 1+6+9+7+8
- §2 manifest feedback 段 → Task 2
- §3.1 ABC + FeedbackAction → Task 1
- §3.2 触发器 + min_trades gate(eval-update2 #3)→ Task 4
- §3.3 per-bar 下采样 + shaping_overrides + 代际溯源 → Task 4+5+7
- §3.4 observation 注入 → Task 8
- §3.5 gold2 drawdown/pnl/close + _peakTpv/_prevTpv/_lastClose → Task 3
- §4.1 review_drift 触发器 → Task 9
- §4.2 reward 源统一 + shaping 合并 → Task 7
- §4.3 observation 注入 → Task 8
- §5.3.1 deploy_gate checklist(eval-update2 B 条件)→ Task 10
- §1.4 state_trace 路径约定 → Task 6

**2. Placeholder scan:** 无 TBD/TODO。Task 5 `_downsample_per_bar` 在 Task 4 是 stub,Task 5 填充(分 task,非 placeholder)。

**3. Type consistency:**
- `FeedbackAction` 字段跨 task 一致
- `Gold2FeedbackAdapter.LAYERS` 跨 Task 4/5 一致
- `_encode_state(s, t, observation_fields=None)` 跨 Task 8 一致
- `_compute_reward(..., per_bar_layer_contrib=None)` 跨 Task 7 一致
- `merge_shaping_overrides(base, overrides)` 跨 Task 7 一致
- `orchestrate(manifest, results_dir)` 跨 Task 6/9 一致

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-12-review-feedback-loop-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
