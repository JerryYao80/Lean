# auto-update5.md 执行完成总结

## 诊断结论：用户的判断完全正确，已确认并修复

### 静态扫描（dsrdiag.py）证据

`docs/dsrdiag.py` 对 `bayesian_optimizer.py` 的静态扫描结果：
- **🚩 高风险：在 DSR/PSR 相关代码块附近发现运行时 trial 计数引用**
- 命中行：
  - `L26: reward = compute_reward(stats, n_trials=trial.number + 1, baseline_sharpe=baseline_sharpe)`
  - `L29: baseline_sharpe = (baseline_sharpe * trial.number + reward["sharpe"]) / (trial.number + 1)`

`compute_reward` 的 DSR 公式中 `expected_max = baseline_sr + se * norm.ppf(1 - 1/n_trials)`，而 `n_trials` 取自 `trial.number + 1`（运行时可变量）。**这直接把随 N 变化的 DSR 嵌入到每 trial 的实时 reward，违反贝叶斯优化代理模型"目标函数是参数的稳定函数"的基本假设**——正如 auto-update5.md 所述。

### 修复方案

| 函数 | 角色 | 平稳性 |
|---|---|---|
| `compute_stationary_reward` | 每 trial 的优化目标 = 原始 Sharpe | ✅ 平稳，仅依赖 θ |
| `compute_dsr_gate` | 搜索结束后对 champion trial 一次性事后多重检验校正 | ✅ N 固定（总 trial 数），事后门禁 |
| `compute_reward` | 保留向后兼容，标注弃用 | ⚠️ 非平稳（已停用作为 objective） |

`bayesian_optimizer.py` objective 改为：
- `return reward["objective"]`（平稳 Sharpe）给 sampler
- `trial.set_user_attr("raw_sharpe", ...)` 存原始 Sharpe 供事后分析
- 搜索结束后用 `compute_dsr_gate(champion_stats, n_trials_total=N, ...)` 做一次性门禁

### 附带修复

1. **lean_runner.py**：
   - `_resolve_dotnet()` 优先 `/usr/local/dotnet/dotnet`（之前 `dotnet` 不在 PATH 导致所有 trial `Sharpe=0`）
   - 优先读 `{Strategy}-summary.json`（有 lowercase `statistics` key），回退 `.json`
   - `parse_results` 支持 `Statistics`/`statistics` 双 key
2. **reward.py**：新增 `_to_float()` 处理 LEAN 的百分比/逗号字符串（如 `"0.209"`、`"2.900%"`、`"180"`）；Drawdown 从百分比转小数

### 30 trials 验证结果

修复后重跑 30 trials（带 sqlite 持久化）：

```
trial | value (Sharpe)
  0   | 0.209   ← best
  1   | 0.209
  ...
 29   | 0.209
```

- **30/30 trial 的 value 均为 0.209**（真实 Sharpe，不再是 0）
- best_value = 0.209，best_trial_number = 0
- DSR gate 事后校正（N=30）：`dsr=0.0647, passed=True`

### 经验数据分析（dsrdiag on sqlite）

- `value_mean = 0.209`（恒定，spearman_r=nan 因输入常数）
- 静态扫描仍报 trial.number，但仅在 `all_stats.append` 记录和 champion 查找（非 DSR 计算路径）——**非平稳性已消除**（`compute_stationary_reward` 内部无 trial.number 依赖）

### 新发现的问题（待后续处理）

**所有 30 trial 的 Sharpe 恒为 0.209，参数变化不影响结果**。进一步排查发现：

1. **Lean Launcher 不支持 `--parameters` CLI**（`LeanArgumentParser` 的 `parameters` 是 MultipleValue 但用 kvp 格式 `key=value`，不支持 JSON）。lean_runner.py 当前传 `--parameters '{json}'` 实际被忽略。
2. **正确方式**：参数必须写入 config.json 的 `"parameters"` 字段。
3. 即使用 config 字段传参（极端 `var-budget=0.04/max-drawdown=0.30/max-position-weight=0.45`），结果仍 180 orders / 0.209 Sharpe——**说明这些参数对当前回测窗口的 OptionVolArb5Layer 影响极小**（L2 Alpha 的 iv-rv-z/ivts/skew 阈值在 4 个月窗口内几乎不触发；L4 Risk 的 var-budget/max-drawdown 在低波动期不收紧）。

### 判断

- ✅ auto-update5.md 的核心问题（DSR 非平稳性）已确认并修复
- ✅ 静态扫描证据明确，修复方案符合 Bailey & López de Prado 原始用法
- ⚠️ 修复后发现新问题：参数传播机制（lean_runner 需改用 config 字段）+ 搜索空间在当前窗口区分度不足

## 提交

- `c295f8c45` fix(auto-optimize): DSR non-stationarity + LEAN stats parsing（reward.py / bayesian_optimizer.py / lean_runner.py，+193/-31）

## 后续建议

1. **lean_runner.py 改用 config 字段传参**：为每 trial 生成临时 config（注入 `"parameters": {...}`），而非 `--parameters` CLI
2. **扩大回测窗口或换策略**：当前 4 个月窗口 + OptionVolArb5Layer 参数区分度不足，无法验证优化器探索能力。建议用更长的回测窗口或换 VarStrategy（参数对 VaR 风控更敏感）
3. 修复后重跑 30 trials，确认 best Sharpe 是否随 trial 数增加而真正改善（证伪"卡在 trial 0"的残留猜测）
