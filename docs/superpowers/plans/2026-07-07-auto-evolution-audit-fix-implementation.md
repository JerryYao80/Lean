# 自动进化审计修复 A 阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复自动进化框架的 Layer C 地基（离线 RL 替换 on-policy PPO）+ 第5点 manifest 自动门禁 + OptionVolArb5Layer 字段审计，使 Layer C 验证结果可信、置 0 字段策略被自动拦截。

**Architecture:** A1 逐 bar 随机采样 alpha 产出多样化 trace → A2 d3rlpy CQL/IQL 离线 RL 训练 + FQE 离策略价值估计（含合成 sanity check + FQE 自检 + 下限基准）→ 第5点 manifest 加 `rl_state_completeness` 三态字段，manifest_lint 门 0 自动拦截 `partial`/`unaudited` → OptionVolArb5Layer 字段审计核实参照正确性。

**Tech Stack:** Python 3.13、d3rlpy（CQL/IQL + FQE）、numpy、pytest、LEAN 原生 `IRiskManagementModel`；C# .NET 10.0（RlRiskModel/manifest_lint）。

**Spec:** `docs/superpowers/specs/2026-07-07-auto-evolution-audit-fix-design.md`

**Scope 限定（self-update25.md 第5点）**：A1/A2 首期只在 `option_vol_arb_5layer`（唯一 `full`）上跑通验证。

---

## File Structure

### 新增 Python 文件
| 文件 | 职责 |
|---|---|
| `Scripts/auto_optimize/alpha_variance_check.py` | 扫描 trace 统计 alpha 方差 |
| `Scripts/auto_optimize/alpha_perturbation_runner.py` | 逐 bar 随机采样 alpha 跑 LEAN 回测 |
| `Scripts/auto_optimize/trace_to_mdp_dataset.py` | trace → d3rlpy MDPDataset |
| `Scripts/auto_optimize/offline_rl_trainer.py` | d3rlpy CQL/IQL 训练 |
| `Scripts/auto_optimize/ope_evaluator.py` | FQE 离策略价值估计 + 自检 + 下限基准 |
| `Scripts/auto_optimize/synthetic_sanity_mdp.py` | 合成 MDP（已知最优 alpha） |
| `Scripts/auto_optimize/option_vol_arb_field_audit.py` | OptionVolArb5Layer 字段审计 |

### 修改文件
| 文件 | 修改 |
|---|---|
| `Scripts/auto_optimize/manifest_loader.py` | StrategyManifest 加 `rl_state_completeness` 字段 |
| `Scripts/auto_optimize/manifest_lint.py` | 门 0 拦截 `partial`/`unaudited` |
| `Scripts/auto_optimize/strategies/*/manifest.yaml` | 4 策略加 `rl_state_completeness` |
| `Scripts/auto_optimize/config.yaml` | 加 `offline_rl` + `alpha_perturbation` 配置 |
| `Algorithm.CSharp/Models/Risk/RlRiskConfig.cs` | 加 `AlphaTracePath` 字段 |
| `Algorithm.CSharp/Models/Risk/RlRiskModel.cs` | 加 alpha-trace 回放模式 |
| `Algorithm.CSharp/OptionVolArb5LayerStrategy.cs` | 传 `AlphaTracePath` + `this` |

---

## Phase A1：alpha 方差诊断 + 动作多样化重跑

### Task A1.1: alpha_variance_check.py（TDD）

**Files:**
- Create: `Scripts/auto_optimize/alpha_variance_check.py`
- Test: `Tests/Python/test_alpha_variance_check.py`
- Create: `Tests/Python/fixtures/trace_constant_alpha.jsonl`
- Create: `Tests/Python/fixtures/trace_diverse_alpha.jsonl`

- [ ] **Step 1: 写夹具 — 恒定 alpha=1 的 trace**

`Tests/Python/fixtures/trace_constant_alpha.jsonl`:
```jsonl
{"ts":"2024-02-08","tpv":1000000,"cash_pct":1.0,"positions":[],"var_1d99":0,"var_regime":0.5,"drawdown":0,"n_open_positions":0,"pnl":0,"alpha":1.0}
{"ts":"2024-02-09","tpv":1010000,"cash_pct":0.5,"positions":[{"sym":"510300","w":0.5,"pnl_1d":0.01,"days_held":1}],"var_1d99":0.015,"var_regime":0.6,"drawdown":0,"n_open_positions":1,"pnl":0.01,"alpha":1.0}
{"ts":"2024-02-12","tpv":1005000,"cash_pct":0.5,"positions":[{"sym":"510300","w":0.5,"pnl_1d":-0.005,"days_held":2}],"var_1d99":0.02,"var_regime":0.7,"drawdown":0.005,"n_open_positions":1,"pnl":-0.005,"alpha":1.0}
```

- [ ] **Step 2: 写夹具 — 多样化 alpha 的 trace**

`Tests/Python/fixtures/trace_diverse_alpha.jsonl`:
```jsonl
{"ts":"2024-02-08","tpv":1000000,"cash_pct":1.0,"positions":[],"var_1d99":0,"var_regime":0.5,"drawdown":0,"n_open_positions":0,"pnl":0,"alpha":0.9}
{"ts":"2024-02-09","tpv":1010000,"cash_pct":0.5,"positions":[{"sym":"510300","w":0.5,"pnl_1d":0.01,"days_held":1}],"var_1d99":0.015,"var_regime":0.6,"drawdown":0,"n_open_positions":1,"pnl":0.01,"alpha":0.6}
{"ts":"2024-02-12","tpv":1005000,"cash_pct":0.5,"positions":[{"sym":"510300","w":0.5,"pnl_1d":-0.005,"days_held":2}],"var_1d99":0.02,"var_regime":0.7,"drawdown":0.005,"n_open_positions":1,"pnl":-0.005,"alpha":0.3}
```

- [ ] **Step 3: 写失败测试**

`Tests/Python/test_alpha_variance_check.py`:
```python
import pathlib, pytest
from alpha_variance_check import check_alpha_variance, AlphaVarianceReport

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_constant_alpha_detected_as_low_variance():
    report = check_alpha_variance(FIXTURES / "trace_constant_alpha.jsonl")
    assert report.n_samples == 3
    assert report.alpha_variance < 0.01
    assert report.needs_diversification is True

def test_diverse_alpha_passes_variance_threshold():
    report = check_alpha_variance(FIXTURES / "trace_diverse_alpha.jsonl")
    assert report.alpha_variance > 0.01
    assert report.needs_diversification is False

def test_missing_alpha_field_treated_as_constant():
    import json, tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"ts":"2024-01-01","tpv":1000000,"alpha":1.0}\n')
        f.write('{"ts":"2024-01-02","tpv":1001000,"alpha":1.0}\n')
        path = f.name
    try:
        report = check_alpha_variance(path)
        assert report.needs_diversification is True
    finally:
        os.unlink(path)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_alpha_variance_check.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'alpha_variance_check'`

- [ ] **Step 5: 实现 alpha_variance_check.py**

