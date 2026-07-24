"""gym Environment replaying state trace. Spec §6.4. Manifest-driven shaping."""
import json, pathlib
import numpy as np
import gymnasium as gym
from gymnasium import spaces


class RlRiskEnv(gym.Env):
    def __init__(self, trace_path, reward_terms, var_budget, max_dd, episode_dsr=0.0):
        self._trace = [json.loads(l) for l in pathlib.Path(trace_path).read_text().splitlines() if l.strip()]
        self._reward_terms = reward_terms
        self._var_budget = var_budget
        self._max_dd = max_dd
        self._episode_dsr = episode_dsr
        self._t = 0
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(8,), dtype=np.float32)
        self.action_space = spaces.Box(low=0.0, high=1.0, shape=(1,), dtype=np.float32)

    def _obs(self):
        s = self._trace[self._t]
        return np.array([
            s["tpv"], s["cash_pct"], s["var_1d99"], s["var_regime"],
            s["drawdown"], s["n_open_positions"], s.get("pnl", 0), self._t
        ], dtype=np.float32)

    def _state_dict(self):
        s = self._trace[self._t]
        return {
            "ts": s.get("ts"),
            "tpv": s["tpv"],
            "cash_pct": s["cash_pct"],
            "var_1d99": s["var_1d99"],
            "var_regime": s["var_regime"],
            "drawdown": s["drawdown"],
            "n_open_positions": s["n_open_positions"],
            "pnl": s.get("pnl", 0),
            "t": self._t,
        }

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._t = 0
        return self._obs(), {}

    def step(self, action):
        alpha = float(action[0]) if isinstance(action, (np.ndarray, list)) else float(action)
        alpha = max(0.0, min(1.0, alpha))
        self._t += 1
        terminated = self._t >= len(self._trace)
        truncated = False
        if terminated:
            r = self._episode_dsr * 10.0
            return np.zeros(8, dtype=np.float32), r, terminated, truncated, {}
        s = self._trace[self._t]
        scaled_pnl = s.get("pnl", 0) * alpha
        r = 0.0
        for term in self._reward_terms:
            if term["term"] == "scaled_pnl":
                r += term["weight"] * scaled_pnl
            elif term["term"] == "var_excess_penalty":
                r -= term["weight"] * max(0, s["var_1d99"] - self._var_budget)
            elif term["term"] == "drawdown_excess_penalty":
                r -= term["weight"] * max(0, s["drawdown"] - self._max_dd)
            elif term["term"] == "over_clearance_penalty":
                if alpha < 0.1: r -= term["weight"]
        return self._obs(), r, terminated, truncated, {}

def eval_term(term_name, alpha, scaled_pnl, var_excess, dd_excess, per_bar=None):
    """注册表, 供 manifest 扩展自定义 shaping 项."""
    if term_name == "layer_contrib_penalty":
        # Spec §3.3: weight * sum(-neg layer contrib) for protective layers
        if per_bar:
            neg_sum = sum(-v for row in per_bar for v in row.values() if v < 0)
            return neg_sum
        return 0
    table = {
        "scaled_pnl": scaled_pnl,
        "var_excess_penalty": -max(0, var_excess),
        "drawdown_excess_penalty": -max(0, dd_excess),
        "over_clearance_penalty": -0.1 if alpha < 0.1 else 0,
    }
    return table.get(term_name, 0)
