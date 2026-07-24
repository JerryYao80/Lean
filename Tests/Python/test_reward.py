import math, pytest
from reward import compute_reward

def test_dsr_monotonic_in_n_trials():
    """固定 Sharpe, N 增 → DSR 降 (多重比较惩罚)"""
    stats = {"Sharpe Ratio": 1.5, "TradingDays": 1008}
    dsr_10 = compute_reward(stats, n_trials=10, baseline_sharpe=0)["dsr"]
    dsr_100 = compute_reward(stats, n_trials=100, baseline_sharpe=0)["dsr"]
    assert dsr_10 > dsr_100

def test_dsr_known_solution_n1():
    """N=1 → DSR 退化为 PSR (baseline=0)"""
    stats = {"Sharpe Ratio": 1.0, "TradingDays": 252}
    r = compute_reward(stats, n_trials=1, baseline_sharpe=0)
    assert 0 < r["dsr"] < 1
    # PSR(1.0, T=252) 应接近 0.9 左右
    assert r["dsr"] > 0.5

def test_no_trades_returns_neg_inf():
    stats = {"Sharpe Ratio": 0, "TradingDays": 100, "Total Orders": 0}
    r = compute_reward(stats, n_trials=10, baseline_sharpe=0)
    assert r["dsr"] == float("-inf")

def test_missing_fields_degrades_to_normal():
    """缺 ReturnSkew/Kurt → 退化正态假设, 不崩溃"""
    r = compute_reward({"Sharpe Ratio": 1.0, "TradingDays": 252}, 5, 0)
    assert "dsr" in r
