"""PyTorch → ONNX exporter (auto-evolution A2.5).

适配 d3rlpy CQL/IQL 的 NormalPolicy: forward 返回 ActionOutput(squashed_mu, mu, logstd),
ONNX 取 squashed_mu 作为确定动作输出. 与 onnx_replay_verify 配合做一致性验证.
"""
import argparse, json, pathlib
import numpy as np
import torch


class _PolicyWrapper(torch.nn.Module):
    """把 d3rlpy NormalPolicy 包成 forward → squashed_mu tensor."""
    def __init__(self, policy):
        super().__init__()
        self._policy = policy

    def forward(self, x):
        out = self._policy(x)
        return out.squashed_mu


def export_policy_onnx(policy_model, obs_dim: int, onnx_path: str) -> str:
    """导出 d3rlpy CQL/IQL policy 为 ONNX, 取 squashed_mu 作确定动作."""
    policy_net = policy_model._impl.policy
    wrapper = _PolicyWrapper(policy_net).eval()
    dummy = torch.randn(1, obs_dim)
    pathlib.Path(onnx_path).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper, dummy, onnx_path,
        input_names=["state"], output_names=["action"],
        dynamic_axes={"state": {0: "batch"}, "action": {0: "batch"}},
        opset_version=14,
    )
    return onnx_path


if __name__ == "__main__":
    import d3rlpy
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--obs-dim", type=int, default=8)
    args = ap.parse_args()
    model = d3rlpy.load_learnable(args.policy)
    out = export_policy_onnx(model, args.obs_dim, args.output)
    print(json.dumps({"exported": out, "obs_dim": args.obs_dim}))
