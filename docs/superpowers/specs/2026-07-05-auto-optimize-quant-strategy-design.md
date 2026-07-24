# 量化策略自动优化系统设计（Layer A Bayesian + Layer C PPO）

**日期:** 2026-07-05
**状态:** Draft → 待用户审核
**作者:** brainstorming session 2026-07-05

## 0. 目标与范围

构建一个自动优化量化策略的系统，覆盖两个层级：

- **Layer A（离线超参扫描）**：Bayesian Optimization（Optuna GP+EI）扫描策略超参向量 θ，reward = Deflated Sharpe Ratio (DSR)。
- **Layer C（在线风控 RL）**：离线训练 PPO policy，导出 ONNX，Python inference server 通过 ZeroMQ 向 Lean 风控模型提供 action。盘中 policy freeze，无在线梯度更新。

**首期交付范围**：Layer A + Layer C 同时交付。覆盖策略：`OptionVolArb5LayerStrategy`（已参数化）、`VarStrategy`（已参数化）。

## 1. 硬约束（最高优先级）

### 1.1 三个立足（源自 `2026-06-28-iv-calculation-fix-design.md` §三大设计原则）

1. **满足 A 股市场实际要求** — 真实 A 股标的（510050/510300/510500 ETF + CSI300）、Asia/Shanghai 时区、A 股 100 股整手规则。
2. **使用 tushare_data 真实交易数据** — 直接读 LEAN `Data/equity/{sse,szse}/daily/` 真实日线 TradeBar 与 SHIBOR 1Y，不模拟不伪造。
3. **使用 LEAN 原生优秀稳定算法** — 实现原生 `IFactor` / `IRiskManagementModel` 接口，复用 LEAN Portfolio/Statistics，不自算组合价值或统计。Bayesian/PPO 用 Python 成熟库（Optuna/stable-baselines3），不自写。

### 1.2 三层管线（源自 `2026-07-01-factor-zoo-design.md`）

```
Factor Zoo (Common/Factors/)  → 原子信号
   ↓ 消费
Model Zoo (Models/)           → 组合信号成决策
   ↓ 消费
Strategy (Algorithm.CSharp/)  → 固定 universe + 参数，端到端回测
```

本设计在 Model Zoo 加法新增 `RlRiskModel`（Layer C 的 C# 端 IPC client），不改 Factor Zoo、不改现有 Model Zoo 模型、不改现有 Strategy 结构（仅参数化 pass）。

### 1.3 五层架构（源自 `docs/LEANarch.md`）

```
L1 Universe → L2 Alpha → L3 Portfolio → L4 Risk → L5 Execution
```

A+C 双层只侵入 L4（Risk）：`RlRiskModel` 实现 `IRiskManagementModel`，通过 ZeroMQ 请求 Python inference server。其余 4 层完全保持 LEAN 原生。Layer A 不侵入任何层，只通过 `--parameters` 注入超参。

### 1.4 零侵入边界

- 不改任何现有因子类 / AlphaModel / Strategy 文件的默认行为
- 新增文件仅 `RlRiskModel.cs` + `RlRiskConfig.cs`（Model Zoo 加法）
- 策略参数化 pass：`OptionVolArb5LayerStrategy` 加 `GetParameterOrDefault`，默认值 = 当前硬编码值
- 现有策略回测 byte-for-byte 不变（`risk-mode=composite` 默认路径）

## 2. 决策汇总

