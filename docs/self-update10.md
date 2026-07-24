# 量化策略自动进化功能 — 实现原理、实现方式、验证方法

**日期:** 2026-07-07
**范围:** 基于本仓库已实现的 Layer A Bayesian + Layer C PPO 自动优化框架（`Scripts/auto_optimize/` + `Algorithm.CSharp/Common/` + `Algorithm.CSharp/Models/Risk/`），阐述"策略自动进化"的完整闭环：原理 → 实现 → 验证。

---

## 一、实现原理

### 1.1 什么是"自动进化"

"自动进化"指：**策略在不人工调参的前提下，通过数据驱动的优化器自动搜索更优参数组合，并通过离线训练的 RL 风控 policy 在线调整仓位，形成"搜索→验证→部署→监控→再搜索"的持续闭环**。

与"自动调参"的区别：
- 自动调参：一次性扫参数，选最优，结束
- 自动进化：周期性重跑搜索（新数据进来后），policy 持续 freeze 推理 + 周期重训，带过拟合防控与上线门禁

### 1.2 三层架构映射

本仓库已落地的"三层管线 + 五层架构"是进化的结构基础：

```
Factor Zoo (Common/Factors/)  → 原子信号（IV/HV/Skew/VaR/Momentum...）
   ↓ 消费
Model Zoo (Models/)           → 组合信号成决策
   - Alpha: OptionVolArbFactorZooAlphaModel / VarAlphaModel
   - Portfolio: EqualWeightPortfolioModel
   - Risk: CompositeRiskModel + RlRiskModel (Layer C 注入点)
   - Universe: OptionVolArbUniverseSelectionModel
   - Execution: ImmediateExecutionModel
   ↓ 消费
Strategy (Algorithm.CSharp/)  → 固定 universe + 参数，端到端回测
```

LEAN 五层架构（`Universe → Alpha → Portfolio → Risk → Execution`）中，**Layer A（Bayesian 超参扫描）不侵入任何层**（只通过 config `parameters` 字段注入），**Layer C（PPO 在线风控）只侵入 L4 Risk**（`RlRiskModel : IRiskManagementModel`）。其余 4 层保持 LEAN 原生。

### 1.3 双层进化闭环

| 层 | 角色 | 频率 | 平稳性 |
|---|---|---|---|
| **Layer A**（离线 Bayesian） | 扫策略超参向量 θ（iv-rv-z 阈值 / var-budget / max-drawdown 等），reward = 原始 Sharpe（平稳），事后 DSR 门禁 | 周期性（如每月/每季度） | ✅ 目标函数仅依赖 θ |
| **Layer C**（在线 PPO 风控） | 离线训练 PPO policy → ONNX freeze → Python inference server → ZeroMQ → `RlRiskModel` 每 rebalance 点缩放仓位 α | 盘中持续推理，周/月重训 | ✅ policy freeze，盘中无梯度更新 |

**进化触发器**：新数据积累 → 重跑 Layer A 搜索 → 产出新 best_params → 人工审核 DSR 门禁 → 部署新 θ + 重训 policy → 灰度上线 → 监控 → 再触发。

### 1.4 三个立足（最高约束）

1. **满足 A 股市场实际要求** — 真实 A 股标的、Asia/Shanghai 时区、100 股整手
2. **使用 tushare 真实交易数据** — 直接读 `Data/equity/{sse,szse}/daily/`，不伪造
3. **使用 LEAN 原生优秀稳定算法** — 复用 `IFactor`/`IRiskManagementModel`/Portfolio/Statistics，Bayesian/PPO 用 Python 成熟库（Optuna/stable-baselines3），不自写

### 1.5 过拟合防控（进化的安全阀）

