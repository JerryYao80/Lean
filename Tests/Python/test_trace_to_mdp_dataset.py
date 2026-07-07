import json, pathlib, pytest, tempfile, os
from trace_to_mdp_dataset import trace_to_transitions, Transition

def test_trace_to_transitions_basic():
    trace = [
        {"tpv":1000,"cash_pct":0.5,"var_1d99":0.01,"var_regime":0.5,"drawdown":0.0,"n_open_positions":1,"pnl":0.01,"alpha":0.6},
        {"tpv":1010,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.6,"drawdown":0.0,"n_open_positions":1,"pnl":0.02,"alpha":0.7},
        {"tpv":1005,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.7,"drawdown":0.005,"n_open_positions":1,"pnl":-0.01,"alpha":0.5},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for s in trace: f.write(json.dumps(s)+"\n")
        path = f.name
    try:
        trans = trace_to_transitions(path, reward_terms=[{"term":"scaled_pnl","weight":1.0}], var_budget=0.015, max_dd=0.1)
        assert len(trans) == 2
        assert trans[0].action == pytest.approx(0.6)
        assert trans[1].action == pytest.approx(0.7)
        assert trans[0].reward == pytest.approx(0.02 * 0.6)
        assert trans[0].done is False
        assert trans[1].done is True
    finally:
        os.unlink(path)

def test_transition_state_dim():
    trace = [{"tpv":1000,"cash_pct":0.5,"var_1d99":0.01,"var_regime":0.5,"drawdown":0.0,"n_open_positions":1,"pnl":0.01,"alpha":0.6},
             {"tpv":1010,"cash_pct":0.5,"var_1d99":0.02,"var_regime":0.6,"drawdown":0.0,"n_open_positions":1,"pnl":0.02,"alpha":0.7}]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for s in trace: f.write(json.dumps(s)+"\n")
        path = f.name
    try:
        trans = trace_to_transitions(path, reward_terms=[{"term":"scaled_pnl","weight":1.0}], var_budget=0.015, max_dd=0.1)
        assert len(trans[0].state) == 8
        assert len(trans[0].next_state) == 8
    finally:
        os.unlink(path)
