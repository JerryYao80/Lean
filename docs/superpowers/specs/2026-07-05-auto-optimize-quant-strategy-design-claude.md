# 量化策略自动优化通用架构设计（Layer A Bayesian + Layer C PPO）

**日期:** 2026-07-05
**状态:** Draft → 待用户审核
**版本说明:** 在原 `2026-07-05-auto-optimize-quant-strategy-design.md` 基础上，将系统从"绑定 `OptionVolArb5LayerStrategy` / `VarStrategy` 两个具体策略"重构为"**任意已参数化 LEAN 策略均可插件式接入**"的通用优化框架。

## 0. 目标与范围

构建一个**策略无关**的自动优化系统，覆盖两个层级，对任何符合"接入契约"（见 §2）的 LEAN 策略均可复用，无需为每个新策略重写优化器本体：

- **Layer A（离线超参扫描）**：Bayesian Optimization（Optuna GP+EI）扫描任意策略声明的超参向量 θ，reward = Deflated Sharpe Ratio (DSR)。
- **Layer C（在线风控 RL）**：离线训练 PPO policy，导出 ONNX，Python inference server 通过 ZeroMQ 向任意策略的 L4 风控节点提供 action。盘中 policy freeze，无在线梯度更新。

**接入方式**：新策略只需提供一份 **策略清单（Strategy Manifest）** + 在 C# 侧实现两个标记接口，即可复用全部 Layer A / Layer C 基础设施，无需修改 `Scripts/auto_optimize/` 下任何优化器代码。

**首期验证策略**：`OptionVolArb5LayerStrategy`、`VarStrategy`（用作参照实现与回归基准，不再是系统的硬编码目标）。

## 1. 硬约束（最高优先级，与具体策略无关）

### 1.1 三个立足

1. **满足 A 股市场实际要求** — 真实 A 股标的、Asia/Shanghai 时区、A 股 100 股整手规则。这是通用约束，任何接入策略的 universe 定义都需遵守，但具体标的由各策略自行在 Manifest 中声明，优化框架不假设特定标的。
2. **使用真实交易数据** — 直接读 LEAN 原生数据源（`Data/equity/{sse,szse}/daily/` 等），不模拟不伪造。数据源路径由策略 Manifest 指定，框架本身不硬编码路径。
3. **使用 LEAN 原生优秀稳定算法** — 复用 LEAN Portfolio/Statistics 与原生接口（`IFactor` / `IRiskManagementModel`），不自算组合价值或统计；Bayesian/PPO 用成熟库（Optuna/stable-baselines3），不自写。

### 1.2 三层管线（通用映射，不绑定具体因子/模型）

```
Factor Zoo (Common/Factors/)  → 原子信号（各策略自选因子子集）
   ↓ 消费
Model Zoo (Models/)           → 组合信号成决策（新增 1 个通用 RlRiskModel）
   ↓ 消费
Strategy (Algorithm.CSharp/)  → 固定 universe + 参数，端到端回测（任意已实现接入契约的策略）
```

优化框架只在 Model Zoo 加法新增 **一个** 通用 `RlRiskModel`（Layer C 的 C# 端 IPC client），不改 Factor Zoo、不改现有 Model Zoo 模型；对 Strategy 层，只要求该策略完成"参数化 pass"和"接入契约"实现，不限定是哪个策略。

### 1.3 五层架构（通用映射）

```
L1 Universe → L2 Alpha → L3 Portfolio → L4 Risk → L5 Execution
```

A+C 双层只侵入 L4（Risk）：任意策略的 `IRiskManagementModel` 若替换为通用 `RlRiskModel`，即可接入 Layer C；Layer A 不侵入任何层，只通过 LEAN 原生 `--parameters` 注入策略自己声明的超参。**这一映射对所有策略一致，不因策略而异。**

### 1.4 零侵入边界（通用规则）

- 不改任何现有因子类 / AlphaModel / 具体 Strategy 文件的默认行为
- 优化框架侧新增文件仅 `RlRiskModel.cs` + `RlRiskConfig.cs` + 策略清单加载器（Model Zoo 加法，与策略数量无关）
- 每个接入策略自行完成"参数化 pass"：`GetParameterOrDefault`，默认值 = 当前硬编码值
- 现有策略回测 byte-for-byte 不变（`risk-mode=composite` 默认路径），对所有接入策略一致要求

## 2. 策略接入契约（Strategy Adapter Contract）—— 通用化核心

这是本次重构的关键新增部分：把"策略特定的因子/参数/状态定义"从优化器代码中剥离，改为**声明式清单**，使框架对策略数量和种类保持无关。

