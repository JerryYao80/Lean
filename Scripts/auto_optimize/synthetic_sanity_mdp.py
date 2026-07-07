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
        optimal = 0.2 if dd > 0.05 else 0.95
        acts[i, 0] = np.clip(optimal + rng.normal(0, 0.08), 0, 1)
    rews = np.zeros(n_samples, dtype=np.float32)
    for i in range(n_samples):
        dd = obs[i, 4]
        pnl = rng.standard_normal() * 0.01
        # alpha scales BOTH pnl AND drawdown exposure (position scaling semantics)
        # high dd + high alpha → large drawdown penalty; low alpha reduces it
        rews[i] = pnl * acts[i, 0] - 5.0 * max(0, dd * acts[i, 0] - 0.05)
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
