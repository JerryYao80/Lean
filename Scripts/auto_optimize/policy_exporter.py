"""PyTorch → ONNX exporter (auto-evolution A2.5).

适配 d3rlpy CQL/IQL 的 NormalPolicy. 关键: ONNX 必须内嵌 observation_scaler.transform
+ policy.forward(squashed_mu) + action_scaler.reverse_transform, 否则 raw obs 喂给学在
scaled 空间的网络 → tanh 饱和 → alpha=1.0 (部署退化).

d3rlpy predict 完整路径:
  raw_obs --observation_scaler.transform--> scaled_obs --policy.forward--> squashed_mu
          --action_scaler.reverse_transform--> action
squashed_mu ∈ [-1,1] (tanh output); reverse: action = (squashed_mu+1)/2*(max-min)+min.
"""
import argparse, json, pathlib
import numpy as np
import torch


def _get_scaler_params(scaler):
    """从 d3rlpy scaler 提取 mean/std 或 minimum/maximum (numpy)."""
    if scaler is None:
        return None
    if type(scaler).__name__ == "StandardObservationScaler":
        mean = scaler.mean
        std = scaler.std
        return {"kind": "standard",
                "mean": np.asarray(mean, dtype=np.float32),
                "std": np.asarray(std, dtype=np.float32),
                "eps": float(getattr(scaler, "eps", 1e-3))}
    if type(scaler).__name__ == "MinMaxActionScaler":
        return {"kind": "minmax",
                "minimum": np.asarray(scaler.minimum, dtype=np.float32),
                "maximum": np.asarray(scaler.maximum, dtype=np.float32)}
    return None


class _FullPolicyWrapper(torch.nn.Module):
    """完整推理路径: obs_scaler.transform → policy.forward → action_scaler.reverse.

    所有 scaler 参数固化为 buffer, 导出后 ONNX 自包含, 无需运行时再 fit scaler.
    """
    def __init__(self, policy_model):
        super().__init__()
        self._policy = policy_model._impl.policy
        obs_p = _get_scaler_params(policy_model.observation_scaler)
        act_p = _get_scaler_params(policy_model.action_scaler)
        if obs_p and obs_p["kind"] == "standard":
            self.register_buffer("obs_mean", torch.tensor(obs_p["mean"]))
            self.register_buffer("obs_std", torch.tensor(obs_p["std"]))
            self.obs_eps = obs_p["eps"]
            self._use_obs_scaler = True
        else:
            self._use_obs_scaler = False
        if act_p and act_p["kind"] == "minmax":
            self.register_buffer("act_min", torch.tensor(act_p["minimum"]))
            self.register_buffer("act_max", torch.tensor(act_p["maximum"]))
            self._use_act_scaler = True
        else:
            self._use_act_scaler = False

    def forward(self, x):
        if self._use_obs_scaler:
            x = (x - self.obs_mean) / (self.obs_std + self.obs_eps)
        out = self._policy(x)
        sm = out.squashed_mu  # ∈ [-1, 1]
        if self._use_act_scaler:
            # reverse min-max: action = (sm+1)/2 * (max-min) + min
            sm = (sm + 1.0) / 2.0 * (self.act_max - self.act_min) + self.act_min
        return sm


def export_policy_onnx(policy_model, obs_dim: int, onnx_path: str) -> dict:
    """导出 d3rlpy CQL/IQL policy 为 ONNX, 内嵌 obs/action scaler (部署自包含)."""
    wrapper = _FullPolicyWrapper(policy_model).eval()
    dummy = torch.randn(1, obs_dim)
    pathlib.Path(onnx_path).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper, dummy, onnx_path,
        input_names=["state"], output_names=["action"],
        dynamic_axes={"state": {0: "batch"}, "action": {0: "batch"}},
        opset_version=14,
    )
    return {"onnx_path": onnx_path, "obs_dim": obs_dim,
            "embeds_obs_scaler": wrapper._use_obs_scaler,
            "embeds_act_scaler": wrapper._use_act_scaler}


def export_and_verify(policy_net, obs_dim: int, onnx_path: str) -> bool:
    """旧 API 兼容 (test_policy_exporter 用): toy policy 无 scaler, 仅 forward 一致性."""
    dummy = torch.randn(1, obs_dim)
    pathlib.Path(onnx_path).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        policy_net, dummy, onnx_path,
        input_names=["state"], output_names=["action"],
        dynamic_axes={"state": {0: "batch"}, "action": {0: "batch"}},
        opset_version=14,
    )
    import onnxruntime
    pt_out = policy_net(dummy).detach().numpy()
    sess = onnxruntime.InferenceSession(onnx_path)
    ort_out = sess.run(None, {"state": dummy.numpy()})[0]
    return np.allclose(pt_out, ort_out, atol=1e-5)


if __name__ == "__main__":
    import d3rlpy
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--obs-dim", type=int, default=8)
    args = ap.parse_args()
    model = d3rlpy.load_learnable(args.policy)
    info = export_policy_onnx(model, args.obs_dim, args.output)
    print(json.dumps(info))