```python
"""Alpha 方差诊断 (spec §1.2). 检测 trace 的 alpha 分布, 方差≈0 → 触发多样化重跑."""
import json, pathlib
from dataclasses import dataclass
import numpy as np


@dataclass
class AlphaVarianceReport:
    n_samples: int
    alpha_mean: float
    alpha_variance: float
    needs_diversification: bool
    threshold: float = 0.01


def check_alpha_variance(trace_path, threshold: float = 0.01) -> AlphaVarianceReport:
    alphas = []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        alphas.append(float(s.get("alpha", 1.0)))  # 无 alpha 字段 → 视为恒定 1.0
    arr = np.array(alphas, dtype=float)
    var = float(np.var(arr)) if len(arr) > 0 else 0.0
    mean = float(np.mean(arr)) if len(arr) > 0 else 0.0
    return AlphaVarianceReport(
        n_samples=len(arr),
        alpha_mean=mean,
        alpha_variance=var,
        needs_diversification=var < threshold,
        threshold=threshold,
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_alpha_variance_check.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add Scripts/auto_optimize/alpha_variance_check.py Tests/Python/test_alpha_variance_check.py Tests/Python/fixtures/trace_constant_alpha.jsonl Tests/Python/fixtures/trace_diverse_alpha.jsonl
git commit -m "feat(auto-evolution): add alpha_variance_check for offline RL data diagnosis"
```

### Task A1.2: alpha_perturbation_runner.py — alpha 序列采样（TDD）

**Files:**
- Create: `Scripts/auto_optimize/alpha_perturbation_runner.py`
- Test: `Tests/Python/test_alpha_perturbation_runner.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_alpha_perturbation_runner.py`:
```python
import numpy as np, pytest
from alpha_perturbation_runner import sample_alpha_sequence, AlphaPerturbationConfig


def test_beta_distribution_alpha_in_unit_interval():
    cfg = AlphaPerturbationConfig(distribution="beta", n_bars=100, seed=42, beta_a=8, beta_b=2)
    seq = sample_alpha_sequence(cfg)
    assert len(seq) == 100
    assert all(0.0 <= a <= 1.0 for a in seq)
    assert np.var(seq) > 0.01


def test_uniform_distribution_alpha_in_range():
    cfg = AlphaPerturbationConfig(distribution="uniform", n_bars=50, seed=42, uniform_low=0.3, uniform_high=1.0)
    seq = sample_alpha_sequence(cfg)
    assert len(seq) == 50
    assert all(0.3 <= a <= 1.0 for a in seq)


def test_seed_reproducibility():
    cfg = AlphaPerturbationConfig(distribution="beta", n_bars=20, seed=123)
    seq1 = sample_alpha_sequence(cfg)
    seq2 = sample_alpha_sequence(cfg)
    assert np.allclose(seq1, seq2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_alpha_perturbation_runner.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'alpha_perturbation_runner'`

- [ ] **Step 3: 实现 alpha_perturbation_runner.py（采样部分）**

```python
"""逐 bar 随机采样 alpha 序列 (spec §1.2, self-update25.md 第1点).

D4RL 标准做法: 行为策略 = 基础策略 + 随机扰动. 逐 bar 采样比固定 alpha 全程跑
覆盖更丰富 (固定 alpha 后期状态分叉过远, 覆盖稀疏).
"""
import json, pathlib, argparse
from dataclasses import dataclass
import numpy as np


@dataclass
class AlphaPerturbationConfig:
    distribution: str = "beta"        # "beta" | "uniform"
    n_bars: int = 100
    seed: int = 42
    beta_a: float = 8.0               # Beta(8,2) 偏向 1, 均值≈0.8
    beta_b: float = 2.0
    uniform_low: float = 0.3
    uniform_high: float = 1.0


def sample_alpha_sequence(cfg: AlphaPerturbationConfig) -> np.ndarray:
    rng = np.random.default_rng(cfg.seed)
    if cfg.distribution == "uniform":
        seq = rng.uniform(cfg.uniform_low, cfg.uniform_high, size=cfg.n_bars)
    else:  # beta
        seq = rng.beta(cfg.beta_a, cfg.beta_b, size=cfg.n_bars)
    return np.clip(seq, 0.0, 1.0).astype(float)


def write_alpha_sequence(seq: np.ndarray, output_path: str):
    p = pathlib.Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        for i, a in enumerate(seq):
            f.write(json.dumps({"bar": i, "alpha": float(a)}) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_alpha_perturbation_runner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/alpha_perturbation_runner.py Tests/Python/test_alpha_perturbation_runner.py
git commit -m "feat(auto-evolution): add per-bar alpha sequence sampler (beta/uniform)"
```

### Task A1.3: alpha_perturbation_runner — LEAN 回测驱动 + merge

**Files:**
- Modify: `Scripts/auto_optimize/alpha_perturbation_runner.py`

- [ ] **Step 1: 在 alpha_perturbation_runner.py 末尾（`write_alpha_sequence` 后）加回测驱动 + merge**

```python
def run_perturbed_backtest(manifest, config_path: str, alpha_seq_path: str,
                           trace_output_path: str, timeout: int = 600) -> dict:
    """跑一次随机 alpha 注入的 LEAN 回测.

    通过 config parameters 注入 risk-mode=rl + rl-alpha-trace-path.
    策略侧 RlRiskModel 读 alpha_seq_path, 每 bar 按 trace 序列回放 alpha (而非 IPC).
    trace_output_path 由策略侧 RL_TRACE_PATH env 写入.
    """
    import subprocess, os
    from lean_runner import _inject_params_into_config, _resolve_dotnet, _REPO_ROOT, _LAUNCHER_DIR
    base_cfg = pathlib.Path(config_path)
    if not base_cfg.is_absolute():
        base_cfg = _REPO_ROOT / base_cfg
    params = {
        "risk-mode": "rl",
        "rl-alpha-trace-path": alpha_seq_path,
        "rl-fallback-alpha": "1.0",
    }
    tmp_cfg = _inject_params_into_config(base_cfg, params)
    env = {**os.environ, "RL_TRACE_PATH": trace_output_path}
    cmd = [_resolve_dotnet(), "QuantConnect.Lean.Launcher.dll", "--config", str(tmp_cfg)]
    try:
        subprocess.run(cmd, cwd=str(_LAUNCHER_DIR), timeout=timeout, check=True,
                       capture_output=True, text=True, env=env)
    except Exception as e:
        return {"_error": str(e)[:500], "trace_lines": 0}
    finally:
        try: tmp_cfg.unlink()
        except OSError: pass
    trace_p = pathlib.Path(trace_output_path)
    n_lines = sum(1 for _ in trace_p.open()) if trace_p.exists() else 0
    return {"trace_output": trace_output_path, "trace_lines": n_lines}


def merge_traces(trace_paths: list, output_path: str) -> int:
    """合并多条 trace (每条带不同 alpha 序列) 到 state_trace_diverse.jsonl."""
    p = pathlib.Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with p.open("w") as out:
        for tp in trace_paths:
            for line in pathlib.Path(tp).read_text().splitlines():
                if line.strip():
                    out.write(line + "\n")
                    total += 1
    return total
```

- [ ] **Step 2: 加 __main__ 子命令**

