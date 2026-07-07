"""trace.jsonl → d3rlpy MDPDataset 转换 (spec §1.3)."""
import json, pathlib
from dataclasses import dataclass
import numpy as np


@dataclass
class Transition:
    state: np.ndarray
    action: float
    reward: float
    next_state: np.ndarray
    done: bool


def _encode_state(s: dict, t: int) -> np.ndarray:
    return np.array([
        float(s.get("tpv", 0)), float(s.get("cash_pct", 0)),
        float(s.get("var_1d99", 0)), float(s.get("var_regime", 0)),
        float(s.get("drawdown", 0)), float(s.get("n_open_positions", 0)),
        float(s.get("pnl", 0)), float(t),
    ], dtype=np.float32)


def _compute_reward(s_next: dict, alpha: float, reward_terms: list, var_budget: float, max_dd: float) -> float:
    scaled_pnl = float(s_next.get("pnl", 0)) * alpha
    r = 0.0
    for term in reward_terms:
        if term["term"] == "scaled_pnl":
            r += term["weight"] * scaled_pnl
        elif term["term"] == "var_excess_penalty":
            r -= term["weight"] * max(0, float(s_next.get("var_1d99", 0)) - var_budget)
        elif term["term"] == "drawdown_excess_penalty":
            r -= term["weight"] * max(0, float(s_next.get("drawdown", 0)) - max_dd)
        elif term["term"] == "over_clearance_penalty":
            if alpha < 0.1: r -= term["weight"]
    return r


def trace_to_transitions(trace_path: str, reward_terms: list, var_budget: float, max_dd: float) -> list:
    states, alphas = [], []
    for line in pathlib.Path(trace_path).read_text().splitlines():
        if not line.strip(): continue
        s = json.loads(line)
        states.append(s)
        alphas.append(float(s.get("alpha", 1.0)))
    trans = []
    for i in range(len(states) - 1):
        s = _encode_state(states[i], i)
        s_next_state = _encode_state(states[i+1], i+1)
        a = alphas[i]
        r = _compute_reward(states[i+1], a, reward_terms, var_budget, max_dd)
        done = (i == len(states) - 2)
        trans.append(Transition(state=s, action=a, reward=r, next_state=s_next_state, done=done))
    return trans


def to_d3rlpy_dataset(transitions: list):
    import d3rlpy
    observations = np.array([t.state for t in transitions], dtype=np.float32)
    actions = np.array([[t.action] for t in transitions], dtype=np.float32)
    rewards = np.array([t.reward for t in transitions], dtype=np.float32)
    terminals = np.array([t.done for t in transitions], dtype=np.float32)
    return d3rlpy.dataset.MDPDataset(observations=observations, actions=actions,
                                     rewards=rewards, terminals=terminals)