| 机制 | 实现 | 文件 |
|---|---|---|
| **平稳 reward** | 每 trial 返回原始 Sharpe（非随 N 变化的 DSR） | `reward.py::compute_stationary_reward` |
| **事后 DSR 门禁** | 搜索结束后用总 trial 数 N 对 champion 一次性校正，DSR>0 才允许上线 | `reward.py::compute_dsr_gate` |
| **CPCV** | Combinatorial Purged K-Fold（López de Prado），训练/测试无泄漏 | `cpcv.py` |
| **Walk-forward OOS** | 滚动训练/测试窗口，reward = OOS DSR | `walk_forward.py` |
| **参数脊岭监控** | 检测参数是否收敛到一点（过收敛 → 过拟合预警） | `ridge_monitor.py` |
| **双阈值过拟合判定** | OOS DSR 绝对值 ≥ 0.5 且 OOS/IS 比 ≥ 0.6，缺一不可 | `overfitting_report.py` |
| **基线对比（第四道门）** | White's Reality Check / bootstrap p-value，对比"优化前自己"和"买入持有" | `baseline_test.py` |

### 1.6 关键修复历史（auto-update1~7）

| 阶段 | 问题 | 修复 |
|---|---|---|
| auto-update2 | 简化项 var_1d99/var_regime 置 0 | VaRFactor 接入 SerializeRlState + RL_TRACE_PATH 写 trace |
| auto-update3 | Layer C 未在准实盘验证 | kill-switch 结构性 + ONNX 确定性 + 延迟预算 |
| auto-update4 | 200 trials DSR 衰减 | 双阈值判定 + 衰减分析 |
| auto-update5 | **DSR 非平稳性**（trial.number 进 reward） | compute_stationary_reward + compute_dsr_gate 事后门禁 |
| auto-update6 | 参数传播（--parameters CLI 不支持 JSON） | config 字段注入 + 极端对比验证 |
| auto-update7 | 8 年回测超时 + 策略不敏感 | VarStrategy 高波动窗口 + manifest 下限修正 |

---

## 二、如何实现

### 2.1 文件结构（已实现）

```
Algorithm.CSharp/
├── Common/
│   ├── IOptimizableStrategy.cs        # 标记接口: GetTunableParameterNames()
│   └── IRlStateExportable.cs          # 标记接口: SerializeRlState(algo)
├── Models/Risk/
│   ├── RlRiskConfig.cs                # Layer C 配置 (endpoint/fallback/timeout)
│   └── RlRiskModel.cs                 # IRiskManagementModel, ZeroMQ→α缩放, kill-switch
└── (各策略)                            # 实现 2 接口 + 参数化 pass

Scripts/auto_optimize/
├── manifest_loader.py                 # 解析 manifest.yaml → StrategyManifest
├── manifest_lint.py                   # CI 校验 manifest↔代码一致 (门0)
├── lean_runner.py                     # Lean 子进程驱动, config 字段注入参数
├── reward.py                          # compute_stationary_reward + compute_dsr_gate
├── cpcv.py                            # CPCV (López de Prado)
├── walk_forward.py                    # Walk-forward OOS 窗口
├── ridge_monitor.py                   # 参数脊岭监控
├── bayesian_optimizer.py              # Layer A 主驱动 (Optuna TPE, sqlite 持久化)
├── lean_step_runner.py                # Layer C 状态序列导出
├── gym_env.py                         # gym Environment 回放 trace
├── ppo_trainer.py                     # Layer C PPO 训练 (stable-baselines3)
├── policy_exporter.py                 # PyTorch → ONNX + 一致性验证
├── inference_server.py                # ZeroMQ REP + ONNX forward (freeze)
├── baseline_test.py                   # 第四道门: bootstrap p-value
├── overfitting_report.py              # 双阈值过拟合判定
├── onnx_replay_verify.py              # ONNX 推理确定性验证
├── latency_budget.py                  # 延迟预算测量
├── trials_decay_analyzer.py           # DSR 衰减分析
├── run_e2e.py                         # 端到端编排
├── config.yaml                        # 全局默认
└── strategies/{name}/manifest.yaml    # 每策略清单
```

### 2.2 Layer A 实现要点（离线超参进化）

