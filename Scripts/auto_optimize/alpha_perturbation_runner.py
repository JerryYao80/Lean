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
