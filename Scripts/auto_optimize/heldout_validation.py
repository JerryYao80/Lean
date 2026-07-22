"""Held-out 窗口验证: train/test 分割 + 真配对回测 + bootstrap 显著性.

按 docs/check-auto-update2.md 建议:
- train (前 14 月): 采 trace + 训 CQL policy
- test  (后 8 月): rl-vs-composite 配对回测, 验证 in-sample 训练 out-of-sample 见效

这是"证明策略变好"的最小验证集: held-out + bootstrap p-value.
"""
import json, pathlib, subprocess, os, time, sys, tempfile
import numpy as np
import yaml
from manifest_loader import load_manifest
from lean_runner import _inject_params_into_config, _resolve_dotnet, _REPO_ROOT, _LAUNCHER_DIR, parse_results
from alpha_perturbation_runner import (AlphaPerturbationConfig, sample_alpha_sequence,
                                       write_alpha_sequence, run_perturbed_backtest, merge_traces)
from offline_rl_trainer import train_offline_rl, OfflineRLConfig
from trace_to_mdp_dataset import trace_to_transitions
from baseline_test import bootstrap_pvalue
from policy_exporter import export_policy_onnx

MANIFEST = "Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml"
BASE_CFG = "Launcher/config/config-option-vol-arb-5layer.json"
ONNX = "Results/auto_optimize/option_vol_arb_5layer/cql_policy.onnx"
ENDPOINT = "tcp://127.0.0.1:5557"
STRATEGY = "OptionVolArb5LayerStrategy"

# 22 月窗口分 train/test (2024-07/08 为分割点)
TRAIN_START, TRAIN_END = "2023-06-05", "2024-07-31"
TEST_START, TEST_END = "2024-08-01", "2025-03-31"


def _to_float(val, default=0.0):
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    s = str(val).replace("%", "").replace(",", "").strip()
    try: return float(s)
    except ValueError: return default


def _run_backtest_with_window(params: dict, start: str, end: str, timeout: int = 1200,
                              trace_tag: str = "") -> dict:
    """跑一次回测, 覆盖窗口 + params + RL_TRACE_PATH. 返回 statistics + trace path."""
    cfg_path = pathlib.Path(BASE_CFG)
    if not cfg_path.is_absolute():
        cfg_path = _REPO_ROOT / cfg_path
    base = json.loads(cfg_path.read_text())
    base["algorithm-start-date"] = f"{start}T00:00:00Z"
    base["algorithm-end-date"] = f"{end}T23:59:59Z"
    existing = base.get("parameters", {}) or {}
    base["parameters"] = {**existing, **{k: str(v) for k, v in params.items()}}
    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="lean_cfg_")
    os.close(fd)
    pathlib.Path(tmp_path).write_text(json.dumps(base, indent=2, ensure_ascii=False))
    dotnet = _resolve_dotnet()
    cmd = [dotnet, "QuantConnect.Lean.Launcher.dll", "--config", tmp_path]
    tag = trace_tag or f"{start.replace('-','')}"
    trace_path = f"/tmp/heldout_trace_{tag}.jsonl"
    # 清旧 trace
    try: pathlib.Path(trace_path).unlink()
    except OSError: pass
    env = {**os.environ, "RL_TRACE_PATH": trace_path}
    try:
        subprocess.run(cmd, cwd=str(_LAUNCHER_DIR), timeout=timeout, check=True,
                       capture_output=True, text=True, env=env)
    except Exception as e:
        return {"_error": str(e)[:300], "_trace_path": trace_path}
    finally:
        try: pathlib.Path(tmp_path).unlink()
        except OSError: pass
    results_dir = _REPO_ROOT / "Results"
    cands = sorted(results_dir.glob(f"{STRATEGY}-summary.json"), key=lambda p: p.stat().st_mtime)
    stats = parse_results(cands[-1]) if cands else {}
    stats["_trace_path"] = trace_path
    return stats