```python
# bayesian_optimizer.py 核心
def objective(trial):
    # 1. 从 manifest.parameter_space 采样参数 θ
    params = {p.name: trial.suggest_xxx(p.name, p.range) for p in manifest.parameter_space}
    # 2. 通过临时 config 注入 parameters 字段跑 Lean 回测
    stats = run_backtest(manifest, params, manifest.lean_config, timeout)
    # 3. 平稳 reward = 原始 Sharpe (仅依赖 θ, 不依赖 trial 计数)
    reward = compute_stationary_reward(stats)
    trial.set_user_attr("raw_sharpe", reward["sharpe"])  # 供事后分析
    return reward["objective"]  # 给 Optuna sampler

study = optuna.create_study(direction="maximize", sampler=TPESampler(), storage=sqlite)
study.optimize(objective, n_trials=N)

# 4. 事后 DSR 门禁: 用总 trial 数 N 对 champion 一次性校正
baseline = mean(all_trial_sharpes)
dsr_gate = compute_dsr_gate(champion_stats, n_trials_total=N, baseline_sharpe=baseline)
# dsr_gate.passed == True 才允许上线
```

**关键点**：
- 参数注入走 config `parameters` 字段（LEAN 原生 `Config.Get("parameters")` → `GetParameter`），不走 `--parameters` CLI（不支持 JSON）
- reward 必须平稳（auto-update5 修复：DSR 含 trial.number 会违反贝叶斯优化假设）
- DSR 只做事后门禁，不进实时 reward

### 2.3 Layer C 实现要点（在线风控进化）

**离线训练阶段**：
```python
# 1. 跑回测导出真实 state_trace.jsonl (RL_TRACE_PATH env)
lean_step_runner.export_state_trace(manifest, config, trace_path)

# 2. gym_env 回放 trace, shaping reward 由 manifest 驱动
env = RlRiskEnv(trace_path, manifest.reward_config.shaping, var_budget, max_dd)

# 3. PPO 训练
model = PPO("MlpPolicy", env).learn(total_timesteps=10000)
model.save("policy.pt")

# 4. 导出 ONNX + 一致性验证
export_and_verify(policy_net, obs_dim, "policy.onnx")  # atol=1e-5
```

**在线推理阶段**：
```python
# inference_server.py: 常驻进程, ONNX freeze, 盘中只 forward
class InferenceServer:
    def serve_forever(self):
        while True:
            state_json = self.socket.recv_string()       # C# ZeroMQ REQ
            action_vec = self.session.run(None, {input: encode(state_json)})  # ONNX forward
            self.socket.send_string(json.dumps(decode(action_vec)))  # α∈[0,1]
```

**C# 端 RlRiskModel**：
```csharp
public IEnumerable<IPortfolioTarget> ManageRisk(algo, targets) {
    if (_killSwitchTripped) return ApplyFallback(targets);  // 结构性 fail-safe
    var stateJson = (algo as IRlStateExportable)?.SerializeRlState(algo);
    var action = _client.Request(stateJson, timeoutMs);     // ZeroMQ REQ
    if (action == null) { _consecutiveTimeouts++; ... }     // 连续 5 次 → trip kill-switch
    var alpha = ClampAlpha(action.Alpha);                   // [0,1]
    return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
}
```

**关键点**：
- policy freeze 部署（盘中无梯度更新，避开 live-paper 探索风险，self-update.md 方案）
- kill-switch 结构性：连续 5 次 IPC 失败 → 永久降级到 fallbackAlpha 直到进程重启
- ONNX 推理确定性：max_abs_diff=1.79e-07（远低于 atol=1e-4）
- 延迟预算：ZeroMQ round-trip p99=0.072ms（远低于 50ms 预算）

### 2.4 策略接入契约（通用化核心）

新策略接入只需 2 步，不改框架代码：

**Step 1: C# 实现 2 接口**
```csharp
public class MyStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable {
    public IEnumerable<string> GetTunableParameterNames() => new[] {"param1", "param2"};
    public string SerializeRlState(QCAlgorithm algo) {
        return JsonConvert.SerializeObject(new {
            ts = algo.Time.ToString("o"), strategy = "MyStrategy",
            tpv = Portfolio.TotalPortfolioValue, ...
        });
    }
}
```