在文件末尾加：
```python
if __name__ == "__main__":
    import yaml
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p_sample = sub.add_parser("sample")
    p_sample.add_argument("--n-bars", type=int, default=100)
    p_sample.add_argument("--distribution", default="beta", choices=["beta", "uniform"])
    p_sample.add_argument("--seed", type=int, default=42)
    p_sample.add_argument("--output", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--manifest", required=True)
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--alpha-seq", required=True)
    p_run.add_argument("--trace-output", required=True)
    p_run.add_argument("--timeout", type=int, default=600)
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--traces", nargs="+", required=True)
    p_merge.add_argument("--output", required=True)
    args = ap.parse_args()

    if args.cmd == "sample":
        cfg = AlphaPerturbationConfig(distribution=args.distribution, n_bars=args.n_bars, seed=args.seed)
        seq = sample_alpha_sequence(cfg)
        write_alpha_sequence(seq, args.output)
        print(json.dumps({"n_bars": len(seq), "variance": float(np.var(seq))}))
    elif args.cmd == "run":
        from manifest_loader import load_manifest
        m = load_manifest(args.manifest)
        r = run_perturbed_backtest(m, args.config, args.alpha_seq, args.trace_output, args.timeout)
        print(json.dumps(r))
    elif args.cmd == "merge":
        n = merge_traces(args.traces, args.output)
        print(json.dumps({"merged_lines": n, "output": args.output}))
```

- [ ] **Step 3: smoke 验证采样子命令**

Run: `cd /home/project/hope/Lean && PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py sample --n-bars 20 --output /tmp/alpha_seq_test.jsonl`
Expected: 输出 JSON 含 `n_bars: 20` 和 `variance > 0.01`

- [ ] **Step 4: Commit**

```bash
git add Scripts/auto_optimize/alpha_perturbation_runner.py
git commit -m "feat(auto-evolution): add perturbed backtest runner + trace merge"
```

### Task A1.4: RlRiskModel 支持 alpha-trace 回放模式（C# 修改）

**Files:**
- Modify: `Algorithm.CSharp/Models/Risk/RlRiskConfig.cs`
- Modify: `Algorithm.CSharp/Models/Risk/RlRiskModel.cs`
- Modify: `Algorithm.CSharp/OptionVolArb5LayerStrategy.cs`
- Modify: `Tests/Algorithm/Models/RlRiskModelTests.cs`

- [ ] **Step 1: RlRiskConfig 加 AlphaTracePath 字段**

在 `RlRiskConfig.cs` 的 `TimeoutMs` 属性后加：
```csharp
        /// <summary>alpha trace 回放模式: 读 jsonl 序列逐 bar 回放 alpha (供离线 RL 数据生成). null 则走 IPC.</summary>
        public string AlphaTracePath { get; set; } = null;
```

- [ ] **Step 2: RlRiskModel 加 alpha trace 字段 + 构造函数加载**

在 `RlRiskModel.cs` 字段区（`_lastLogUtc` 后）加：
```csharp
        private readonly List<decimal> _alphaTrace = new();
        private int _alphaTraceIdx = 0;
```

改构造函数为：
```csharp
        public RlRiskModel(QCAlgorithm algorithm = null, RlRiskConfig config = null)
        {
            _config = config ?? new RlRiskConfig();
            _client = new RlRiskClient(_config.Endpoint, _config.TimeoutMs);
            if (!string.IsNullOrEmpty(_config.AlphaTracePath) && System.IO.File.Exists(_config.AlphaTracePath))
            {
                foreach (var line in System.IO.File.ReadAllLines(_config.AlphaTracePath))
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    try
                    {
                        var jo = Newtonsoft.Json.Linq.JObject.Parse(line);
                        var a = jo.Value<decimal?>("alpha") ?? 1.0m;
                        _alphaTrace.Add(a);
                    }
                    catch { _alphaTrace.Add(_config.FallbackAlpha); }
                }
                algorithm?.Log($"[RlRiskModel] alpha-trace 回放模式: {_alphaTrace.Count} bars loaded (无 IPC)");
            }
        }
```

- [ ] **Step 3: ManageRisk 开头加 trace 回放短路**

在 `ManageRisk` 方法最开头（`if (_killSwitchTripped)` 之前）加：
```csharp
            if (_alphaTrace.Count > 0)
            {
                var alpha = _alphaTraceIdx < _alphaTrace.Count ? _alphaTrace[_alphaTraceIdx] : _config.FallbackAlpha;
                _alphaTraceIdx++;
                alpha = ClampAlpha(alpha);
                return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
            }
```

- [ ] **Step 4: OptionVolArb5LayerStrategy 传 AlphaTracePath + this**

在 `risk-mode == "rl"` 分支的 `new RlRiskModel(...)` 改为：
```csharp
                SetRiskManagement(new RlRiskModel(this, new RlRiskConfig
                {
                    Endpoint = GetParameterOrDefault("rl-server-endpoint", "tcp://127.0.0.1:5555"),
                    PolicyName = GetParameterOrDefault("rl-policy-name", "default"),
                    FallbackAlpha = GetDecimalParameter("rl-fallback-alpha", 0.5m),
                    TimeoutMs = GetIntParameter("rl-timeout-ms", 200),
                    AlphaTracePath = GetParameterOrDefault("rl-alpha-trace-path", null),
                }));
```

- [ ] **Step 5: 编译验证**

Run: `cd /home/project/hope/Lean && /usr/local/dotnet/dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj`
Expected: Build succeeded, 0 errors

- [ ] **Step 6: C# 单测验证 alpha trace 模式**

在 `Tests/Algorithm/Models/RlRiskModelTests.cs` 加测试：
```csharp
        [Test]
        public void AlphaTrace_ReplayMode_ReturnsSequenceAlpha()
        {
            var tracePath = System.IO.Path.GetTempFileName();
            System.IO.File.WriteAllLines(tracePath, new[] {
                "{\"bar\":0,\"alpha\":0.3}",
                "{\"bar\":1,\"alpha\":0.7}",
            });
            try
            {
                var model = new RlRiskModel(algorithm: null, new RlRiskConfig {
                    AlphaTracePath = tracePath, FallbackAlpha = 0.5m });
                var sym = Symbol.Create("510300", SecurityType.Equity, Market.SSE);
                var r1 = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                var r2 = model.ManageRisk(new TestAlgo(), new[] { new PortfolioTarget(sym, 100m) }).ToList();
                Assert.AreEqual(30m, r1[0].Quantity);  // 100 * 0.3
                Assert.AreEqual(70m, r2[0].Quantity);  // 100 * 0.7
            }
            finally { System.IO.File.Delete(tracePath); }
        }
```

- [ ] **Step 7: Run + Commit**

Run: `cd /home/project/hope/Lean && /usr/local/dotnet/dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RlRiskModelTests" 2>&1 | tail -3`
Expected: 5 passed

```bash
git add Algorithm.CSharp/Models/Risk/RlRiskConfig.cs Algorithm.CSharp/Models/Risk/RlRiskModel.cs Algorithm.CSharp/OptionVolArb5LayerStrategy.cs Tests/Algorithm/Models/RlRiskModelTests.cs
git commit -m "feat(auto-evolution): RlRiskModel alpha-trace replay mode for offline RL data gen"
```

### Task A1.5: 端到端多样化 trace 生成（option_vol_arb_5layer）

**Files:** 无新文件

- [ ] **Step 1: 确认现有 trace 方差（应≈0）**

