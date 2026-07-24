"""ONNX 推理确定性验证 (auto-update3.md 第2点).

对比训练时 PPO policy 与导出 ONNX 在相同输入下的输出是否严格一致.
用真实 trace 的状态序列做 offline replay, 检测数值漂移.
"""
import sys, json, pathlib, argparse
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts" / "auto_optimize"))

import torch
from stable_baselines3 import PPO
import onnxruntime


def load_trace(trace_path):
    """从真实 trace 提取状态向量序列 (与 gym_env._obs 一致的 8 维)."""
    states = []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        states.append([
            s["tpv"], s["cash_pct"], s["var_1d99"], s["var_regime"],
            s["drawdown"], s["n_open_positions"], s.get("pnl", 0), 0
        ])
    return np.array(states, dtype=np.float32)


def compare_policy_vs_onnx(policy_pt_path, onnx_path, trace_path, atol=1e-4):
    """对比 PPO policy 网络 forward 与 ONNX forward 在相同输入下的输出.

    Returns: dict with max_abs_diff, mean_abs_diff, n_samples, deterministic (bool)
    """
    states = load_trace(trace_path)
    if len(states) == 0:
        return {"error": "empty trace", "deterministic": False}

    # PPO policy forward (deterministic, no sampling)
    model = PPO.load(policy_pt_path, device="cpu")
    policy_net = model.policy.mlp_extractor.policy_net
    with torch.no_grad():
        pt_out = policy_net(torch.tensor(states, dtype=torch.float32)).numpy()

    # ONNX forward
    sess = onnxruntime.InferenceSession(onnx_path)
    input_name = sess.get_inputs()[0].name
    ort_out = sess.run(None, {input_name: states})[0]

    # 形状对齐
    if pt_out.shape != ort_out.shape:
        pt_out = pt_out.reshape(ort_out.shape)

    diff = np.abs(pt_out - ort_out)
    max_abs = float(diff.max())
    mean_abs = float(diff.mean())
    deterministic = bool(max_abs < atol)

    return {
        "max_abs_diff": max_abs,
        "mean_abs_diff": mean_abs,
        "n_samples": len(states),
        "atol": atol,
        "deterministic": deterministic,
        "pt_shape": list(pt_out.shape),
        "ort_shape": list(ort_out.shape),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, help="path to policy.pt")
    ap.add_argument("--onnx", required=True, help="path to policy.onnx")
    ap.add_argument("--trace", required=True, help="path to state_trace.jsonl")
    ap.add_argument("--atol", type=float, default=1e-4)
    args = ap.parse_args()
    result = compare_policy_vs_onnx(args.policy, args.onnx, args.trace, args.atol)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("deterministic") else 1)
