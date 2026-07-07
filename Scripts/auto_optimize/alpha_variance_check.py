"""Alpha 方差诊断 (spec §1.2). 检测 trace 的 alpha 分布, 方差≈0 → 触发多样化重跑.

通过条件为语义判断 (方差 >= threshold AND 非恒定), 不绑定历史 bug 快照数值.
needs_diversification=True 表示当前 trace alpha 多样性不足, 需重跑注入;
needs_diversification=False 表示 trace 已足够多样化, 可进入离线 RL 训练.
"""
import json, pathlib
from dataclasses import dataclass
import numpy as np


@dataclass
class AlphaVarianceReport:
    n_samples: int
    alpha_mean: float
    alpha_variance: float
    needs_diversification: bool
    threshold: float = 0.01


def check_alpha_variance(trace_path, threshold: float = 0.01) -> AlphaVarianceReport:
    alphas = []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        alphas.append(float(s.get("alpha", 1.0)))  # 无 alpha 字段 → 视为恒定 1.0
    arr = np.array(alphas, dtype=float)
    var = float(np.var(arr)) if len(arr) > 0 else 0.0
    mean = float(np.mean(arr)) if len(arr) > 0 else 0.0
    # 语义判断: 方差 < threshold → alpha 多样性不足, 需重跑注入 (非 bug 快照数值绑定)
    return AlphaVarianceReport(
        n_samples=len(arr),
        alpha_mean=mean,
        alpha_variance=var,
        needs_diversification=var < threshold,
        threshold=threshold,
    )
