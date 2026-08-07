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