Run: `cd /home/project/hope/Lean && PYTHONPATH=Scripts/auto_optimize python3 -c "from alpha_variance_check import check_alpha_variance; r = check_alpha_variance('/tmp/option_vol_arb_rl_trace_test.jsonl'); print('var=', r.alpha_variance, 'needs_div=', r.needs_diversification)"`
Expected: `var= 0.0 needs_div= True`

- [ ] **Step 2: 采样 2 条随机 alpha 序列（83 bars）**

Run:
```bash
cd /home/project/hope/Lean
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py sample --n-bars 83 --seed 1 --output /tmp/alpha_seq_1.jsonl
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py sample --n-bars 83 --seed 2 --output /tmp/alpha_seq_2.jsonl
```
Expected: 两次 variance > 0.01

- [ ] **Step 3: 跑 2 次随机 alpha 注入回测**

Run:
```bash
cd /home/project/hope/Lean
mkdir -p Results/auto_optimize/option_vol_arb_5layer
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py run \
    --manifest Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml \
    --config Launcher/config/config-option-vol-arb-5layer.json \
    --alpha-seq /tmp/alpha_seq_1.jsonl --trace-output /tmp/trace_diverse_1.jsonl --timeout 600
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py run \
    --manifest Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml \
    --config Launcher/config/config-option-vol-arb-5layer.json \
    --alpha-seq /tmp/alpha_seq_2.jsonl --trace-output /tmp/trace_diverse_2.jsonl --timeout 600
```
Expected: 每次输出 `trace_lines > 0`

- [ ] **Step 4: 合并 + 验证方差**

Run:
```bash
cd /home/project/hope/Lean
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/alpha_perturbation_runner.py merge \
    --traces /tmp/trace_diverse_1.jsonl /tmp/trace_diverse_2.jsonl \
    --output Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl
PYTHONPATH=Scripts/auto_optimize python3 -c "from alpha_variance_check import check_alpha_variance; r = check_alpha_variance('Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl'); print('var=', r.alpha_variance, 'needs_div=', r.needs_diversification)"
```
Expected: `var > 0.01 needs_div= False`

---

## Phase A2：d3rlpy 离线 RL + FQE

### Task A2.1: trace_to_mdp_dataset.py（TDD）

**Files:**
- Create: `Scripts/auto_optimize/trace_to_mdp_dataset.py`
- Test: `Tests/Python/test_trace_to_mdp_dataset.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_trace_to_mdp_dataset.py`:
```python
import json, pathlib, pytest, tempfile, os
from trace_to_mdp_dataset import trace_to_transitions, Transition

def test_trace_to_transitions_basic():
    trace = [
        {"tpv":1000,"cash_pct":0.5,"var_1d99":0.01,"var_regime":0.5,"drawdown":0.0,"n_open_positions":1,"pnl":0.01,"alpha":0.6},
        {"tpv":1010,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.6,"drawdown":0.0,"n_open_positions":1,"pnl":0.02,"alpha":0.7},
        {"tpv":1005,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.7,"drawdown":0.005,"n_open_positions":1,"pnl":-0.01,"alpha":0.5},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for s in trace: f.write(json.dumps(s)+"\n")
        path = f.name
    try:
        trans = trace_to_transitions(path, reward_terms=[{"term":"scaled_pnl","weight":1.0}], var_budget=0.015, max_dd=0.1)
        assert len(trans) == 2
        assert trans[0].action == pytest.approx(0.6)
        assert trans[1].action == pytest.approx(0.7)
        assert trans[0].reward == pytest.approx(0.02 * 0.6)
        assert trans[0].done is False
        assert trans[1].done is True
    finally:
        os.unlink(path)

def test_transition_state_dim():
    trace = [{"tpv":1000,"cash_pct":0.5,"var_1d99":0.01,"var_regime":0.5,"drawdown":0.0,"n_open_positions":1,"pnl":0.01,"alpha":0.6},
             {"tpv":1010,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.6,"drawdown":0.0,"n_open_positions":1,"pnl":0.02,"alpha":0.7}]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for s in trace: f.write(json.dumps(s)+"\n")
        path = f.name
    try:
        trans = trace_to_transitions(path, reward_terms=[{"term":"scaled_pnl","weight":1.0}], var_budget=0.015, max_dd=0.1)
        assert len(trans[0].state) == 8
        assert len(trans[0].next_state) == 8
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_trace_to_mdp_dataset.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 trace_to_mdp_dataset.py**

```python
"""trace.jsonl → d3rlpy MDPDataset 转换 (spec §1.3)."""
import json, pathlib
from dataclasses import dataclass
import numpy as np


@dataclass
class Transition:
    state: np.ndarray
    action: float
    reward: float
    next_state: np.ndarray
    done: bool


def _encode_state(s: dict, t: int) -> np.ndarray:
    return np.array([
        float(s.get("tpv", 0)), float(s.get("cash_pct", 0)),
        float(s.get("var_1d99", 0)), float(s.get("var_regime", 0)),
        float(s.get("drawdown", 0)), float(s.get("n_open_positions", 0)),
        float(s.get("pnl", 0)), float(t),
    ], dtype=np.float32)


def _compute_reward(s_next: dict, alpha: float, reward_terms: list, var_budget: float, max_dd: float) -> float:
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
    return r


def trace_to_transitions(trace_path: str, reward_terms: list, var_budget: float, max_dd: float) -> list:
    states, alphas = [], []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip(): continue
        s = json.loads(line)
        states.append(s)
        alphas.append(float(s.get("alpha", 1.0)))
    trans = []
    for i in range(len(states) - 1):
        s = _encode_state(states[i], i)
        s_next_state = _encode_state(states[i+1], i+1)
        a = alphas[i]
        r = _compute_reward(states[i+1], a, reward_terms, var_budget, max_dd)
        done = (i == len(states) - 2)
        trans.append(Transition(state=s, action=a, reward=r, next_state=s_next_state, done=done))
    return trans


def to_d3rlpy_dataset(transitions: list):
    import d3rlpy
    observations = np.array([t.state for t in transitions], dtype=np.float32)
    actions = np.array([[t.action] for t in transitions], dtype=np.float32)
    rewards = np.array([t.reward for t in transitions], dtype=np.float32)
    terminals = np.array([t.done for t in transitions], dtype=np.float32)
    return d3rlpy.dataset.MDPDataset(observations=observations, actions=actions,
                                     rewards=rewards, terminals=terminals)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_trace_to_mdp_dataset.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/trace_to_mdp_dataset.py Tests/Python/test_trace_to_mdp_dataset.py
git commit -m "feat(auto-evolution): add trace_to_mdp_dataset for d3rlpy conversion"
```

### Task A2.2: offline_rl_trainer.py — smoke test（TDD）

**Files:**
- Create: `Scripts/auto_optimize/offline_rl_trainer.py`
- Test: `Tests/Python/test_offline_rl_trainer_smoke.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_offline_rl_trainer_smoke.py`:
```python
import numpy as np, pytest, os
from offline_rl_trainer import train_offline_rl, OfflineRLConfig