### 2.1 策略清单（`strategy_manifest.yaml`）

每个接入策略在 `Scripts/auto_optimize/strategies/{strategy_name}/manifest.yaml` 提供：

```yaml
strategy_name: OptionVolArb5LayerStrategy   # 示例：任意策略名
lean_config: Launcher/config/config-{strategy_name}.json
risk_model_target: CompositeRiskModel        # 该策略默认使用、将被 RlRiskModel 替换的目标类

parameter_space:                             # Layer A 扫描的超参声明（策略自定义，数量任意）
  - name: iv-rv-z-score-threshold
    type: float
    range: [1.5, 3.5]
    default: 2.0
    layer: L2_Alpha
  - name: var-budget
    type: float
    range: [0.01, 0.04]
    default: 0.02
    layer: L4_Risk
    log: false

state_schema:                                # Layer C 状态空间声明（维度、字段任意）
  fields:
    - {name: tpv, type: float}
    - {name: var_1d99, type: float}
    - {name: drawdown, type: float}
    - {name: positions, type: array, item_schema: [sym, w, pnl_1d, days_held]}
  dim_hint: 15-20

reward_config:
  primary: dsr
  shaping:
    - {term: scaled_pnl, weight: 1.0}
    - {term: var_excess_penalty, weight: 0.5}
    - {term: drawdown_excess_penalty, weight: 2.0}

universe:
  symbols: ["510050", "510300", "510500", "CSI300"]
  timezone: Asia/Shanghai
```

`bayesian_optimizer.py`、`ppo_trainer.py`、`gym_env.py`、`reward.py` 等**全部从 manifest 读取配置**，不含任何策略名的硬编码分支。新增策略 = 新增一个 manifest 目录，无需改优化器代码。

### 2.2 C# 侧标记接口（每个接入策略实现，数量与策略无关）

```csharp
// 策略需实现此接口以支持 Layer A 参数化扫描
public interface IOptimizableStrategy
{
    // 返回策略声明的可调参数名列表（与 manifest.parameter_space 对应，用于 CI 校验一致性）
    IEnumerable<string> GetTunableParameterNames();
}

// 策略需实现此接口以支持 Layer C 状态导出（若参与 RL 风控）
public interface IRlStateExportable
{
    // 序列化当前策略状态为 JSON，字段需与 manifest.state_schema 一致
    string SerializeRlState(QCAlgorithm algo);
}
```

`RlRiskModel` 本身与具体策略解耦：它只依赖 `IRlStateExportable.SerializeRlState()` 拿到状态 JSON，不关心状态来自哪个策略。

### 2.3 一致性校验（新增门禁）

`manifest_lint.py`：CI 阶段校验

- `parameter_space` 中每个 `name` 均能在策略的 `GetParameterOrDefault` 调用中找到对应 key（防止 manifest 与代码漂移）
- `state_schema.fields` 与 `SerializeRlState()` 输出字段集合一致
- `risk_model_target` 类确实存在且实现 `IRiskManagementModel`

不通过 → 该策略不允许进入 Layer A / Layer C 流程，报错列出具体不一致字段。

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Python 元优化器层 (Scripts/auto_optimize/)  —— 策略无关          │
│  ┌───────────────────────┐   ┌──────────────────────────────┐  │
│  │ Layer A: Bayesian     │   │ Layer C: PPO 训练器          │  │
│  │ (Optuna GP+EI)        │   │ (stable-baselines3 + gym)    │  │
│  │ 从 manifest 读参数空间 │   │ 从 manifest 读状态/reward配置 │  │
│  └──────────┬────────────┘   └──────────┬───────────────────┘  │
│             │ trial{θ}                   │ rollout{s,a,r}       │
│             ▼                            ▼                      │
│  ┌───────────────────────┐   ┌──────────────────────────────┐  │
│  │ Lean 子进程驱动器      │   │ Lean 回测状态序列导出器      │  │
│  │ (subprocess + json)   │   │ (lean_step_runner)           │  │
│  └──────────┬────────────┘   └──────────┬───────────────────┘  │
└─────────────┼────────────────────────────┼─────────────────────┘
              │ --parameters (由manifest生成) │ 状态序列
              ▼                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Lean 子进程 (QuantConnect.Lean.Launcher.dll)                   │
