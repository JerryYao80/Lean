# 基于复盘的量化策略优化 vs 现有参数优化

> 本文档澄清"基于复盘反馈的策略优化"(本 spec)与"现有已实现的策略参数优化"(Layer A Bayesian)的本质区别,作为 `docs/superpowers/specs/2026-07-12-review-feedback-loop-design.md` 的设计边界说明。

---

## 1. 核心区别(一句话)

> **Bayesian 优化策略参数(标量空间,全局,DSR 目标);复盘优化训练信号(reward + observation,state→action 级,层归因目标)。两者正交:Bayesian 选参数,复盘让 RL 学行为。**

---

## 2. 对照表

### 2.1 现有参数优化(Layer A Bayesian,已实现)

| 维度 | 现有 |
|---|---|
| **搜什么** | manifest `parameter_space` 的 10 个标量(trend-ma-short/long, ewma-lambda, vol-target, vol-warmup, smooth-alpha, rebalance-threshold, extreme-vol-cap, realrate-cap, trend-floor, trend-disable) |
| **怎么搜** | Optuna 贝叶斯(TPE),`bayesian_optimizer.py --n-trials 200`,目标 DSR |
| **目标函数** | 单一标量 DSR(或 configured metric) |
| **粒度** | 策略级全局参数(一套参数跑全回测) |
| **反馈信号** | 回测终值 DSR — **不看归因/不看回撤成因/不看层失败 regime** |
| **盲区** | DSR=0.9 可能是 trend 层 +120% / extreme_risk 层 -30% 净出来的;Bayesian 不知道是哪层拖后腿,只能盲调 10 个参数组合 |

### 2.2 基于复盘的优化(本 spec)

| 维度 | 复盘反馈优化 |
|---|---|
| **调什么** | (a) reward shaping 权重(层贡献 → penalty term)— 不是策略参数,是**训练信号**;(b) CQL observation 维度(让 RL agent **看到**层状态);(c) 触发器(何时重训) |
| **怎么搜** | 不搜参数空间。把归因洞察**直接注入** RL reward + observation,CQL 自己学出应对层失败的最优行为 |
| **目标函数** | reward = scaled_pnl + drawdown_penalty + **layer_contrib_penalty**(新);FQE 评估含层惩罚的 value |
| **粒度** | **per-bar** 层贡献(state_trace 解锁后)— 不是策略级,是 **state→action 级** |
| **反馈信号** | review.json 的 layer_attribution(per-trade)+ state_trace 的 per-bar 层状态(w_after_vol/extreme_triggered) |
| **解锁能力** | RL agent 学到"extreme_triggered=True 且 w_after_vol 高时降仓"这种**条件行为** — Bayesian 全局参数学不到 |

---

## 3. 举例(gold2)

| 场景 | Bayesian(Layer A) | 复盘反馈(Layer C+) |
|---|---|---|
| DSR=0.9 但极端回撤 -28% | 调 `extreme-vol-cap` 0.2→0.5 盲试 | review 显示 extreme_risk 层 -30% → reward 加 `extreme_risk_contrib_penalty=2.0` → CQL 学到极端 regime 降仓 |
| trend 层在 RISING_FAST 失效 | 调 `trend-ma-short` 20→10 盲试 | review 显示 trend 层 PnL 在 RISING_FAST regime 集中亏损 → observation 加 `trend_dir/realrate_cap` → CQL **看到** regime 学条件行为 |
| warmup 不足导致 EWMA 不稳 | 调 `vol-warmup` 60→120 盲试 | review 显示 vol_target 层前 60 bar 贡献噪声 → observation 加 `w_smooth` → CQL 学到 warmup 期保守 |

---

## 4. 关系:不是替代,是串联

```
Bayesian (Layer A, 已有) → 最优标量参数 (DSR-max)
  → CQL (Layer C, 已有, 但 8 维 obs + 4 term reward)
    → 复盘反馈 (本 spec) → CQL 升级 (13 维 obs + 含层惩罚 reward)
      → FQE 评估 → ONNX → deploy_gate
```

Bayesian 选参数,复盘让**基于这些参数训出的 RL agent** 额外学到层条件行为。两轨目标函数不同(Bayesian=DSR,复盘=层惩罚加权 reward),互不冲突。

---

## 5. 对设计边界的影响

**不碰** `bayesian_optimizer.py` / `parameter_space` / Layer A 流程(那是参数优化,本 spec 是训练信号优化)。

**只碰** Layer C 上下游:
- `offline_rl_trainer.py` reward 源(改为读 manifest `reward_config.shaping`)
- `trace_to_mdp_dataset.py` 的 `_encode_state`(observation 扩 13 维)+ `_compute_reward`(新 term `layer_contrib_penalty`)
- `evolution_scheduler.py` 触发器(第 5 触发器 `review_drift`)

Layer A 的 Bayesian 参数优化保持原样,与本 spec 正交。
