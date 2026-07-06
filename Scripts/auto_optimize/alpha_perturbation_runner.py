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