│  运行 manifest.lean_config 指向的任意接入策略                    │
│                                                                 │
│  3 层管线: Factor Zoo → Model Zoo → Strategy                    │
│  5 层架构: L1 Universe → L2 Alpha → L3 Portfolio → L4 Risk → L5│
│                                                                 │
│  Layer C 注入点: L4 Risk (通用 RlRiskModel 替换                  │
│                  manifest.risk_model_target)                    │
│    └─ ZeroMQ client → Python inference server (ONNX policy)     │
└─────────────────────────────────────────────────────────────────┘
```

策略在图中的唯一入口是 **manifest 文件**；优化器主体（Bayesian/PPO/reward/CPCV/walk-forward/inference server）对所有策略共用同一份代码。

## 4. 组件清单与文件结构（通用化）

### 4.1 C# 侧（Model Zoo 加法，与策略数量无关）

| 文件 | 职责 | 依赖 |
|---|---|---|
| `Algorithm.CSharp/Models/Risk/RlRiskConfig.cs` | Layer C 配置数据类：server endpoint、policy name、fallback action、请求超时 | 无 |
| `Algorithm.CSharp/Models/Risk/RlRiskModel.cs` | 通用 `IRiskManagementModel` 实现，通过 `IRlStateExportable` 拿状态，不关心具体策略 | `RlRiskConfig`、目标策略实现 `IRlStateExportable` |
| `Algorithm.CSharp/Common/IOptimizableStrategy.cs`（新增） | 标记接口，供 manifest 一致性校验使用 | 无 |
| `Algorithm.CSharp/Common/IRlStateExportable.cs`（新增） | 标记接口，供 `RlRiskModel` 调用 | 无 |
| 各接入策略文件（如 `OptionVolArb5LayerStrategy.cs`、`VarStrategy.cs`，修改） | 参数化 pass + 实现上述两个接口 + `risk-mode` 参数（`composite` 默认｜`rl`） | 既有模型 |

### 4.2 Python 侧（`Scripts/auto_optimize/`，策略无关）

| 文件 | 职责 | 输入 → 输出 |
|---|---|---|
| `manifest_loader.py`（新增） | 解析 `strategies/{name}/manifest.yaml`，提供统一配置对象 | manifest 路径 → `StrategyManifest` |
| `manifest_lint.py`（新增） | CI 一致性校验（§2.3） | manifest + 策略源码 → pass/fail 报告 |
| `lean_runner.py` | Lean 子进程驱动器：subprocess + 读 `results.json` + 解析 Statistics | manifest + params → backtest_result |
| `reward.py` | reward 计算：DSR 闭式解 + Sharpe/Sortino/Calmar，reward 组成由 `manifest.reward_config` 驱动 | stats + n_trials + reward_config → reward dict |
| `cpcv.py` | Combinatorial Purged K-Fold 划分（López de Prado） | 日期 + 折数 + purge → 索引 |
| `walk_forward.py` | Walk-forward OOS 窗口划分 | 区间 + 训练/测试天数 → 窗口 |
| `ridge_monitor.py` | 参数脊岭监控 | trial 历史 → 是否收敛 |
| `bayesian_optimizer.py` | **Layer A 主驱动器**。从 manifest 读参数空间，Optuna TPE/GP+EI 扫 θ | manifest → best θ + 报告 |
| `lean_step_runner.py` | Layer C 状态序列导出器：跑回测，让 Lean 写 `state_trace.jsonl`（字段由 manifest.state_schema 校验） | manifest + params → state_trace |
| `gym_env.py` | gym `Environment` 包装 state_trace，回放不跑 Lean，reward shaping 由 manifest 驱动 | state_trace + manifest → gym env |
| `ppo_trainer.py` | **Layer C 训练器**。stable-baselines3 PPO + CPCV 多折 + 早停 | gym env → policy.pt |
| `policy_exporter.py` | PyTorch → ONNX 导出 + 一致性验证 | policy.pt → policy.onnx |
| `inference_server.py` | **Layer C 部署器**。ZeroMQ REP + ONNX forward，按策略名路由 endpoint | ONNX + endpoint → server |
| `baseline_test.py` | **第四道门**：White's Reality Check / bootstrap p-value 对比基线 | 优化前后收益序列 → 显著性 |

### 4.3 目录结构（新增 manifest 层）

```
Scripts/auto_optimize/
├── strategies/
│   ├── option_vol_arb_5layer/manifest.yaml
│   ├── var_strategy/manifest.yaml
│   └── {new_strategy}/manifest.yaml        # 新策略只需加这一份文件
├── manifest_loader.py
├── manifest_lint.py
├── bayesian_optimizer.py    # 策略无关
├── ppo_trainer.py           # 策略无关
├── ...（其余同上表）
└── config.yaml              # 全局默认（CPCV折数、训练超参等，可被 manifest 覆盖）
```

### 4.4 配置与产物（按策略分目录，框架逻辑不变）

| 路径 | 用途 |
|---|---|
| `Launcher/config/config-{strategy_name}-rl.json` | 该策略 Layer C 部署配置：`risk-mode=rl`、endpoint、fallback-alpha |
| `Results/auto_optimize/{strategy_name}/{timestamp}/` | 每次运行产物：`optuna.db`、`best_params.json`、`state_trace.jsonl`、`ppo_policy.onnx`、`dsr_report.json`、`ridge_report.json`、`baseline_test_report.json` |

## 5. Layer A（Bayesian 超参扫描）数据流（通用）

### 5.1 端到端流程

```
strategies/{name}/manifest.yaml → manifest_loader → bayesian_optimizer.py (Optuna study, GP+EI)
   │
   │  trial #i: θᵢ 从 manifest.parameter_space 采样
   ▼