| 维度 | 选择 | 理由 |
|---|---|---|
| RL 范式 | A+C 双层 | 离线超参 + 在线风控，功能完整 |
| 参数空间 | 灰盒优先 + 黑盒补充 | 先扫 `GetParameter`，关键类开 setter |
| Layer A 算法 | Bayesian (Optuna GP+EI) | 样本效率最高，50-200 次收敛 |
| Layer C 算法 | PPO (stable-baselines3) | 金融 RL 事实标准，稳定 |
| 训练架构 | Python 外驱 + Lean 子进程 | 零侵入 C#，与 SoloQuant 管线同构 |
| 过拟合防控 | Walk-forward OOS + CPCV + DSR + 参数脊岭 | 全选 |
| reward | DSR（主）+ 逐笔 shaping（PnL/VaR 惩罚） | self-update.md 建议 |
| Layer C IPC | ZeroMQ/UDS + 离线训练 + freeze 推理 | self-update.md 方案 |
| 首期范围 | A+C 同时 | 用户明确要求 |
| 部署 | ONNX + inference server，盘中固定权重 | 避开纯在线 RL 探索风险 |

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Python 元优化器层 (Scripts/auto_optimize/)                     │
│  ┌───────────────────────┐   ┌──────────────────────────────┐  │
│  │ Layer A: Bayesian     │   │ Layer C: PPO 训练器          │  │
│  │ (Optuna GP+EI)        │   │ (stable-baselines3 + gym)    │  │
│  │ 扫策略超参向量 θ       │   │ 训练风控 policy π(a|s)       │  │
│  └──────────┬────────────┘   └──────────┬───────────────────┘  │
│             │ trial{θ}                   │ rollout{s,a,r}       │
│             ▼                            ▼                      │
│  ┌───────────────────────┐   ┌──────────────────────────────┐  │
│  │ Lean 子进程驱动器      │   │ Lean 回测状态序列导出器      │  │
│  │ (subprocess + json)   │   │ (lean_step_runner)           │  │
│  └──────────┬────────────┘   └──────────┬───────────────────┘  │
└─────────────┼────────────────────────────┼─────────────────────┘
              │ --parameters               │ 状态序列
              ▼                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Lean 子进程 (QuantConnect.Lean.Launcher.dll)                   │
│                                                                 │
│  3 层管线: Factor Zoo → Model Zoo → Strategy                    │
│  5 层架构: L1 Universe → L2 Alpha → L3 Portfolio → L4 Risk → L5│
│                                                                 │
│  Layer C 注入点: L4 Risk (RlRiskModel 替换 CompositeRiskModel)  │
│    └─ ZeroMQ client → Python inference server (ONNX policy)     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.1 三层管线映射

- **Factor Zoo** 不变（复用 IV/HV/Skew/VaR 等既有因子）
- **Model Zoo** 新增 1 个模型：`RlRiskModel`（Layer C 的 C# 端 IPC client）。其他模型不变
- **Strategy** 不变结构，仅参数化（`GetParameter` 化所有可调阈值）

### 3.2 五层架构映射

A+C 双层只侵入 L4（Risk）——`RlRiskModel` 通过 ZeroMQ 向 Python inference server 请求 action（如"仓位缩放系数 α"），其余 4 层完全保持 LEAN 原生。Layer A 不侵入任何层，只通过 `--parameters` 注入超参。

## 4. 组件清单与文件结构

### 4.1 C# 侧（Model Zoo 加法，零侵入）

| 文件 | 职责 | 依赖 |
|---|---|---|
| `Algorithm.CSharp/Models/Risk/RlRiskConfig.cs` | Layer C 配置数据类：server endpoint、policy name、fallback action、请求超时 | 无 |
| `Algorithm.CSharp/Models/Risk/RlRiskModel.cs` | `IRiskManagementModel` 实现。每 rebalance 点：序列化状态 → ZeroMQ REQ → 解析 action（α∈[0,1]）→ `targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * α))`。不可达 → fallback | `RlRiskConfig`、`VaRFactor` |
| `Algorithm.CSharp/OptionVolArb5LayerStrategy.cs`（修改） | 参数化 pass + `risk-mode` 参数（`composite` 默认｜`rl`） | 既有模型 |

### 4.2 Python 侧（`Scripts/auto_optimize/`，新目录）

| 文件 | 职责 | 输入 → 输出 |
|---|---|---|
| `lean_runner.py` | Lean 子进程驱动器：subprocess + 读 `results.json` + 解析 Statistics | config、params → backtest_result |
| `reward.py` | reward 计算：DSR 闭式解 + Sharpe/Sortino/Calmar | stats + n_trials → reward dict |
| `cpcv.py` | Combinatorial Purged K-Fold 划分（López de Prado） | 日期 + 折数 + purge → 索引 |
| `walk_forward.py` | Walk-forward OOS 窗口划分 | 区间 + 训练/测试天数 → 窗口 |
| `ridge_monitor.py` | 参数脊岭监控 | trial 历史 → 是否收敛 |
| `bayesian_optimizer.py` | **Layer A 主驱动器**。Optuna TPE/GP+EI 扫 θ | 参数空间 + 配置 → best θ + 报告 |
| `lean_step_runner.py` | Layer C 状态序列导出器：跑回测，让 Lean 写 `state_trace.jsonl` | config + params → state_trace |
| `gym_env.py` | gym `Environment` 包装 state_trace，回放不跑 Lean | state_trace → gym env |
| `ppo_trainer.py` | **Layer C 训练器**。stable-baselines3 PPO + CPCV 多折 + 早停 | gym env → policy.pt |
| `policy_exporter.py` | PyTorch → ONNX 导出 + 一致性验证 | policy.pt → policy.onnx |
| `inference_server.py` | **Layer C 部署器**。ZeroMQ REP + ONNX forward，盘中固定权重 | ONNX + endpoint → server |
| `baseline_test.py` | **第四道门**：White's Reality Check / bootstrap p-value 对比基线 | 优化前后收益序列 → 显著性 |
| `config.yaml` | 优化配置：参数空间、目标、约束、训练超参、CPCV 折数 | — |

