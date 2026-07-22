"""d3rlpy 离线 RL 训练 (spec §1.3). CQL 默认, IQL 备选.

适配 d3rlpy >= 2.6 API:
- `d3rlpy.algos.{CQLConfig,IQLConfig}` (非 `d3rlpy.algorithms.*`)
- scalers 通过 Config 的 `observation_scaler`/`action_scaler`/`reward_scaler` 字段传入,
  `fit()` 不再接受 `scalers=` 关键字.
- `learning_rate` 拆为 `actor_learning_rate` / `critic_learning_rate` 等.
- `d3rlpy.algos.IQLConfig` 不接受 `alpha_learning_rate`/`temp_learning_rate`.
"""
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
    scaler: str = "standard"  # "standard" | "none"


def _build_scalers(cfg: OfflineRLConfig):
    """根据 cfg.scaler 构造 d3rlpy 的 observation/action/reward scaler 实例."""
    import d3rlpy
    if cfg.scaler == "standard":
        obs_scaler = d3rlpy.preprocessing.StandardObservationScaler()
        # action 用 MinMax 归一化到 [-1, 1] (d3rlpy 连续动作默认期望)
        act_scaler = d3rlpy.preprocessing.MinMaxActionScaler()
        rew_scaler = d3rlpy.preprocessing.StandardRewardScaler()
    else:
        obs_scaler = None
        act_scaler = None
        rew_scaler = None
    return obs_scaler, act_scaler, rew_scaler


def _build_algo(cfg: OfflineRLConfig):
    """根据 algorithm 选择 CQLConfig / IQLConfig 并注入 scalers + learning_rate."""
    import d3rlpy
    obs_scaler, act_scaler, rew_scaler = _build_scalers(cfg)
    common = dict(
        actor_learning_rate=cfg.learning_rate,
        critic_learning_rate=cfg.learning_rate,
        observation_scaler=obs_scaler,
        action_scaler=act_scaler,
        reward_scaler=rew_scaler,
    )
    if cfg.algorithm == "iol":
        algo = d3rlpy.algos.IQLConfig(**common)
    else:  # cql (默认)
        algo = d3rlpy.algos.CQLConfig(
            temp_learning_rate=cfg.learning_rate,
            alpha_learning_rate=cfg.learning_rate,
            **common,
        )
    return algo


def train_offline_rl(observations: np.ndarray, actions: np.ndarray, rewards: np.ndarray,
                     terminals: np.ndarray, cfg: OfflineRLConfig, output_path: str) -> dict:
    import d3rlpy
    d3rlpy.seed(cfg.seed)
    dataset = d3rlpy.dataset.MDPDataset(
        observations=observations, actions=actions,
        rewards=rewards, terminals=terminals)
    algo = _build_algo(cfg)
    # device: d3rlpy 接受 "cpu:0" / False / True; cfg.device == "cpu" → "cpu:0"
    device = "cpu:0" if cfg.device in ("cpu", "cpu:0") else cfg.device
    model = algo.create(device=device)
    # d3rlpy >=2.6: scalers 已在 Config 内, fit() 不再接受 scalers 关键字
    model.fit(dataset, n_steps=cfg.n_steps, show_progress=False, save_interval=0)
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