lean_runner.run_backtest(manifest, θᵢ)
   │  subprocess: dotnet Lean.Launcher.dll --config manifest.lean_config --parameters θᵢ.json
   ▼
{backtestId}.json (Statistics)
   ▼
reward.compute_reward(stats, n_trials, baseline_sharpe, manifest.reward_config) → DSR
   ▼
Optuna trial.report(DSR)
   ▼
(after N trials) best_params
   ▼
CPCV 验证 + ridge_monitor → dsr_report.json + ridge_report.json
```

任何策略只要提供合法 manifest，即可复用该流程；流程本身不含策略特定分支。

### 5.2 参数注入协议（通用机制，参数集合来自 manifest）

复用 LEAN 原生 `--parameters` 机制（`ConsoleLeanOptimizer.cs` 已验证）：

```bash
dotnet QuantConnect.Lean.Launcher.dll \
    --config {manifest.lean_config} \
    --parameters '{θ 由 manifest.parameter_space 自动生成}'
```

参数命名沿用各策略自身既有 kebab-case 习惯，具体参数列表**不再由本文档硬编码**，而是各策略 manifest 自行声明（示例见 §2.1）。参数空间声明支持 `log: true`（对数采样）、`step`（粒度建议）。

### 5.3 reward 函数（DSR 闭式解，通用，不变）

```python
def compute_reward(stats, n_trials, baseline_sharpe, reward_config=None):
    sharpe = stats["Sharpe Ratio"]
    T = stats.get("TradingDays", 252 * 4)
    skew = stats.get("ReturnSkew", 0)
    kurt = stats.get("ReturnKurt", 3)
    se_sharpe = math.sqrt((1 - skew * sharpe + (kurt - 1) / 4 * sharpe**2) / (T - 1))
    if n_trials <= 1:
        expected_max = baseline_sharpe  # = 0（首次）
    else:
        expected_max = baseline_sharpe + se_sharpe * norm.ppf(1 - 1 / n_trials)
    dsr = norm.cdf((sharpe - expected_max) / se_sharpe)
    return {"dsr": dsr, "sharpe": sharpe, ...}
```

`baseline_sharpe` = 该策略前 N-1 次 trial 的 Sharpe 均值（按 `strategy_name` 分组的滚动基线，不同策略互不影响）；首次 trial 用 `baseline=0`。`n_trials=1` 时跳过多重比较修正，DSR 退化为 PSR。

**退化处理**：若 Lean 不输出 `ReturnSkew`/`ReturnKurt`，退化到 `skew=0, kurt=3`（正态假设），log 警告。

### 5.4 CPCV 验证（防过拟合，通用）

`cpcv.py` 实现 López de Prado CPCV：按时序切 N 折，对所有 C(N, N-1) 组合生成 train/test，purge 重叠 ±purge_bars，embargo test 后 embargo_bars 天。折数、purge/embargo 参数默认取全局 `config.yaml`，可被单个策略 manifest 覆盖。

**边界**：CPCV 用于**验证**（best_params 是否稳健），不用于**搜索**（搜索阶段跑完整回测太贵）。搜索阶段用 walk-forward 单折。

### 5.5 Walk-forward 搜索阶段（默认值，可被 manifest 覆盖）

```yaml
walk_forward:
  train_days: 504      # 2 年训练
  test_days: 126        # 0.5 年 OOS
  step_days: 126        # 每次前进 0.5 年