### 4.3 配置与产物

| 文件 | 用途 |
|---|---|
| `Launcher/config/config-option-vol-arb-5layer-rl.json` | Layer C 部署：`risk-mode=rl`、`rl-server-endpoint=tcp://127.0.0.1:5555`、`rl-fallback-alpha=0.5` |
| `Results/auto_optimize/{strategy}/{timestamp}/` | 每次运行产物：`optuna.db`、`best_params.json`、`state_trace.jsonl`、`ppo_policy.onnx`、`dsr_report.json`、`ridge_report.json`、`baseline_test_report.json` |

## 5. Layer A（Bayesian 超参扫描）数据流

### 5.1 端到端流程

```
config.yaml → bayesian_optimizer.py (Optuna study, GP+EI)
   │
   │  trial #i: θᵢ = {iv-rv-z:2.3, var-budget:0.015, ...}
   ▼
lean_runner.run_backtest(config, θᵢ)
   │  subprocess: dotnet Lean.Launcher.dll --parameters θᵢ.json
   ▼
{backtestId}.json (Statistics)
   ▼
reward.compute_reward(stats, n_trials, baseline_sharpe) → DSR
   ▼
Optuna trial.report(DSR)
   ▼
(after N trials) best_params
   ▼
CPCV 验证 + ridge_monitor → dsr_report.json + ridge_report.json
```

### 5.2 参数注入协议

复用 Lean 原生 `--parameters` 机制（`ConsoleLeanOptimizer.cs` 已验证）：

```bash
dotnet QuantConnect.Lean.Launcher.dll \
    --config /path/to/config.json \
    --parameters '{"iv-rv-z-score-threshold":2.3,"var-budget":0.015}'
```

参数命名约定（kebab-case，与 `VarStrategy` 一致）：

| 参数名 | 类型 | 范围 | 默认 | 作用层 |
|---|---|---|---|---|
| `iv-rv-z-score-threshold` | float | [1.5, 3.5] | 2.0 | L2 Alpha |
| `ivts-threshold` | float | [1.1, 1.6] | 1.3 | L2 Alpha |
| `skew-percentile-high` | float | [0.80, 0.95] | 0.90 | L2 Alpha |
| `skew-percentile-low` | float | [0.05, 0.20] | 0.10 | L2 Alpha |
| `var-budget` | float | [0.01, 0.04] | 0.02 | L4 Risk |
| `max-drawdown` | float | [0.10, 0.30] | 0.20 | L4 Risk |
| `max-position-weight` | float | [0.15, 0.45] | 0.30 | L4 Risk |
| `var-lookback-days` | int | [126, 504] | 252 | L4 Risk |

参数空间声明（`config.yaml`）支持 `log: true`（对数采样）、`step`（粒度建议）。

### 5.3 reward 函数（DSR 闭式解）

```python
def compute_reward(stats, n_trials, baseline_sharpe):
    sharpe = stats["Sharpe Ratio"]
    T = stats.get("TradingDays", 252 * 4)
    skew = stats.get("ReturnSkew", 0)
    kurt = stats.get("ReturnKurt", 3)
    se_sharpe = math.sqrt((1 - skew * sharpe + (kurt - 1) / 4 * sharpe**2) / (T - 1))
    # n_trials=1 边界: 无多重比较, DSR 退化为 PSR (baseline=0)
    if n_trials <= 1:
        expected_max = baseline_sharpe  # = 0 (首次)
    else:
        expected_max = baseline_sharpe + se_sharpe * norm.ppf(1 - 1 / n_trials)
    dsr = norm.cdf((sharpe - expected_max) / se_sharpe)
    return {"dsr": dsr, "sharpe": sharpe, ...}
```