**Step 2: 新增 manifest.yaml**
```yaml
strategy_name: MyStrategy
lean_config: Launcher/config/config-my.json
parameter_space:
  - {name: param1, type: float, range: [0.1, 0.9], default: 0.5, layer: L2_Alpha}
state_schema:
  fields: [{name: tpv, type: float}, ...]
```

**已接入 4 策略**：option_vol_arb_5layer / var_strategy / barra_cne5_v4 / overnight_anomaly

### 2.5 进化调度（周期闭环）

```yaml
# config.yaml
ppo_training:
  retrain_frequency: weekly       # 每周收盘后增量训练
  deploy_gate: manual             # 人工审核 DSR 后发布 (CI/CD 灰度)
  rollout_policy: greedy          # 部署时无探索噪声
optuna:
  n_trials: 200                   # 每次进化搜索 200 trials
```

闭环：
1. 新数据积累 → 触发 Layer A 搜索（200 trials）
2. best_params + DSR 门禁通过 → 人工审核
3. 部署新 θ + 重训 PPO policy（新 trace）
4. ONNX 导出 → inference server 替换
5. live-paper 灰度 → 监控 kill-switch/延迟/收益
6. 稳定后全量上线 → 等待下一周期

---

## 三、如何验证

### 3.1 五道验证门

| 门 | 内容 | 命令 | 通过标准 |
|---|---|---|---|
| **门 0** manifest 一致性 | manifest ↔ 代码 key 一致 | `python manifest_lint.py --strategy {name}` | 全部策略 OK |
| **门 1** 编译 | LEAN 解决方案编译 | `dotnet build QuantConnect.Lean.sln` | 0 error |
| **门 2** 单测+集成 | Python + C# 单测 | `pytest Tests/Python/` + `dotnet test --filter RlRiskModel` | 全绿 |
| **门 3** 回测回归 | 默认参数 byte-for-byte 不变 | 对比 OrderListHash | 改动前后一致 |
| **门 4** 基线对比 | White's Reality Check p-value | `baseline_test.py` | 两个 p<0.05 → PASS |

### 3.2 Layer A 验证（离线搜索）

```bash
# 1. 小规模 smoke (3-5 trials, 确认闭环)
python bayesian_optimizer.py --manifest strategies/var_strategy/manifest.yaml \
    --n-trials 5 --storage sqlite:///study.db

# 2. 极端对比 (隔离传参管道 vs 策略敏感性, lean_check.py)
# 极端低 vs 极端高参数, 订单数/Sharpe 应有显著差异

# 3. 正式搜索 (200 trials)
python bayesian_optimizer.py --manifest ... --n-trials 200 --storage sqlite:///study.db

# 4. DSR 衰减分析 (auto-update4)
python trials_decay_analyzer.py --log 200trials.log
# 判定: best_dsr 在 milestone 10/25/50/100/150/200 稳定, decay_ratio < 30%

# 5. dsrdiag 经验分析 (auto-update5, 确认 reward 平稳)
python docs/dsrdiag.py --storage sqlite:///study.db --study-name {name} \
    --objective-src bayesian_optimizer.py
# 判定: 静态扫描无高风险 (DSR 计算路径无 trial.number 引用)
```

**通过标准**：
- best Sharpe 随 trial 数增加而真正改善（非卡在 trial 0）
- DSR gate 事后校正 passed=True
- 参数脊岭未过收敛
- OOS DSR 双阈值（绝对≥0.5 且 OOS/IS≥0.6）满足

### 3.3 Layer C 验证（在线风控）

