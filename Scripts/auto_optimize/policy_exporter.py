"""PyTorch → ONNX exporter + verification. Spec §6.6."""
import torch, numpy as np, onnxruntime

def export_and_verify(policy_net, obs_dim: int, onnx_path: str) -> bool:
    dummy = torch.randn(1, obs_dim)
    torch.onnx.export(
        policy_net, dummy, onnx_path,
        input_names=["state"], output_names=["action"],
        dynamic_axes={"state": {0: "batch"}, "action": {0: "batch"}},
        opset_version=14,
    )
    pt_out = policy_net(dummy).detach().numpy()
    sess = onnxruntime.InferenceSession(onnx_path)
    ort_out = sess.run(None, {"state": dummy.numpy()})[0]
    return np.allclose(pt_out, ort_out, atol=1e-5)