`baseline_sharpe` = 前 N-1 次 trial 的 Sharpe 均值（滚动基线）；首次 trial 用 `baseline=0`。`n_trials=1` 时跳过多重比较修正，DSR 退化为 PSR（Probabilistic Sharpe Ratio）。

**退化处理**：若 Lean 不输出 `ReturnSkew`/`ReturnKurt`，退化到 `skew=0, kurt=3`（正态假设），log 警告。

### 5.4 CPCV 验证（防过拟合）

`cpcv.py` 实现 López de Prado CPCV：按时序切 N 折，对所有 C(N, N-1) 组合生成 train/test，purge 重叠 ±purge_bars，embargo test 后 embargo_bars 天。

**边界**：CPCV 在 Layer A 用于**验证**（best_params 是否稳健），不用于**搜索**（搜索阶段跑完整回测太贵）。搜索阶段用 walk-forward 单折。

### 5.5 Walk-forward 搜索阶段

```yaml
walk_forward:
  train_days: 504      # 2 年训练
  test_days: 126       # 0.5 年 OOS
  step_days: 126       # 每次前进 0.5 年
```

每个 Optuna trial 的 reward = OOS 窗口的 DSR（非 in-sample）。可选优化：先单窗口搜候选，再用 walk-forward 验证。

### 5.6 错误处理

| 失败模式 | 处理 |
|---|---|
| Lean 子进程崩溃 | reward = `-inf`，记 `user_attrs` |
| `results.json` 缺字段 | `{"dsr": 0, "sharpe": 0}`，不崩溃 |
| 0 订单 | reward = `-inf` + `no_trades=True` |
| 回测超时（>10 min） | 杀进程，reward = `-inf` |
| Optuna trial 重复参数 | Optuna 自动去重 |

## 6. Layer C（在线 PPO 风控）数据流

### 6.1 两阶段生命周期

```
阶段 1: 离线训练 (收盘后)
  lean_step_runner → state_trace.jsonl → gym_env → ppo_trainer → policy.pt → policy_exporter → policy.onnx

阶段 2: 在线推理 (live-paper / 实盘)
  inference_server (常驻, ONNX freeze) ↔ ZeroMQ ↔ RlRiskModel (Lean L4)
  盘中只 forward, 无梯度更新
```

### 6.2 状态空间 `s`（Lean → Python）

```json
{
  "ts": "2024-03-15T09:30:00+08:00",
  "strategy": "OptionVolArb5Layer",
  "tpv": 1027343.51,
  "cash_pct": 0.12,
  "positions": [{"sym":"510300","w":0.22,"pnl_1d":-0.003,"days_held":5}],
  "var_1d99": 0.018,
  "var_regime": 0.7,
  "drawdown": 0.04,
  "days_to_peak": 12,
  "n_open_positions": 4
}
```

状态维度 ~15-20 维连续。只用策略内部已算出的量（VaR/regime/回撤/持仓权重），**不引入新数据依赖**。`market_breadth` 标记可选，首期不实现。

### 6.3 动作空间 `a`（Python → Lean）

```json
{"action": "scale", "alpha": 0.65, "per_symbol_override": null}
```

`RlRiskModel` 应用：

```csharp
public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algo, IPortfolioTarget[] targets)
{
    var state = SerializeState(algo);
    var action = _client.Request(state, _config.TimeoutMs);
    var alpha = action?.Alpha ?? _config.FallbackAlpha;
    alpha = Math.Clamp(alpha, 0m, 1m);  // 首期只减不增
    return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
}
```

**首期约束**：`alpha = clamp(alpha, 0, 1.0)`（只减不增），避免 RL 在 live-paper 上激进加仓。

### 6.4 shaping reward（gym_env 内，dense signal）

```python
def step(self, action):
    alpha = action["alpha"]
    scaled_pnl = self._trace["pnl"].iloc[self._t] * alpha
    r = 0.0
    r += scaled_pnl
    r -= 0.5 * max(0, self._var - self._var_budget)   # VaR 超限惩罚
    r -= 2.0 * max(0, self._drawdown - self._max_dd)  # 回撤超限重罚
    if alpha < 0.1: r -= 0.1                          # 过度清仓惩罚
    if self._t == len(self._trace) - 1:
        r += self._episode_dsr * 10.0                 # episode 末 DSR bonus
    return r
```

