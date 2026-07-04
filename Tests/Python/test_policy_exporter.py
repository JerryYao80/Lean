import torch, numpy as np, pytest
from policy_exporter import export_and_verify

def test_onnx_matches_pytorch(tmp_path):
    # toy policy: 1-layer MLP, obs_dim=8 → act_dim=1 (sigmoid)
    class ToyPolicy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.fc = torch.nn.Linear(8, 1)
        def forward(self, x):
            return torch.sigmoid(self.fc(x))
    net = ToyPolicy()
    onnx_path = tmp_path / "policy.onnx"
    assert export_and_verify(net, obs_dim=8, onnx_path=str(onnx_path))
    assert onnx_path.exists()