```

每个 Optuna trial 的 reward = OOS 窗口的 DSR（非 in-sample）。

### 5.6 错误处理（通用，与策略无关）

| 失败模式 | 处理 |
|---|---|
| Lean 子进程崩溃 | reward = `-inf`，记 `user_attrs` |
| `results.json` 缺字段 | `{"dsr": 0, "sharpe": 0}`，不崩溃 |
| 0 订单 | reward = `-inf` + `no_trades=True` |
| 回测超时（>10 min） | 杀进程，reward = `-inf` |
| Optuna trial 重复参数 | Optuna 自动去重 |
| manifest 校验未通过 | 拒绝启动该策略的优化流程，报具体字段错误 |

## 6. Layer C（在线 PPO 风控）数据流（通用）

### 6.1 两阶段生命周期

```
阶段 1: 离线训练（收盘后，按 strategy_name 分策略跑）
  lean_step_runner(manifest) → state_trace.jsonl → gym_env(manifest) → ppo_trainer → policy.pt → policy_exporter → policy.onnx

阶段 2: 在线推理（live-paper / 实盘）
  inference_server（常驻，每策略一个 endpoint，ONNX freeze）↔ ZeroMQ ↔ RlRiskModel（该策略的 L4）
  盘中只 forward，无梯度更新