shaping 提供密集梯度方向，DSR 在 episode 末做整体校准（self-update.md 第 9 行建议）。

### 6.5 ZeroMQ IPC 协议

JSON over ZeroMQ REQ/REP（首期不用 protobuf，可调试优先）。

**C# 侧**（`RlRiskModel.cs`，依赖 `NetMQ` 包）：

```csharp
public class RlRiskClient : IDisposable
{
    private readonly RequestSocket _socket;
    public RlRiskAction Request(RlRiskState state, int timeoutMs)
    {
        var json = JsonSerializer.Serialize(state);
        if (!_socket.TrySendFrame(TimeSpan.FromMilliseconds(timeoutMs), json)) return null;
        if (!_socket.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out var reply)) return null;
        return JsonSerializer.Deserialize<RlRiskAction>(reply);
    }
}
```

**Python 侧**（`inference_server.py`，`pyzmq` + `onnxruntime`）：

```python
class InferenceServer:
    def __init__(self, onnx_path, endpoint="tcp://127.0.0.1:5555"):
        self.session = onnxruntime.InferenceSession(onnx_path)
        self.socket = zmq.REP(); self.socket.bind(endpoint)

    def serve_forever(self):
        while True:
            state_json = self.socket.recv_string()
            state_vec = self._encode(state_json)
            action_vec = self.session.run(None, {self.input_name: state_vec})[0]
            self.socket.send_string(json.dumps(self._decode(action_vec)))
```

### 6.6 ONNX 导出与验证

`policy_exporter.py` 用 `torch.onnx.export`（opset 14，dynamic_axes 支持 batch），验证 ONNX 输出 == PyTorch（atol=1e-5）。

### 6.7 fallback 与安全降级

`RlRiskModel` 永不抛异常——任何 IPC 故障降级到固定 `fallbackAlpha`（默认 0.5）：

| 触发 | 行为 |
|---|---|
| ZeroMQ 连接失败 | log 警告，每次返回 fallback，不阻塞 |
| 请求超时（>200ms） | 该次 fallback；连续 10 次 → log 错误 |
| action JSON 解析失败 | fallback |
| α 为 NaN/Inf | fallback |
| live-paper 首日（无 policy） | 策略 `risk-mode=composite` 直到 policy 部署 |

### 6.8 离线增量训练调度

```yaml
ppo_training:
  retrain_frequency: weekly
  train_window_days: 504
  cpcv_folds: 6
  early_stop_patience: 20
  deploy_gate: manual        # 人工审核 DSR 后发布, 首期不自动 CI/CD
  rollout_policy: greedy     # 部署时无探索噪声
```

`deploy_gate: manual` 落地 self-update.md 第 12 行"人工审核 DSR 是否劣化后再替换线上 policy"。

## 7. 测试策略与验证门

### 7.1 测试金字塔

```
端到端验证 (3)  ← 真实回测 + DSR 报告 + 基线对比
集成测试 (6)    ← Lean 子进程 ↔ Python server
单元测试 (15)   ← 各模块独立
```

### 7.2 单元测试清单

**C# 侧（`Tests/Algorithm/Models/RlRiskModelTests.cs`）**

| 测试 | 验证点 |
|---|---|
| `SerializeState_CapturesAllFields` | 状态 JSON 含 tpv/positions/var/drawdown |
| `Request_ReturnsAction_WhenServerUp` | mock server 返回 α=0.6 → targets.Quantity × 0.6 |
| `Request_ReturnsFallback_WhenServerDown` | server 未启动 → fallback，不抛异常 |
| `Request_ReturnsFallback_OnTimeout` | mock server sleep 500ms，超时 200ms → fallback |
| `Request_ReturnsFallback_OnMalformedJson` | server 返回 garbage → fallback |
| `Alpha_ClampedToZeroOne` | α=2.0/-0.5/NaN → clamp [0,1] |
| `FallbackAlpha_NeverThrows` | 连续 10 次超时，第 11 次正常 |
| `ManageRisk_PreservesZeroTargets` | Quantity=0 不被 α 影响 |

**Python 侧（`Tests/Python/`）**