```bash
# 1. ONNX 推理确定性 (auto-update3 第2点)
python onnx_replay_verify.py --policy policy.pt --onnx policy.onnx --trace trace.jsonl
# 判定: max_abs_diff < 1e-4, deterministic=true

# 2. 延迟预算 (auto-update3 第3点)
python latency_budget.py --onnx policy.onnx --trace trace.jsonl --n 500
# 判定: round_trip p99 < 50ms

# 3. kill-switch 结构性 (auto-update3 第1点)
# C# 单测: Request_ReturnsFallback_WhenServerDown / FallbackAlpha_NeverThrows
dotnet test --filter RlRiskModel

# 4. live-paper 端到端回测
# 启动 inference_server + risk-mode=rl 回测
# 判定: 全程无 KILL-SWITCH 触发, 无 alpha 异常, IPC 稳定
```

**实测结果（已验证）**：
- ONNX max_abs_diff=1.79e-07 ✅
- ZeroMQ round-trip p99=0.072ms ✅
- live-paper 回测：composite 180 orders/1.954% vs rl 155 orders/1.870%（RL α<1 减仓，风控收紧代价 0.084%）✅

### 3.4 通用性验证（spec Phase 6 验收）

接入第 N 个策略时，确认**不改框架代码**：
```bash
# 仅新增 manifest + C# 接口实现
git diff --stat HEAD~1
# 应只显示: 新 manifest.yaml + 策略 .cs 改动 (加接口)
# 不应有: Scripts/auto_optimize/*.py 改动
```

已验证 4 策略全部 lint OK：option_vol_arb_5layer / var_strategy / barra_cne5_v4 / overnight_anomaly。

### 3.5 持续监控指标（上线后）

| 指标 | 阈值 | 工具 |
|---|---|---|
| kill-switch 触发次数 | 0（任何触发即需介入） | RlRiskModel 日志 |
| IPC 连续超时 | < 5（超 5 即 trip） | `_consecutiveTimeouts` |
| ONNX 推理延迟 p99 | < 50ms | `latency_budget.py` |
| 实盘 Sharpe vs 回测 | 衰减 < 30% | 周度对比 |
| DSR 门禁 | > 0 | `compute_dsr_gate` 周度重算 |
| 参数脊岭密度 | < 0.3 | `ridge_monitor.py` |

---

## 四、当前状态与后续

### 4.1 已完成

- ✅ 三层管线 + 五层架构落地（零侵入 LEAN 原生）
- ✅ Layer A Bayesian（平稳 Sharpe reward + 事后 DSR 门禁 + sqlite 持久化）
- ✅ Layer C PPO（ONNX freeze + ZeroMQ IPC + kill-switch）
- ✅ 4 策略接入（manifest + 接口实现）
- ✅ 五道验证门 + 过拟合防控（CPCV/walk-forward/ridge/双阈值/基线对比）
- ✅ auto-update1~7 全部修复完成

### 4.2 已知简化（后续补）

1. `SerializeRlState` 的 `pnl_1d`/`days_held`/`drawdown` 在 BarraCNE5V4/OvernightAnomaly 首期置 0（OptionVolArb5Layer 已填真实值）
2. `lean_step_runner` 需策略侧加 `RL_TRACE_PATH` env 读取（OptionVolArb5Layer 已实现）
3. VarStrategy 8 年回测超时（`RegimeScore` O(N²)/bar，需增量更新优化）
4. `deploy_gate: manual`（首期不自动 CI/CD 灰度）

### 4.3 进化闭环触发条件

- 数据累积达到 `train_window_days`（默认 504 = 2 年）
- 或实盘 Sharpe 衰减超过 30%
- 或 kill-switch 触发（需重训 policy）
- 或人工周期触发（月度/季度）

每次触发后跑完整 Layer A + Layer C 流程，通过五道门 + DSR 门禁后灰度部署。

---

## 五、引用

- 设计 spec: `docs/superpowers/specs/2026-07-05-auto-optimize-quant-strategy-design-claude.md`
- 实现计划: `docs/superpowers/plans/2026-07-05-auto-optimize-quant-strategy-implementation.md`
- auto-update1~7: `docs/auto-update*.md` + `docs/self-update*.md`
- López de Prado, *Advances in Financial Machine Learning*, Ch.7-8（CPCV, DSR）
- Bailey & López de Prado (2014): The Deflated Sharpe Ratio
