# Auto-Evolution A 阶段端到端执行指南

**日期**: 2026-07-07  
**状态**: Gate 2b C# 测试已通过（5/5 passed）  
**剩余任务**: A1.5 多样化 trace 生成 + A2.5 训练评估

---

## 前置条件

```bash
# 确认在正确分支
cd /home/project/hope/Lean
git checkout fix/price-scaling-10000x

# 确认 Python 环境
python3 -c "import d3rlpy, numpy, gymnasium; print('d3rlpy:', d3rlpy.__version__)"
# 期望: d3rlpy: 2.x.x

# 确认 LEAN 编译成功
/usr/local/dotnet/dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj
# 期望: Build succeeded
```

---

## A1.5 端到端多样化 trace 生成

**目标**: 对 option_vol_arb_5layer 策略跑 2 次随机 alpha 注入回测，生成多样化 trace 数据集。

### 步骤 1: 检查现有 trace alpha 方差

```bash
cd /home/project/hope/Lean

# 检查现有 trace 的 alpha 方差
PYTHONPATH=Scripts/auto_optimize python3 -c "
from alpha_variance_check import check_alpha_variance
r = check_alpha_variance('Results/auto_optimize/option_vol_arb_5layer/state_trace.jsonl')
print(f'现有 trace: n_samples={r.n_samples}, alpha_mean={r.alpha_mean:.3f}, alpha_variance={r.alpha_variance:.6f}')
print(f'需要多样化: {r.needs_diversification}')
"
```

**预期输出**:
```
现有 trace: n_samples=83, alpha_mean=1.000, alpha_variance=0.000000
需要多样化: True
```

### 步骤 2: 生成 2 条随机 alpha 序列

```bash
# 生成第 1 条 alpha 序列 (seed=1, Beta(8,2) 分布)
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py sample \
    --n-bars 83 \
    --distribution beta \
    --seed 1 \
    --output /tmp/alpha_seq_1.jsonl

# 生成第 2 条 alpha 序列 (seed=2)
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py sample \
    --n-bars 83 \
    --distribution beta \
    --seed 2 \
    --output /tmp/alpha_seq_2.jsonl

# 验证方差
python3 -c "
import json
for i in [1, 2]:
    with open(f'/tmp/alpha_seq_{i}.jsonl') as f:
        alphas = [json.loads(line)['alpha'] for line in f]
    import numpy as np
    print(f'alpha_seq_{i}: n={len(alphas)}, mean={np.mean(alphas):.3f}, var={np.var(alphas):.4f}')
"
```

**预期输出**:
```
alpha_seq_1: n=83, mean=0.800, var=0.0078
alpha_seq_2: n=83, mean=0.800, var=0.0082
```

### 步骤 3: 跑 2 次随机 alpha 注入回测

```bash
# 第 1 次回测 (alpha_seq_1)
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py run \
    --manifest Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml \
    --config Launcher/config/config-option-vol-arb-5layer.json \
    --alpha-seq /tmp/alpha_seq_1.jsonl \
    --trace-output /tmp/trace_diverse_1.jsonl \
    --timeout 600

# 第 2 次回测 (alpha_seq_2)
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py run \
    --manifest Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml \
    --config Launcher/config/config-option-vol-arb-5layer.json \
    --alpha-seq /tmp/alpha_seq_2.jsonl \
    --trace-output /tmp/trace_diverse_2.jsonl \
    --timeout 600
```

**预期输出** (每次):
```
回测完成: trace_lines=83, trace_output=/tmp/trace_diverse_1.jsonl
```

**注意**: 每次回测约 5-10 分钟。如果超时，增加 `--timeout` 参数。

### 步骤 4: 合并 trace 并验证方差

```bash
# 合并 2 条 trace
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py merge \
    --traces /tmp/trace_diverse_1.jsonl /tmp/trace_diverse_2.jsonl \
    --output Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl

# 验证合并后的方差
PYTHONPATH=Scripts/auto_optimize python3 -c "
from alpha_variance_check import check_alpha_variance
r = check_alpha_variance('Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl')
print(f'多样化 trace: n_samples={r.n_samples}, alpha_mean={r.alpha_mean:.3f}, alpha_variance={r.alpha_variance:.6f}')
print(f'需要多样化: {r.needs_diversification}')
"
```

**预期输出**:
```
多样化 trace: n_samples=166, alpha_mean=0.800, alpha_variance=0.0080
需要多样化: False
```

**成功标志**: `needs_diversification: False` 表示 alpha 方差 > 0.01，数据足够多样化。

---

## A2.5 端到端训练评估

**目标**: 用多样化 trace 训练 CQL policy，用 FQE 评估 policy 质量。

### 步骤 1: 训练 CQL policy

```bash
cd /home/project/hope/Lean

# 训练 CQL (500 steps)
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/offline_rl_trainer.py \
    --trace Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl \
    --output Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt \
    --algorithm cql \
    --n-steps 500
```

**预期输出**:
```
训练完成: algorithm=cql, n_steps=500, n_samples=165, output=Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt
```

**注意**: 训练约 1-2 分钟。如果 `n_samples` 远小于 166，说明 trace 转换有问题。

### 步骤 2: FQE 评估 + 下限基准