def _run_inference_server_bg(onnx_path: str):
    cmd = ["python3", "inference_server.py", "--manifest", MANIFEST,
           "--onnx", onnx_path, "--endpoint", ENDPOINT]
    cwd = str(_REPO_ROOT / "Scripts" / "auto_optimize")
    env = {**os.environ, "PYTHONPATH": cwd}
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.0)
    return proc


def _extract_daily_returns(trace_path: str) -> np.ndarray:
    """从 trace tpv 序列提取 per-bar returns."""
    if not pathlib.Path(trace_path).exists():
        return np.array([])
    tpvs = []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if line.strip():
            tpvs.append(float(json.loads(line).get("tpv", 0)))
    tpvs = np.array(tpvs)
    if len(tpvs) < 2:
        return np.array([])
    return np.diff(tpvs) / tpvs[:-1]


def main():
    print("=" * 70)
    print("Held-out 窗口验证 (train/test 分割 + bootstrap)")
    print(f"train: {TRAIN_START} ~ {TRAIN_END} | test: {TEST_START} ~ {TEST_END}")
    print("=" * 70)

    # === Phase 1: train 窗口采 trace + 训 CQL ===
    print("\n[Phase 1] train 窗口: 采 2 条 alpha 注入 trace + 训 CQL")
    train_traces = []
    for seed in [1, 2]:
        seq = sample_alpha_sequence(AlphaPerturbationConfig(seed=seed, n_bars=300))
        seq_path = f"/tmp/heldout_alpha_{seed}.jsonl"
        write_alpha_sequence(seq, seq_path)
        # 用 _run_backtest_with_window 注入 risk-mode=rl + alpha-seq 到 train 窗口
        # trace 写到 RL_TRACE_PATH = /tmp/heldout_trace_train_{seed}.jsonl (由 _run_backtest_with_window 设)
        params = {"risk-mode": "rl", "rl-alpha-trace-path": seq_path, "rl-fallback-alpha": "1.0"}
        stats = _run_backtest_with_window(params, TRAIN_START, TRAIN_END, trace_tag=f"train_{seed}")
        out = stats.get("_trace_path", f"/tmp/heldout_trace_train_{seed}.jsonl")
        n_lines = sum(1 for _ in pathlib.Path(out).open()) if pathlib.Path(out).exists() else 0
        print(f"  seed={seed}: trace_lines={n_lines} ({out})")
        if n_lines > 0:
            train_traces.append(out)

    if not train_traces:
        print("  ⚠️ train trace 采集失败, 退出")
        return
    merged = "Results/auto_optimize/option_vol_arb_5layer/state_trace_train.jsonl"
    merge_traces(train_traces, merged)
    print(f"  合并 train trace: {merged}")

    config = yaml.safe_load(pathlib.Path("Scripts/auto_optimize/config.yaml").read_text())
    orl = config.get("offline_rl", {})
    trans = trace_to_transitions(merged, orl.get("reward_terms"), orl.get("var_budget", 0.5), orl.get("max_dd", 0.05))
    obs = np.array([t.state for t in trans], dtype=np.float32)
    acts = np.array([[t.action] for t in trans], dtype=np.float32)
    rews = np.array([t.reward for t in trans], dtype=np.float32)
    terms = np.array([t.done for t in trans], dtype=np.float32)
    policy_pt = "Results/auto_optimize/option_vol_arb_5layer/cql_policy_heldout.pt"
    train_offline_rl(obs, acts, rews, terms,
                     OfflineRLConfig(algorithm="cql", n_steps=500, device="cpu", seed=0),
                     policy_pt)
    print(f"  CQL 训练完成: n_samples={len(trans)}")

    import d3rlpy
    m = d3rlpy.load_learnable(policy_pt)
    onnx_heldout = "Results/auto_optimize/option_vol_arb_5layer/cql_policy_heldout.onnx"
    export_policy_onnx(m, 8, onnx_heldout)
    print(f"  ONNX 导出: {onnx_heldout}")

    # === Phase 2: test 窗口 rl-vs-composite 配对回测 ===
    print("\n[Phase 2] test 窗口: rl-vs-composite 配对回测")
    print("  [2a] composite 基线...")
    comp_stats = _run_backtest_with_window({"risk-mode": "composite"}, TEST_START, TEST_END, trace_tag="test_comp")
    comp_returns = _extract_daily_returns(comp_stats.get("_trace_path", ""))
    print(f"    composite: Sharpe={_to_float(comp_stats.get('Sharpe Ratio'))}, returns n={len(comp_returns)}")

    print("  [2b] rl (inference_server + held-out ONNX)...")
    server = _run_inference_server_bg(onnx_heldout)
    try:
        rl_stats = _run_backtest_with_window({
            "risk-mode": "rl", "rl-server-endpoint": ENDPOINT,
            "rl-fallback-alpha": "0.5", "rl-timeout-ms": "200",
        }, TEST_START, TEST_END, trace_tag="test_rl")
    finally:
        server.terminate()
        try: server.wait(timeout=5)
        except: server.kill()
    rl_returns = _extract_daily_returns(rl_stats.get("_trace_path", ""))
    print(f"    rl: Sharpe={_to_float(rl_stats.get('Sharpe Ratio'))}, returns n={len(rl_returns)}")

    # === Phase 3: bootstrap p-value ===
    print("\n[Phase 3] bootstrap 显著性检验")
    p_val = None
    sig = False
    if len(comp_returns) > 0 and len(rl_returns) > 0:
        n = min(len(comp_returns), len(rl_returns))
        comp_r = comp_returns[:n]
        rl_r = rl_returns[:n]
        p_val = bootstrap_pvalue(rl_r, comp_r, n_bootstrap=2000)
        print(f"  H0: rl 收益均值 <= composite | p-value = {p_val:.4f}")
        sig = p_val < 0.05
        print(f"  显著性 (p<0.05): {sig}")
    else:
        print("  ⚠️ returns 提取失败, 跳过 bootstrap")

    # === 汇总 ===
    print("\n" + "=" * 70)
    print("Held-out 验证结论 (test 窗口)")
    print("=" * 70)
    comp_sharpe = _to_float(comp_stats.get("Sharpe Ratio"))
    rl_sharpe = _to_float(rl_stats.get("Sharpe Ratio"))
    comp_dd = _to_float(comp_stats.get("Drawdown"))
    rl_dd = _to_float(rl_stats.get("Drawdown"))
    print(f"{'指标':<15} {'composite':<15} {'rl(held-out)':<15} {'差异':<10}")
    print("-" * 55)
    print(f"{'Sharpe':<15} {comp_sharpe:<15.4f} {rl_sharpe:<15.4f} {rl_sharpe-comp_sharpe:+.4f}")
    print(f"{'Drawdown(%)':<15} {comp_dd:<15.4f} {rl_dd:<15.4f} {rl_dd-comp_dd:+.4f}")
    print(f"{'bootstrap p':<15} {'—':<15} {p_val if p_val is not None else 'N/A'}")

    print("\n验收:")
    risk_ok = rl_dd < comp_dd
    print(f"  风控 (rl dd < composite dd): {'✅ PASS' if risk_ok else '❌ FAIL'}")
    if p_val is not None:
        print(f"  收益显著 (p<0.05 rl>composite): {'✅ PASS' if sig else '❌ FAIL (p='+f'{p_val:.4f}'+')'}")

    out = {"train_window": [TRAIN_START, TRAIN_END], "test_window": [TEST_START, TEST_END],
           "composite": {"Sharpe": comp_sharpe, "Drawdown": comp_dd, "n_returns": int(len(comp_returns))},
           "rl": {"Sharpe": rl_sharpe, "Drawdown": rl_dd, "n_returns": int(len(rl_returns))},
           "bootstrap_p": p_val, "significant": sig}
    out_path = _REPO_ROOT / "Results" / "auto_optimize" / "option_vol_arb_5layer" / "heldout_validation.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n结果已存: {out_path}")


if __name__ == "__main__":
    main()
