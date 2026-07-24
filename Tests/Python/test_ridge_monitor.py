import pytest
from ridge_monitor import check_convergence

def test_convergence_detected():
    # 参数都聚集在 [0.19, 0.21] → 收敛
    trials = [{"var-budget": 0.19 + 0.01 * (i % 3)} for i in range(20)]
    assert check_convergence(trials, threshold=0.3)

def test_divergence_not_flagged():
    trials = [{"var-budget": 0.01 + 0.04 * (i / 20)} for i in range(20)]
    assert not check_convergence(trials, threshold=0.3)