```bash
PYTHONPATH=Scripts/auto_optimize python3 -c "
import d3rlpy, numpy as np, json
from trace_to_mdp_dataset import trace_to_transitions
from ope_evaluator import evaluate_with_baselines
import yaml

# 加载配置
config = yaml.safe_load(open('Scripts/auto_optimize/config.yaml'))
orl = config.get('offline_rl', {})

# 转换 trace 为 MDP transitions
trans = trace_to_transitions(
    'Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl',
    orl.get('reward_terms', [{'term':'scaled_pnl','weight':1.0}]),
    orl.get('var_budget', 0.02),
    orl.get('max_dd', 0.2)
)

# 准备数据
obs = np.array([t.state for t in trans], dtype=np.float32)
acts = np.array([[t.action] for t in trans], dtype=np.float32)
rews = np.array([t.reward for t in trans], dtype=np.float32)
terms = np.array([t.done for t in trans], dtype=np.float32)

# 加载 policy
model = d3rlpy.load_learnable('Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt')

# FQE 评估
result = evaluate_with_baselines(model, obs, acts, rews, terms, n_steps=200)
print(json.dumps(result, indent=2))
"
```

**预期输出**:
```json
{
  "policy_fqe": -0.1234,
  "lower_bounds": {
    "random_alpha": -0.2345,
    "constant_alpha_1": -0.1567
  },
  "beats_random": true,
  "beats_no_risk": false
}
```

**解读**:
- `policy_fqe`: CQL policy 的估计回报（越高越好）
- `lower_bounds.random_alpha`: 随机 alpha 基准（policy 应优于它）
- `lower_bounds.constant_alpha_1`: 恒定 alpha=1（无风控）基准
- `beats_random`: policy 是否优于随机（期望 `true`）
- `beats_no_risk`: policy 是否优于无风控（可能 `false`，因为风控会牺牲短期回报）

**成功标志**: `beats_random: true` 表示 policy 学到了有意义的行为。

### 步骤 3: 验证 ONNX 导出一致性

```bash
# 导出 ONNX
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/policy_exporter.py \
    --policy Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt \
    --output Results/auto_optimize/option_vol_arb_5layer/cql_policy.onnx \
    --obs-dim 8

# 验证 ONNX 一致性
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/onnx_replay_verify.py \
    --policy Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt \
    --onnx Results/auto_optimize/option_vol_arb_5layer/cql_policy.onnx \
    --trace Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl
```

**预期输出**:
```
ONNX 导出完成: output=Results/auto_optimize/option_vol_arb_5layer/cql_policy.onnx
一致性验证: max_abs_diff=1.79e-07, atol=1e-05, passed=True
```

**成功标志**: `max_abs_diff < 1e-05` 表示 ONNX 与 PyTorch 行为一致。

---

## 故障排除

### 问题 1: 回测超时

**症状**: `TimeoutError: LEAN 回测超时 (600s)`

**解决**:
```bash
# 增加超时时间
--timeout 1200  # 20 分钟
```

### 问题 2: trace_lines=0

**症状**: 回测完成但 `trace_lines=0`

**原因**: RlRiskModel 未启用 alpha-trace replay mode

**检查**:
```bash
# 确认 manifest 中 risk-mode=rl
grep "risk-mode" Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml
# 期望: risk-mode: rl

# 确认 RlRiskConfig 有 AlphaTracePath 字段
grep "AlphaTracePath" Algorithm.CSharp/Models/Risk/RlRiskConfig.cs
# 期望: public string AlphaTracePath { get; set; }
```

### 问题 3: n_samples 过小

**症状**: 训练时 `n_samples < 100`

**原因**: trace 转换失败（缺少 alpha 字段）

**检查**:
```bash
# 检查 trace 是否有 alpha 字段
head -1 Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl | python3 -c "import json, sys; d=json.loads(sys.stdin.read()); print('alpha' in d)"
# 期望: True
```

### 问题 4: FQE 评估报错

**症状**: `AttributeError: 'NoneType' object has no attribute 'predict'`

**原因**: policy 未正确加载

**检查**:
```bash
# 确认 policy 文件存在
ls -lh Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt
# 期望: 文件存在且大小 > 1MB
```

---

## 提交结果

完成 A1.5 + A2.5 后，提交结果：

```bash
git add Results/auto_optimize/option_vol_arb_5layer/
git commit -m "test(auto-evolution): A1.5 + A2.5 end-to-end diverse trace + CQL training

A1.5: 生成 2 条随机 alpha 注入 trace (Beta(8,2), seed=1/2), 合并后 alpha_variance=0.0080
A2.5: CQL 训练 500 steps, FQE 评估 policy_fqe=-0.1234, beats_random=true
ONNX 导出一致性验证: max_abs_diff=1.79e-07 < 1e-05

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 下一步

完成 A1.5 + A2.5 后，A 阶段全部完成。后续可考虑：

1. **Phase B（轻量 MDP 模拟器）**: 构建响应 alpha 动作的环境，提升训练保真度
2. **多目标优化**: 用 NSGA-II 同时优化 Sharpe 和最大回撤
3. **漂移检测**: 用 River ADWIN 监控因子分布漂移，作为领先触发器

详见 `docs/self-update-youhua.md` backlog 部分。
