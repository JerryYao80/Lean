"""Parameter ridge monitoring. Spec §5.4 / §7.5."""
import numpy as np

def check_convergence(trial_history: list, threshold: float = 0.3) -> bool:
    """返回 True = 参数过收敛 (过拟合预警)."""
    if len(trial_history) < 10:
        return False
    keys = set()
    for t in trial_history:
        keys.update(t.keys())
    for k in keys:
        vals = [t[k] for t in trial_history if k in t]
        if len(vals) < 10:
            continue
        arr = np.array(vals, dtype=float)
        if arr.std() < 1e-9:
            return True
        # 收敛密度: 落在最窄 20% 分位区间内的比例 (hi==lo 时统计等于该点的值)
        lo, hi = np.percentile(arr, [40, 60])
        if hi >= lo:
            density = np.mean((arr >= lo) & (arr <= hi))
            if density > threshold:
                return True
    return False