| 测试 | 验证点 |
|---|---|
| `test_cpcv.py::test_no_overlap` | 训练/测试折无日期重叠 |
| `test_cpcv.py::test_embargo_applied` | test 折后 embargo_bars 不进 train |
| `test_cpcv.py::test_n_folds_coverage` | N 折覆盖所有日期 |
| `test_reward.py::test_dsr_known_solution` | DSR 匹配 López de Prado 解析解 |
| `test_reward.py::test_dsr_monotonic_in_n_trials` | 固定 Sharpe，N 增 → DSR 降 |
| `test_reward.py::test_no_trades_returns_neg_inf` | 0 订单 → reward=-inf |
| `test_ridge_monitor.py::test_convergence_detected` | 收敛序列 → True |
| `test_ridge_monitor.py::test_divergence_not_flagged` | 分散 → False |
| `test_gym_env.py::test_reset_returns_initial_state` | reset 返回起点 |
| `test_gym_env.py::test_step_applies_alpha` | step(α=0.5) → pnl × 0.5 |
| `test_gym_env.py::test_episode_dsr_bonus` | episode 末含 DSR bonus |
| `test_policy_exporter.py::test_onnx_matches_pytorch` | ONNX == PyTorch (atol=1e-5) |
| `test_inference_server.py::test_roundtrip` | 发状态收 action，α∈[0,1] |
| `test_inference_server.py::test_concurrent_requests` | 100 并发无串扰 |
| `test_lean_runner.py::test_shortest_backtest` | 1 个月回测，解析正确 |

### 7.3 集成测试

| 测试 | 验证点 |
|---|---|
| `test_lean_runner_integration` | 真实启动 Lean 子进程，返回 statistics |
| `test_risk_model_with_mock_server` | C# RlRiskModel + Python mock server 往返 |
| `test_layer_a_smoke` | Optuna 5 trials（1 月回测），best_params + optuna.db 落盘 |
| `test_layer_c_training_smoke` | 1 年 trace 训练 PPO 100 步，policy.pt + onnx 产出 |
| `test_inference_server_load` | 1000 请求压测，p99 < 50ms |
| `test_walk_forward_pipeline` | 完整 walk-forward，OOS DSR < IS DSR |

### 7.4 四道验证门（含 self-update2.md 反馈）

**门 1：编译**

```bash
dotnet build QuantConnect.Lean.sln
# 0 error. 现有策略编译不受影响.
```

**门 2：单元 + 集成测试**

```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "RlRiskModel"
pytest Tests/Python/test_cpcv.py test_reward.py test_gym_env.py test_policy_exporter.py
# 全绿.
```

**门 3：回测回归（byte-for-byte 不变）**

| 策略 | 默认参数回测 | 验证 |
|---|---|---|
| `OptionVolArb5LayerStrategy` | `risk-mode=composite`（默认） | OrderListHash == 改动前 |
| `OptionVolArb5LayerStrategy` | `risk-mode=rl` + mock server | 全程 fallback，不崩溃，订单 > 0 |
| `VarStrategy`（不修改） | 默认参数 | OrderListHash == 改动前（不应触及） |

**门 4：基线对比（统计显著性，新增）**

防止"代码没坏但优化无效"或"框架跑不赢无脑基准"。对比两个基线：

- **基线 A**：优化前自己（同策略上一版本 best_params）—— 防止负优化
- **基线 B**：买入持有 / 等权基准 —— 防止框架本身跑不赢无脑基准

方法：**White's Reality Check (WRC)** 或 bootstrap p-value，不用数值比大小（避免 1.1→1.15 噪声级提升误判，与 DSR 同源逻辑）。

`baseline_test.py` 输出 `baseline_test_report.json`：

```json
{
  "vs_previous_version": {"p_value": 0.03, "significant": true, "metric": "DSR"},
  "vs_buy_and_hold": {"p_value": 0.08, "significant": false, "metric": "DSR"},
  "verdict": "PASS"
}
```

`verdict` 判定：两个 p_value 均 < 0.05 → PASS；任一不显著 → WARNING（人工审核）。

### 7.5 过拟合防控验证（self-update2.md 修订）

Layer A 优化完成后，`dsr_report.json`：

```json
{
  "n_trials": 200,
  "dsr_is": 0.92,
  "dsr_oos_cpcv_mean": 0.61,
  "dsr_oos_cpcv_std": 0.18,
  "overfitting_flag": "WARNING",
  "overfitting_reason": "OOS DSR / IS DSR = 0.66 ≥ 0.6 但绝对值 0.61 ≥ 0.5; 衰减 33.7%",
  "ridge_converged": false
}
```