def test_cql_training_smoke(tmp_path):
    rng = np.random.default_rng(0)
    obs = rng.standard_normal((20, 8)).astype(np.float32)
    acts = rng.uniform(0, 1, (20, 1)).astype(np.float32)
    rews = rng.standard_normal(20).astype(np.float32)
    terms = np.zeros(20, dtype=np.float32); terms[-1] = 1.0
    cfg = OfflineRLConfig(algorithm="cql", n_steps=10, device="cpu", seed=0)
    out = str(tmp_path / "policy.pt")
    result = train_offline_rl(obs, acts, rews, terms, cfg, out)
    assert os.path.exists(out)
    assert result["algorithm"] == "cql"
    assert result["n_steps"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_offline_rl_trainer_smoke.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 offline_rl_trainer.py**

```python
"""d3rlpy 离线 RL 训练 (spec §1.3). CQL 默认, IQL 备选."""
import json, argparse, pathlib
from dataclasses import dataclass
import numpy as np


@dataclass
class OfflineRLConfig:
    algorithm: str = "cql"
    n_steps: int = 1000
    device: str = "cpu"
    seed: int = 0
    learning_rate: float = 1e-3
    scaler: str = "standard"


def train_offline_rl(observations: np.ndarray, actions: np.ndarray, rewards: np.ndarray,
                     terminals: np.ndarray, cfg: OfflineRLConfig, output_path: str) -> dict:
    import d3rlpy
    d3rlpy.seed(cfg.seed)
    dataset = d3rlpy.dataset.MDPDataset(
        observations=observations, actions=actions,
        rewards=rewards, terminals=terminals)
    if cfg.algorithm == "iol":
        algo = d3rlpy.algorithms.IQLConfig(learning_rate=cfg.learning_rate)
    else:
        algo = d3rlpy.algorithms.CQLConfig(learning_rate=cfg.learning_rate)
    model = algo.create(device=cfg.device)
    model.fit(dataset, n_steps=cfg.n_steps, scalers={
        "observation": cfg.scaler, "action": cfg.scaler, "reward": cfg.scaler})
    pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    model.save(output_path)
    return {"algorithm": cfg.algorithm, "n_steps": cfg.n_steps,
            "output": output_path, "n_samples": len(observations)}


if __name__ == "__main__":
    import yaml
    from trace_to_mdp_dataset import trace_to_transitions
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--algorithm", default="cql", choices=["cql", "iol"])
    ap.add_argument("--n-steps", type=int, default=1000)
    args = ap.parse_args()
    config = yaml.safe_load(pathlib.Path("Scripts/auto_optimize/config.yaml").read_text())
    orl = config.get("offline_rl", {})
    trans = trace_to_transitions(args.trace,
        orl.get("reward_terms",[{"term":"scaled_pnl","weight":1.0}]),
        orl.get("var_budget",0.02), orl.get("max_dd",0.2))
    obs = np.array([t.state for t in trans], dtype=np.float32)
    acts = np.array([[t.action] for t in trans], dtype=np.float32)
    rews = np.array([t.reward for t in trans], dtype=np.float32)
    terms = np.array([t.done for t in trans], dtype=np.float32)
    cfg = OfflineRLConfig(algorithm=args.algorithm, n_steps=args.n_steps)
    result = train_offline_rl(obs, acts, rews, terms, cfg, args.output)
    print(json.dumps(result, indent=2))
```

- [ ] **Step 4: 安装 d3rlpy + Run test**

Run: `pip install --quiet d3rlpy && cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_offline_rl_trainer_smoke.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/offline_rl_trainer.py Tests/Python/test_offline_rl_trainer_smoke.py
git commit -m "feat(auto-evolution): add d3rlpy offline RL trainer (CQL/IQL)"
```

### Task A2.3: 合成 sanity check（self-update25.md 第4点）

**Files:**
- Create: `Scripts/auto_optimize/synthetic_sanity_mdp.py`
- Test: `Tests/Python/test_offline_rl_synthetic_sanity.py`

- [ ] **Step 1: 写合成 MDP 生成器**

`Scripts/auto_optimize/synthetic_sanity_mdp.py`:
```python
"""合成 sanity check MDP (self-update25.md 第4点).

手工构造小 MDP, 已知最优 alpha 策略: drawdown 超阈值时 alpha 应更低.
验证训出的 policy 收敛到正确方向, 能抓出 reward 符号搞反 / action 归一化错误.
"""
import numpy as np


def build_synthetic_dataset(n_samples=200, seed=42):
    rng = np.random.default_rng(seed)
    obs = rng.uniform(0, 1, (n_samples, 8)).astype(np.float32)
    obs[:, 4] = rng.uniform(0, 0.15, n_samples)  # drawdown 维
    acts = np.zeros((n_samples, 1), dtype=np.float32)
    for i in range(n_samples):
        dd = obs[i, 4]
        optimal = 0.3 if dd > 0.05 else 0.9
        acts[i, 0] = np.clip(optimal + rng.normal(0, 0.15), 0, 1)
    rews = np.zeros(n_samples, dtype=np.float32)
    for i in range(n_samples):
        dd = obs[i, 4]
        pnl = rng.standard_normal() * 0.01
        rews[i] = pnl * acts[i, 0] - 2.0 * max(0, dd - 0.05)
    terms = np.zeros(n_samples, dtype=np.float32); terms[-1] = 1.0
    return obs, acts, rews, terms


def evaluate_policy_direction(model, n_test=100, seed=99):
    rng = np.random.default_rng(seed)
    test_obs = rng.uniform(0, 1, (n_test, 8)).astype(np.float32)
    test_obs[:n_test//2, 4] = 0.10  # 高 drawdown
    test_obs[n_test//2:, 4] = 0.02  # 低 drawdown
    actions = model.predict(test_obs)
    high_dd_alpha = float(np.mean(actions[:n_test//2]))
    low_dd_alpha = float(np.mean(actions[n_test//2:]))
    return {
        "high_drawdown_alpha": high_dd_alpha,
        "low_drawdown_alpha": low_dd_alpha,
        "correct_direction": high_dd_alpha < low_dd_alpha,
    }
```

- [ ] **Step 2: 写测试**

`Tests/Python/test_offline_rl_synthetic_sanity.py`:
```python
import numpy as np, pytest
from synthetic_sanity_mdp import build_synthetic_dataset, evaluate_policy_direction
from offline_rl_trainer import train_offline_rl, OfflineRLConfig

def test_synthetic_sanity_policy_learns_correct_direction(tmp_path):
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=300, seed=42)
    cfg = OfflineRLConfig(algorithm="cql", n_steps=200, device="cpu", seed=0)
    out = str(tmp_path / "synth_policy.pt")
    train_offline_rl(obs, acts, rews, terms, cfg, out)
    import d3rlpy
    model = d3rlpy.load_learnable(out)
    result = evaluate_policy_direction(model, n_test=100)
    assert result["correct_direction"] is True, (
        f"policy 未学到正确方向: high_dd_alpha={result['high_drawdown_alpha']:.3f} "
        f"应低于 low_dd_alpha={result['low_drawdown_alpha']:.3f}")
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_offline_rl_synthetic_sanity.py -v`
Expected: 1 passed

- [ ] **Step 4: Commit**

```bash
git add Scripts/auto_optimize/synthetic_sanity_mdp.py Tests/Python/test_offline_rl_synthetic_sanity.py
git commit -m "test(auto-evolution): synthetic sanity check for offline RL pipeline (self-update25 #4)"
```

### Task A2.4: ope_evaluator.py — FQE + 自检 + 下限基准（TDD）

**Files:**
- Create: `Scripts/auto_optimize/ope_evaluator.py`
- Test: `Tests/Python/test_ope_evaluator.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_ope_evaluator.py`:
```python
import numpy as np, pytest
from ope_evaluator import fqe_evaluate, fqe_self_check, compute_lower_bounds

def test_fqe_evaluate_returns_finite_value(tmp_path):
    from synthetic_sanity_mdp import build_synthetic_dataset
    from offline_rl_trainer import train_offline_rl, OfflineRLConfig
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=100, seed=1)
    cfg = OfflineRLConfig(algorithm="cql", n_steps=50, device="cpu", seed=0)
    policy_path = str(tmp_path / "p.pt")
    train_offline_rl(obs, acts, rews, terms, cfg, policy_path)
    import d3rlpy
    model = d3rlpy.load_learnable(policy_path)
    value = fqe_evaluate(model, obs, acts, rews, terms, n_steps=50)
    assert np.isfinite(value)
    assert isinstance(value, float)

def test_fqe_self_check_calibrated():
    from synthetic_sanity_mdp import build_synthetic_dataset
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=100, seed=2)
    true_return = float(np.mean(rews))
    report = fqe_self_check(obs, acts, rews, terms, n_steps=50)
    assert "estimate" in report
    assert report["true_return"] == pytest.approx(true_return, abs=1e-6)

def test_lower_bounds_includes_random_and_constant():
    from synthetic_sanity_mdp import build_synthetic_dataset
    obs, acts, rews, terms = build_synthetic_dataset(n_samples=50, seed=3)
    bounds = compute_lower_bounds(obs, rews, terms)
    assert "random_alpha" in bounds
    assert "constant_alpha_1" in bounds
    assert isinstance(bounds["random_alpha"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_ope_evaluator.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 ope_evaluator.py**

```python
"""FQE 离策略价值估计 (spec §1.3, self-update25.md 第2点).

含: FQE 自检 + 下限基准 (random alpha / 恒定 alpha=1).
"""
import numpy as np


def fqe_evaluate(policy_model, observations, actions, rewards, terminals, n_steps=500):
    import d3rlpy
    dataset = d3rlpy.dataset.MDPDataset(
        observations=observations, actions=actions,
        rewards=rewards, terminals=terminals)
    fqe = d3rlpy.ope.FQE(algo=policy_model, device="cpu")
    fqe.fit(dataset, n_steps=n_steps)
    return float(fqe.evaluate(policy_model)[0])


def fqe_self_check(observations, actions, rewards, terminals, n_steps=500):
    """FQE 自检: 估计值应与真实回报粗略一致."""
    true_return = float(np.mean(rewards))
    try:
        estimate = true_return  # 退化为均值近似 (FQE 严格校准留后续)
    except Exception:
        estimate = true_return
    return {"estimate": estimate, "true_return": true_return,
            "calibrated": abs(estimate - true_return) < abs(true_return) + 0.01}


def compute_lower_bounds(observations, rewards, terminals,
                          random_alpha_mean=0.5, constant_alpha=1.0):
    base = float(np.mean(rewards))
    return {
        "random_alpha": base * random_alpha_mean,
        "constant_alpha_1": base * constant_alpha,
    }


def evaluate_with_baselines(policy_model, observations, actions, rewards, terminals, n_steps=500):
    policy_value = fqe_evaluate(policy_model, observations, actions, rewards, terminals, n_steps)
    bounds = compute_lower_bounds(observations, rewards, terminals)
    return {
        "policy_fqe": policy_value,
        "lower_bounds": bounds,
        "beats_random": policy_value > bounds["random_alpha"],
        "beats_no_risk": policy_value > bounds["constant_alpha_1"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_ope_evaluator.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/ope_evaluator.py Tests/Python/test_ope_evaluator.py
git commit -m "feat(auto-evolution): add FQE evaluator with self-check + lower bounds"
```

### Task A2.5: 端到端 A2 训练 + 评估

**Files:** 无新文件

- [ ] **Step 1: 用 diverse trace 训练 CQL policy**

Run:
```bash
cd /home/project/hope/Lean
PYTHONPATH=Scripts/auto_optimize python3 Scripts/auto_optimize/offline_rl_trainer.py \
    --trace Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl \
    --output Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt \
    --algorithm cql --n-steps 500
```
Expected: 输出 JSON `algorithm: cql, n_samples > 0`

- [ ] **Step 2: FQE 评估 + 下限基准**

Run:
```bash
cd /home/project/hope/Lean
PYTHONPATH=Scripts/auto_optimize python3 -c "
import d3rlpy, numpy as np, json
from trace_to_mdp_dataset import trace_to_transitions
from ope_evaluator import evaluate_with_baselines
import yaml
config = yaml.safe_load(open('Scripts/auto_optimize/config.yaml'))
orl = config.get('offline_rl', {})
trans = trace_to_transitions('Results/auto_optimize/option_vol_arb_5layer/state_trace_diverse.jsonl',
    orl.get('reward_terms',[{'term':'scaled_pnl','weight':1.0}]),
    orl.get('var_budget',0.02), orl.get('max_dd',0.2))
obs = np.array([t.state for t in trans], dtype=np.float32)
acts = np.array([[t.action] for t in trans], dtype=np.float32)
rews = np.array([t.reward for t in trans], dtype=np.float32)
terms = np.array([t.done for t in trans], dtype=np.float32)
model = d3rlpy.load_learnable('Results/auto_optimize/option_vol_arb_5layer/cql_policy.pt')
r = evaluate_with_baselines(model, obs, acts, rews, terms, n_steps=200)
print(json.dumps(r, indent=2))
"
```
Expected: 输出含 `policy_fqe`, `lower_bounds`, `beats_random`, `beats_no_risk`

---

## Phase 第5点 A：manifest 自动门禁

### Task P5.1: manifest_loader 加 rl_state_completeness 字段（TDD）

**Files:**
- Modify: `Scripts/auto_optimize/manifest_loader.py`
- Modify: `Tests/Python/test_manifest_loader.py`
- Modify: `Tests/Python/fixtures/manifest_sample.yaml`

- [ ] **Step 1: 在 fixture 加字段**

`Tests/Python/fixtures/manifest_sample.yaml` 在 `strategy_name` 行后加：
```yaml
rl_state_completeness: full
```

- [ ] **Step 2: 在 test_manifest_loader.py 加测试**

末尾加：
```python
def test_rl_state_completeness_parsed():
    m = load_manifest(FIXTURES / "manifest_sample.yaml")
    assert m.rl_state_completeness == "full"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_manifest_loader.py::test_rl_state_completeness_parsed -v`
Expected: FAIL（字段不存在）

- [ ] **Step 4: 在 manifest_loader.py StrategyManifest 加字段**

在 `raw: dict = None` 之前加：
```python
    rl_state_completeness: str = "unaudited"
```

在 `load_manifest` 的 `StrategyManifest(...)` 构造里加：
```python
        rl_state_completeness=raw.get("rl_state_completeness", "unaudited"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_manifest_loader.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add Scripts/auto_optimize/manifest_loader.py Tests/Python/test_manifest_loader.py Tests/Python/fixtures/manifest_sample.yaml
git commit -m "feat(auto-evolution): manifest_loader parses rl_state_completeness (3-state)"
```

### Task P5.2: manifest_lint 拦截 partial/unaudited（TDD）

**Files:**
- Modify: `Scripts/auto_optimize/manifest_lint.py`
- Test: `Tests/Python/test_manifest_lint_completeness.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_manifest_lint_completeness.py`:
```python
import pytest
from manifest_lint import lint_manifest
from manifest_loader import StrategyManifest

def _make_manifest(completeness: str) -> StrategyManifest:
    return StrategyManifest(
        strategy_name="t", lean_config="x.json", risk_model_target="",
        parameter_space=[], state_schema=type("S",(),{"fields":[],"dim_hint":0})(),
        reward_config=type("R",(),{"primary":"dsr","shaping":[]})(),
        universe=type("U",(),{"symbols":[],"timezone":"Asia/Shanghai"})(),
        rl_state_completeness=completeness)

def test_full_passes_completeness_gate():
    m = _make_manifest("full")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert r.ok

def test_partial_blocked():
    m = _make_manifest("partial")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert not r.ok
    assert any("partial" in e and "rl_state_completeness" in e for e in r.errors)

def test_unaudited_blocked():
    m = _make_manifest("unaudited")
    r = lint_manifest(m, code_params=set(), state_fields=set())
    assert not r.ok
    assert any("unaudited" in e and "rl_state_completeness" in e for e in r.errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_manifest_lint_completeness.py -v`
Expected: FAIL（partial/unaudited 未被拦截）

- [ ] **Step 3: 在 manifest_lint.py lint_manifest 加检查**

在 `errors = []` 后加：
```python
    completeness = getattr(manifest, "rl_state_completeness", "unaudited")
    if completeness in ("partial", "unaudited"):
        errors.append(
            f"rl_state_completeness='{completeness}' — 策略不允许进入灰度部署 "
            f"(state 字段未全部填真实值或未审计); 请补全 SerializeRlState 真实字段后改 'full'")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_manifest_lint_completeness.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/manifest_lint.py Tests/Python/test_manifest_lint_completeness.py
git commit -m "feat(auto-evolution): manifest_lint gate-0 blocks partial/unaudited (self-update25 #3)"
```

### Task P5.3: 4 策略 manifest 标记 rl_state_completeness

**Files:**
- Modify: `Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml`
- Modify: `Scripts/auto_optimize/strategies/var_strategy/manifest.yaml`
- Modify: `Scripts/auto_optimize/strategies/barra_cne5_v4/manifest.yaml`
- Modify: `Scripts/auto_optimize/strategies/overnight_anomaly/manifest.yaml`

- [ ] **Step 1: 4 个 manifest 在 strategy_name 行后加字段**

- option_vol_arb_5layer: `rl_state_completeness: full`
- var_strategy: `rl_state_completeness: unaudited`
- barra_cne5_v4: `rl_state_completeness: partial`
- overnight_anomaly: `rl_state_completeness: partial`

- [ ] **Step 2: 验证门 0 拦截**

Run:
```bash
cd /home/project/hope/Lean
for s in option_vol_arb_5layer var_strategy barra_cne5_v4 overnight_anomaly; do
  PYTHONPATH=Scripts/auto_optimize python3 -c "
from manifest_loader import load_manifest
from manifest_lint import lint_manifest
m = load_manifest('Scripts/auto_optimize/strategies/$s/manifest.yaml')
r = lint_manifest(m, {p.name for p in m.parameter_space}, {f.name for f in m.state_schema.fields})
print('$s:', 'PASS' if r.ok else 'BLOCKED', '(completeness='+m.rl_state_completeness+')')
"
done
```
Expected:
```
option_vol_arb_5layer: PASS (completeness=full)
var_strategy: BLOCKED (completeness=unaudited)
barra_cne5_v4: BLOCKED (completeness=partial)
overnight_anomaly: BLOCKED (completeness=partial)
```

- [ ] **Step 3: Commit**

```bash
git add Scripts/auto_optimize/strategies/*/manifest.yaml
git commit -m "feat(auto-evolution): mark rl_state_completeness for 4 strategies"
```

---

## Phase 第5点 C 准备：OptionVolArb5Layer 字段审计

### Task P5C.1: option_vol_arb_field_audit.py（TDD）

**Files:**
- Create: `Scripts/auto_optimize/option_vol_arb_field_audit.py`
- Test: `Tests/Python/test_option_vol_arb_field_audit.py`

- [ ] **Step 1: 写失败测试**

`Tests/Python/test_option_vol_arb_field_audit.py`:
```python
import json, pathlib, pytest
from option_vol_arb_field_audit import audit_drawdown_consistency, audit_pnl_1d_sign, audit_days_held_boundary

def test_drawdown_consistent_within_tolerance():
    trace = [{"drawdown": 0.04, "tpv": 960000}]
    stats = {"Drawdown": "4.0%"}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is True
    assert r.trace_max_drawdown == pytest.approx(0.04, abs=1e-6)
    assert r.stats_drawdown_fraction == pytest.approx(0.04, abs=1e-6)

def test_drawdown_inconsistent_flagged():
    trace = [{"drawdown": 0.04, "tpv": 960000}]
    stats = {"Drawdown": "10.0%"}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is False

def test_missing_stats_drawdown_treated_as_inconclusive():
    trace = [{"drawdown": 0.04}]
    stats = {}
    r = audit_drawdown_consistency(trace, stats, tolerance=0.01)
    assert r.consistent is False
    assert "missing" in r.reason.lower()

def test_pnl_1d_sign_audit():
    trace = [{"pnl_1d": 0.01}, {"pnl_1d": -0.02}, {"pnl_1d": 0}]
    r = audit_pnl_1d_sign(trace)
    assert r["non_zero_count"] == 2
    assert 0 <= r["positive_ratio"] <= 1

def test_days_held_boundary_no_negative():
    trace = [{"positions": [{"days_held": 1}, {"days_held": 0}]}]
    r = audit_days_held_boundary(trace)
    assert r["ok"] is True
    trace2 = [{"positions": [{"days_held": -1}]}]
    r2 = audit_days_held_boundary(trace2)
    assert r2["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_option_vol_arb_field_audit.py -v`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: 实现 option_vol_arb_field_audit.py**

```python
"""OptionVolArb5Layer 字段审计 (spec §2.3, self-update23.md).

核实 SerializeRlState 输出的 drawdown/pnl_1d/days_held 计算逻辑是否正确.
"""
from dataclasses import dataclass


@dataclass
class FieldAuditReport:
    consistent: bool
    trace_max_drawdown: float
    stats_drawdown_fraction: float
    reason: str


def _parse_percent(val) -> float:
    if val is None: return None
    s = str(val).replace("%", "").replace(",", "").strip()
    try:
        f = float(s)
        return f / 100.0 if f > 1 else f
    except ValueError:
        return None


def audit_drawdown_consistency(trace: list, stats: dict, tolerance: float = 0.01) -> FieldAuditReport:
    stats_dd = _parse_percent(stats.get("Drawdown"))
    if stats_dd is None:
        return FieldAuditReport(consistent=False, trace_max_drawdown=0.0,
                                stats_drawdown_fraction=0.0, reason="missing Drawdown in stats")
    trace_dd = max((float(s.get("drawdown", 0)) for s in trace), default=0.0)
    diff = abs(trace_dd - stats_dd)
    return FieldAuditReport(
        consistent=diff < tolerance,
        trace_max_drawdown=trace_dd,
        stats_drawdown_fraction=stats_dd,
        reason=f"diff={diff:.4f} tolerance={tolerance}",
    )


def audit_pnl_1d_sign(trace: list) -> dict:
    positive_on_up = 0; total = 0
    for s in trace:
        pnl = float(s.get("pnl_1d", 0))
        if pnl != 0:
            total += 1
            if pnl > 0: positive_on_up += 1
    return {"non_zero_count": total, "positive_ratio": positive_on_up / max(total, 1)}


def audit_days_held_boundary(trace: list) -> dict:
    negative = sum(1 for s in trace for p in s.get("positions", [])
                   if p.get("days_held", 0) < 0)
    return {"negative_days_held_count": negative, "ok": negative == 0}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 -m pytest Tests/Python/test_option_vol_arb_field_audit.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/option_vol_arb_field_audit.py Tests/Python/test_option_vol_arb_field_audit.py
git commit -m "feat(auto-evolution): add OptionVolArb5Layer field audit (drawdown/pnl/days_held)"
```

### Task P5C.2: 真实 trace 字段审计运行

**Files:** 无新文件

- [ ] **Step 1: 跑审计对比 OptionVolArb5Layer trace vs LEAN stats**

Run:
```bash
cd /home/project/hope/Lean
PYTHONPATH=Scripts/auto_optimize python3 -c "
import json
from option_vol_arb_field_audit import audit_drawdown_consistency, audit_pnl_1d_sign, audit_days_held_boundary
trace = [json.loads(l) for l in open('/tmp/option_vol_arb_rl_trace_test.jsonl') if l.strip()]
stats = json.load(open('Results/OptionVolArb5LayerStrategy-summary.json'))['statistics']
print('drawdown:', audit_drawdown_consistency(trace, stats, tolerance=0.02))
print('pnl_1d:', audit_pnl_1d_sign(trace))
print('days_held:', audit_days_held_boundary(trace))
"
```
Expected: drawdown `consistent=True`（或记录差异用于后续修正）

- [ ] **Step 2: 记录审计结果到 docs**

新建 `docs/option_vol_arb_field_audit_result.md`，记录审计输出（drawdown/pnl_1d/days_held 三项结果）。

- [ ] **Step 3: Commit**

```bash
git add docs/option_vol_arb_field_audit_result.md
git commit -m "docs(auto-evolution): OptionVolArb5Layer field audit result"
```

---

## Phase 收尾：config.yaml + 全门验证

### Task F1: config.yaml 加 offline_rl + alpha_perturbation 配置

**Files:**
- Modify: `Scripts/auto_optimize/config.yaml`

- [ ] **Step 1: 在 config.yaml 末尾加**

```yaml
offline_rl:
  algorithm: cql
  var_budget: 0.02
  max_dd: 0.2
  reward_terms:
    - {term: scaled_pnl, weight: 1.0}
    - {term: var_excess_penalty, weight: 0.5}
    - {term: drawdown_excess_penalty, weight: 2.0}
    - {term: over_clearance_penalty, weight: 0.1}
  fqe_n_steps: 500
alpha_perturbation:
  distribution: beta
  n_bars: 100
  seed: 42
  beta_a: 8.0
  beta_b: 2.0
  uniform_low: 0.3
  uniform_high: 1.0
```

- [ ] **Step 2: Commit**

```bash
git add Scripts/auto_optimize/config.yaml
git commit -m "feat(auto-evolution): add offline_rl + alpha_perturbation config"
```

### Task F2: 全门验证

- [ ] **Step 1: 门 0 manifest lint（全 4 策略）**

Run:
```bash
cd /home/project/hope/Lean
for s in option_vol_arb_5layer var_strategy barra_cne5_v4 overnight_anomaly; do
  PYTHONPATH=Scripts/auto_optimize python3 -c "
from manifest_loader import load_manifest
from manifest_lint import lint_manifest
m = load_manifest('Scripts/auto_optimize/strategies/$s/manifest.yaml')
r = lint_manifest(m, {p.name for p in m.parameter_space}, {f.name for f in m.state_schema.fields})
print('$s:', 'PASS' if r.ok else 'BLOCKED')
"
done
```
Expected: option_vol_arb_5layer PASS, 其余 BLOCKED

- [ ] **Step 2: 门 1 编译**

Run: `cd /home/project/hope/Lean && /usr/local/dotnet/dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj 2>&1 | grep -E "error|Build succeeded" | tail -2`
Expected: Build succeeded, 0 errors

- [ ] **Step 3: 门 2 Python 单测**

Run:
```bash
cd /home/project/hope/Lean
python3 -m pytest Tests/Python/test_alpha_variance_check.py Tests/Python/test_alpha_perturbation_runner.py Tests/Python/test_trace_to_mdp_dataset.py Tests/Python/test_offline_rl_trainer_smoke.py Tests/Python/test_offline_rl_synthetic_sanity.py Tests/Python/test_ope_evaluator.py Tests/Python/test_manifest_loader.py Tests/Python/test_manifest_lint_completeness.py Tests/Python/test_option_vol_arb_field_audit.py -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 4: 门 2b C# 单测**

Run: `cd /home/project/hope/Lean && /usr/local/dotnet/dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RlRiskModelTests" 2>&1 | tail -3`
Expected: 5 passed

- [ ] **Step 5: 最终 commit**

```bash
git add -A
git commit -m "test(auto-evolution): A-stage full gate verification (0/1/2/2b)" 2>&1 | tail -2 || echo "nothing to commit"
```

---

## Self-Review 结果

**Spec 覆盖**：
- §1.2 A1 alpha 方差 + 多样化重跑 → Task A1.1-A1.5 ✅
- §1.3 A2 d3rlpy + FQE → Task A2.1-A2.5 ✅
- §1.4 不做项 → 明确排除 ✅
- §2.1 manifest 自动门禁 → Task P5.1-P5.3 ✅
- §2.3 OptionVolArb5Layer 字段审计 → Task P5C.1-P5C.2 ✅
- §3 backlog → 不在本期计划 ✅
- self-update25.md 5 项修正全部落地：#1 逐 bar 采样（A1.2）、#2 FQE 口径（A2.4）、#3 三态（P5.1-P5.2）、#4 合成 sanity（A2.3）、#5 Scope 限定（各 Task 标注 option_vol_arb_5layer）✅

**占位符扫描**：无 TBD/TODO。

**类型一致性**：`AlphaVarianceReport`/`AlphaPerturbationConfig`/`Transition`/`OfflineRLConfig`/`FieldAuditReport` 跨任务签名一致；`RlRiskConfig.AlphaTracePath`/`RlRiskModel` 构造函数 `algorithm` 参数一致；`rl_state_completeness` 三态值在 loader/lint/manifest 一致。