```

### 6.2 状态空间 `s`（由 manifest.state_schema 定义，示例）

```json
{
  "ts": "2024-03-15T09:30:00+08:00",
  "strategy": "{strategy_name}",
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

字段集合由各策略 manifest 自定义，`gym_env.py`/`inference_server.py` 按 manifest 动态构造编码/解码逻辑，不写死字段名。状态维度典型 ~15-20 维连续，只用策略内部已算出的量，**不引入新数据依赖**。

### 6.3 动作空间 `a`（Python → Lean，通用协议）

```json
{"action": "scale", "alpha": 0.65, "per_symbol_override": null}
```

`RlRiskModel` 应用（对任意实现 `IRlStateExportable` 的策略一致）：

```csharp
public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algo, IPortfolioTarget[] targets)
{
    var state = (algo as IRlStateExportable)?.SerializeRlState(algo);
    var action = _client.Request(state, _config.TimeoutMs);
    var alpha = action?.Alpha ?? _config.FallbackAlpha;
    alpha = Math.Clamp(alpha, 0m, 1m);  // 首期只减不增
    return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
}
```

**首期约束（对所有接入策略统一）**：`alpha = clamp(alpha, 0, 1.0)`（只减不增），避免 RL 在 live-paper 上激进加仓。

### 6.4 shaping reward（gym_env 内，由 manifest.reward_config.shaping 驱动）

```python
def step(self, action):
    alpha = action["alpha"]
    scaled_pnl = self._trace["pnl"].iloc[self._t] * alpha
    r = 0.0
    for term in self._manifest.reward_config.shaping:
        r += term.weight * self._eval_term(term.term, alpha, scaled_pnl)
    if self._t == len(self._trace) - 1:
        r += self._episode_dsr * 10.0   # episode 末 DSR bonus，通用
    return r
```

shaping 项集合由 manifest 声明（示例：`scaled_pnl`、`var_excess_penalty`、`drawdown_excess_penalty`），新增策略若需要自定义 shaping 项，只需在 manifest 中声明并在 `_eval_term` 的注册表中实现对应函数，无需改动 `step()` 主逻辑。

### 6.5 ZeroMQ IPC 协议（通用，端口按策略分配）

JSON over ZeroMQ REQ/REP（首期不用 protobuf，可调试优先）。每个策略的 `inference_server` 实例监听独立 endpoint（如 `tcp://127.0.0.1:5555` + 策略序号偏移），互不干扰。

**C# 侧**（`RlRiskModel.cs`，依赖 `NetMQ` 包，与策略无关）：

```csharp
public class RlRiskClient : IDisposable
{
    private readonly RequestSocket _socket;
    public RlRiskAction Request(string stateJson, int timeoutMs)
    {
        if (!_socket.TrySendFrame(TimeSpan.FromMilliseconds(timeoutMs), stateJson)) return null;
        if (!_socket.TryReceiveFrameString(TimeSpan.FromMilliseconds(timeoutMs), out var reply)) return null;
        return JsonSerializer.Deserialize<RlRiskAction>(reply);
    }
}
```

**Python 侧**（`inference_server.py`，`pyzmq` + `onnxruntime`，按 manifest 参数化）：

```python
class InferenceServer:
    def __init__(self, manifest, onnx_path, endpoint):
        self.manifest = manifest
        self.session = onnxruntime.InferenceSession(onnx_path)
        self.socket = zmq.REP(); self.socket.bind(endpoint)

    def serve_forever(self):
        while True:
            state_json = self.socket.recv_string()
            state_vec = self._encode(state_json, self.manifest.state_schema)
            action_vec = self.session.run(None, {self.input_name: state_vec})[0]
            self.socket.send_string(json.dumps(self._decode(action_vec)))
```

### 6.6 ONNX 导出与验证（通用）

`policy_exporter.py` 用 `torch.onnx.export`（opset 14，dynamic_axes 支持 batch），验证 ONNX 输出 == PyTorch（atol=1e-5）。逻辑与策略无关。

### 6.7 fallback 与安全降级（通用规则）

`RlRiskModel` 永不抛异常——任何 IPC 故障降级到固定 `fallbackAlpha`（默认 0.5）：

| 触发 | 行为 |
|---|---|
| ZeroMQ 连接失败 | log 警告，每次返回 fallback，不阻塞 |
| 请求超时（>200ms） | 该次 fallback；连续 10 次 → log 错误 |
| action JSON 解析失败 | fallback |
| α 为 NaN/Inf | fallback |
| live-paper 首日（无 policy） | 策略 `risk-mode=composite` 直到 policy 部署 |

### 6.8 离线增量训练调度（默认值，可按策略在 manifest 覆盖）

```yaml
ppo_training:
  retrain_frequency: weekly
  train_window_days: 504
  cpcv_folds: 6
  early_stop_patience: 20
  deploy_gate: manual        # 人工审核 DSR 后发布，首期不自动 CI/CD
  rollout_policy: greedy     # 部署时无探索噪声
```

## 7. 测试策略与验证门（通用化）

### 7.1 测试金字塔

```
端到端验证 (3)  ← 真实回测 + DSR 报告 + 基线对比（对每个接入策略各跑一遍）
集成测试 (6)    ← Lean 子进程 ↔ Python server（含 manifest 加载）
单元测试 (15+)  ← 各模块独立（框架逻辑），另加 manifest_lint 校验
```

### 7.2 单元测试清单（框架层，策略无关）

**C# 侧（`Tests/Algorithm/Models/RlRiskModelTests.cs`）**

| 测试 | 验证点 |
|---|---|
| `SerializeState_CapturesAllFields` | 状态 JSON 含 manifest 声明的全部字段 |
| `Request_ReturnsAction_WhenServerUp` | mock server 返回 α=0.6 → targets.Quantity × 0.6 |
| `Request_ReturnsFallback_WhenServerDown` | server 未启动 → fallback，不抛异常 |
| `Request_ReturnsFallback_OnTimeout` | mock server sleep 500ms，超时 200ms → fallback |
| `Request_ReturnsFallback_OnMalformedJson` | server 返回 garbage → fallback |
| `Alpha_ClampedToZeroOne` | α=2.0/-0.5/NaN → clamp [0,1] |
| `FallbackAlpha_NeverThrows` | 连续 10 次超时，第 11 次正常 |
| `ManageRisk_PreservesZeroTargets` | Quantity=0 不被 α 影响 |
| `ManageRisk_WorksAcrossStrategies` | 用两个不同策略的 mock `IRlStateExportable` 分别跑，行为一致 |

**Python 侧（`Tests/Python/`）**

| 测试 | 验证点 |
|---|---|
| `test_manifest_loader.py::test_valid_manifest_parses` | 合法 manifest 正确加载为对象 |
| `test_manifest_lint.py::test_param_mismatch_detected` | manifest 参数与代码不符时报错 |
| `test_manifest_lint.py::test_state_schema_mismatch_detected` | state 字段不符时报错 |
| `test_cpcv.py::test_no_overlap` | 训练/测试折无日期重叠 |
| `test_cpcv.py::test_embargo_applied` | test 折后 embargo_bars 不进 train |
| `test_reward.py::test_dsr_known_solution` | DSR 匹配 López de Prado 解析解 |
| `test_reward.py::test_dsr_monotonic_in_n_trials` | 固定 Sharpe，N 增 → DSR 降 |
| `test_reward.py::test_no_trades_returns_neg_inf` | 0 订单 → reward=-inf |
| `test_gym_env.py::test_reset_returns_initial_state` | reset 返回起点 |
| `test_gym_env.py::test_step_applies_alpha` | step(α=0.5) → pnl × 0.5 |
| `test_gym_env.py::test_shaping_terms_driven_by_manifest` | 更换 manifest 的 shaping 项，reward 组成随之变化 |
| `test_policy_exporter.py::test_onnx_matches_pytorch` | ONNX == PyTorch (atol=1e-5) |
| `test_inference_server.py::test_roundtrip` | 发状态收 action，α∈[0,1] |
| `test_inference_server.py::test_multi_strategy_endpoints_isolated` | 两策略各自 endpoint 互不串扰 |
| `test_lean_runner.py::test_shortest_backtest` | 1 个月回测，解析正确 |

### 7.3 集成测试

| 测试 | 验证点 |
|---|---|
| `test_lean_runner_integration` | 真实启动 Lean 子进程，返回 statistics |
| `test_risk_model_with_mock_server` | C# RlRiskModel + Python mock server 往返 |
| `test_layer_a_smoke[strategy=option_vol_arb_5layer]` | Optuna 5 trials，best_params + optuna.db 落盘 |
| `test_layer_a_smoke[strategy=var_strategy]` | 同上，验证框架对第二个策略同样生效（**关键：证明通用性**） |
| `test_layer_c_training_smoke` | 1 年 trace 训练 PPO 100 步，policy.pt + onnx 产出 |
| `test_inference_server_load` | 1000 请求压测，p99 < 50ms |
| `test_walk_forward_pipeline` | 完整 walk-forward，OOS DSR < IS DSR |

### 7.4 四道验证门（新增第 0 道：manifest 校验）

**门 0：manifest 一致性（新增，通用化专属）**

```bash
python manifest_lint.py --strategy option_vol_arb_5layer
python manifest_lint.py --strategy var_strategy
# 所有已接入策略 manifest 均通过，字段与代码一致
```

**门 1：编译**

```bash
dotnet build QuantConnect.Lean.sln
# 0 error，现有策略编译不受影响
```

**门 2：单元 + 集成测试**

```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "RlRiskModel"
pytest Tests/Python/test_manifest_loader.py test_manifest_lint.py test_cpcv.py test_reward.py test_gym_env.py test_policy_exporter.py
# 全绿
```

**门 3：回测回归（byte-for-byte 不变，对每个接入策略执行）**

| 策略 | 默认参数回测 | 验证 |
|---|---|---|
| 每个已接入策略 | `risk-mode=composite`（默认） | OrderListHash == 改动前 |
| 每个已接入策略 | `risk-mode=rl` + mock server | 全程 fallback，不崩溃，订单 > 0 |
| 未接入策略（不修改） | 默认参数 | OrderListHash == 改动前（不应被框架触及） |

**门 4：基线对比（统计显著性）**

对比两个基线：

- **基线 A**：优化前自己（同策略上一版本 best_params）—— 防止负优化
- **基线 B**：买入持有 / 等权基准 —— 防止框架本身跑不赢无脑基准

方法：White's Reality Check (WRC) 或 bootstrap p-value，不用数值比大小。

`baseline_test.py` 输出 `baseline_test_report.json`（按策略分文件）：

```json
{
  "strategy": "{strategy_name}",
  "vs_previous_version": {"p_value": 0.03, "significant": true, "metric": "DSR"},
  "vs_buy_and_hold": {"p_value": 0.08, "significant": false, "metric": "DSR"},
  "verdict": "PASS"
}
```

`verdict` 判定：两个 p_value 均 < 0.05 → PASS；任一不显著 → WARNING（人工审核）。

### 7.5 过拟合防控验证（双阈值判定，通用规则）

Layer A 优化完成后，`dsr_report.json`：

```json
{
  "strategy": "{strategy_name}",
  "n_trials": 200,
  "dsr_is": 0.92,
  "dsr_oos_cpcv_mean": 0.61,
  "dsr_oos_cpcv_std": 0.18,
  "overfitting_flag": "WARNING",
  "overfitting_reason": "OOS DSR / IS DSR = 0.66 ≥ 0.6 但绝对值 0.61 ≥ 0.5；衰减 33.7%",
  "ridge_converged": false
}
```

**双阈值判定（对所有策略统一，防止"作弊分数定合格线"）**：

- **绝对下限**：`OOS DSR ≥ 0.5`
- **衰减比例**：`OOS DSR / IS DSR ≥ 0.6`
- 两者缺一不可

判定：`PASS`（两者均满足）/ `WARNING`（衰减明显但可用，人工审核）/ `FAIL`（OOS DSR < 0.5 或衰减 > 60% → best_params 不可部署）。

### 7.6 Layer C 训练验证（通用）

| 指标 | 通过标准 |
|---|---|
| policy 收敛 | episode reward 后 20% 均值 > 前 20% 均值 |
| ONNX 一致性 | `test_onnx_matches_pytorch` 通过 |
| CPCV 多折稳定 | 各折 episode reward std/mean < 0.5 |
| 安全检查 | α 测试 trace 上始终 ∈ [0, 1] |
| 不退化 | α ≠ 0（非过度清仓 trivial 解） |

### 7.7 性能基准（框架层，通用）

| 场景 | 目标 |
|---|---|
| Layer A 单 trial（4 年回测） | < 5 min |
| Layer A 200 trials | < 24 h（并发 4） |
| Layer C 训练（2 年 trace，PPO 10k 步） | < 30 min |
| inference server 单次请求 | p99 < 10ms |
| `RlRiskModel.ManageRisk` 含 IPC | < 50ms |

### 7.8 测试数据准备

| 夹具 | 来源 | 是否 commit |
|---|---|---|
| `manifest_sample.yaml`（每种典型 manifest 各一份） | 手写，纯格式验证 | ✅ commit |
| `state_trace_schema_sample.jsonl` | 3-5 条，纯字段格式验证 | ✅ commit（稳定基线） |
| 完整 `state_trace.jsonl` | 每次训练动态生成 | ❌ CI artifact，不进 repo |
| `results_sample.json` | 真实回测脱敏 | ✅ commit |
| `policy_sample.onnx` | toy policy 导出 | ✅ commit |
| mock ZeroMQ server | 测试内联 | — |

## 8. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| `NetMQ` 新增 C# 依赖 | 备选：`System.Net.Sockets` 自写极简 TCP（牺牲重连/超时） |
| Lean `ReturnSkew`/`ReturnKurt` 可能不输出 | DSR 退化到正态假设（skew=0, kurt=3），log 警告 |
| `--parameters` JSON 注入需验证 | 集成测试 `test_lean_runner_integration` 首先验证此路径 |
| PPO 在金融数据上易过拟合 | CPCV 多折 + DSR + 早停 + 人工 deploy_gate |
| ZeroMQ 在 Windows live-paper 环境 | 首期仅 Linux，Windows 支持后续 |
| 状态空间维度可能不足 | 首期 ~15-20 维，后续可加市场状态（如波动率/资金流），在 manifest 中扩展即可，无需改框架代码 |
| **通用化新增风险**：manifest 与代码漂移 | `manifest_lint.py` 作为门 0 强制拦截，CI 每次提交必跑 |
| **通用化新增风险**：不同策略 endpoint/资源冲突 | endpoint 分配集中在 `config.yaml` 管理，策略间端口不重叠，启动时校验 |

## 9. 实现顺序建议

1. **Phase 0（新增）**：设计并落地 `strategy_manifest.yaml` schema + `manifest_loader.py` + `manifest_lint.py`，为 `OptionVolArb5LayerStrategy`、`VarStrategy` 各写一份 manifest 作为参照实现
2. **Phase 1**：`IOptimizableStrategy` / `IRlStateExportable` 接口 + `RlRiskModel.cs` + `RlRiskConfig.cs` + 单元测试（门 0-3）
3. **Phase 2**：`lean_runner.py`（改为读 manifest）+ `reward.py` + `cpcv.py` + `walk_forward.py` + `ridge_monitor.py` + Layer A smoke test（对两个参照策略各跑一次，验证框架通用性）
4. **Phase 3**：`lean_step_runner.py` + `gym_env.py`（读 manifest 驱动 shaping）+ `ppo_trainer.py` + `policy_exporter.py` + Layer C smoke test
5. **Phase 4**：`inference_server.py`（多策略 endpoint 隔离）+ `baseline_test.py` + 集成测试 + 第四道门
6. **Phase 5**：端到端验证（两个参照策略）+ 过拟合诊断报告 + 人工审核 gate
7. **Phase 6（通用化验收）**：接入第三个此前未涉及的策略，全程只新增 manifest + 两个接口实现，不改框架代码，作为"通用性"验收标准

## 10. 引用

- `docs/self-update.md` — Layer C IPC + 训练模式方案
- `docs/self-update2.md` — 第四道门 + 双阈值 + trace 折中
- `docs/LEANarch.md` — 五层架构定义
- `docs/superpowers/specs/2026-07-01-factor-zoo-design.md` — 三层管线 + 零侵入原则
- `docs/superpowers/specs/2026-07-03-var-strategy-design.md` — 三个立足要求
- López de Prado, *Advances in Financial Machine Learning*, Ch.7-8（CPCV, DSR）
- 原始版本：`2026-07-05-auto-optimize-quant-strategy-design.md`（策略绑定版，本文档为其通用化重构）