**双阈值判定**（修订，防止"作弊分数定合格线"漏洞）：

- **绝对下限**：`OOS DSR ≥ 0.5`（DSR 已编码试验次数，比 Sharpe 公平）
- **衰减比例**：`OOS DSR / IS DSR ≥ 0.6`（泛化能力）
- 两者缺一不可

判定：

- `PASS`：两个阈值均满足
- `WARNING`：OOS 表现衰减明显但仍可用，人工审核
- `FAIL`：OOS DSR < 0.5 或衰减 > 60% → best_params 不可部署

**关键**：OOS 阶段直接算 DSR 而非普通 Sharpe（self-update2.md 第 11 行：DSR 已编码"扫了多少次"，固定 0.6 比例对 10 次和 10000 次试验不公平）。

### 7.6 Layer C 训练验证

| 指标 | 通过标准 |
|---|---|
| policy 收敛 | episode reward 后 20% 均值 > 前 20% 均值 |
| ONNX 一致性 | `test_onnx_matches_pytorch` 通过 |
| CPCV 多折稳定 | 各折 episode reward std/mean < 0.5 |
| 安全检查 | α 测试 trace 上始终 ∈ [0, 1] |
| 不退化 | α ≠ 0（非过度清仓 trivial 解） |

### 7.7 性能基准

| 场景 | 目标 |
|---|---|
| Layer A 单 trial（4 年回测） | < 5 min |
| Layer A 200 trials | < 24 h（并发 4） |
| Layer C 训练（2 年 trace，PPO 10k 步） | < 30 min |
| inference server 单次请求 | p99 < 10ms |
| `RlRiskModel.ManageRisk` 含 IPC | < 50ms |

### 7.8 测试数据准备（self-update2.md 折中方案）

| 夹具 | 来源 | 是否 commit |
|---|---|---|
| `state_trace_schema_sample.jsonl` | 3-5 条，纯字段格式验证 | ✅ commit（稳定基线，未来不漂移） |
| 完整 `state_trace.jsonl` | 每次训练动态生成 | ❌ CI artifact，不进 repo |
| `results_sample.json` | 真实回测脱敏 | ✅ commit |
| `policy_sample.onnx` | toy policy 导出 | ✅ commit |
| mock ZeroMQ server | 测试内联 | — |

## 8. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| `NetMQ` 新增 C# 依赖 | 备选：`System.Net.Sockets` 自写极简 TCP（牺牲 ZeroMQ 重连/超时） |
| Lean `ReturnSkew`/`ReturnKurt` 可能不输出 | DSR 退化到正态假设（skew=0, kurt=3），log 警告 |
| `--parameters` JSON 注入需验证 | 集成测试 `test_lean_runner_integration` 首先验证此路径 |
| PPO 在金融数据上易过拟合 | CPCV 多折 + DSR + 早停 + 人工 deploy_gate |
| ZeroMQ 在 Windows live-paper 环境 | 首期仅 Linux，Windows 支持后续 |
| 状态空间维度可能不足 | 首期 ~15-20 维，后续可加市场状态（沪深300波动率/北向资金） |

## 9. 实现顺序建议

1. **Phase 1**：参数化 pass + `RlRiskModel.cs` + `RlRiskConfig.cs` + 单元测试（门 1-3）
2. **Phase 2**：`lean_runner.py` + `reward.py` + `cpcv.py` + `walk_forward.py` + `ridge_monitor.py` + Layer A smoke test
3. **Phase 3**：`lean_step_runner.py` + `gym_env.py` + `ppo_trainer.py` + `policy_exporter.py` + Layer C smoke test
4. **Phase 4**：`inference_server.py` + `baseline_test.py` + 集成测试 + 第四道门
5. **Phase 5**：端到端验证 + 过拟合诊断报告 + 人工审核 gate

## 10. 引用

- `docs/self-update.md` — Layer C IPC + 训练模式方案
- `docs/self-update2.md` — §5 反馈（第四道门 + 双阈值 + trace 折中）
- `docs/LEANarch.md` — 五层架构定义
- `docs/superpowers/specs/2026-07-01-factor-zoo-design.md` — 三层管线 + 零侵入原则
- `docs/superpowers/specs/2026-07-03-var-strategy-design.md` — 三个立足要求
- López de Prado, "Advances in Financial Machine Learning" Ch.7-8 (CPCV, DSR)
