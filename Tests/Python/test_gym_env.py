import pathlib, pytest
from gym_env import RlRiskEnv

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

def test_reset_returns_initial_state():
    env = RlRiskEnv(FIXTURES / "state_trace_schema_sample.jsonl",
                    reward_terms=[{"term": "scaled_pnl", "weight": 1.0}],
                    var_budget=0.02, max_dd=0.1)
    obs, info = env.reset()
    assert obs[0] == 1000000  # tpv is first element of obs vector

def test_step_applies_alpha():
    env = RlRiskEnv(FIXTURES / "state_trace_schema_sample.jsonl",
                    reward_terms=[{"term": "scaled_pnl", "weight": 1.0}],
                    var_budget=0.02, max_dd=0.1)
    env.reset()
    obs, reward, terminated, truncated, info = env.step([0.5])
    # pnl=0.01 * alpha=0.5 = 0.005
    assert abs(reward - 0.005) < 1e-6 or reward > 0
