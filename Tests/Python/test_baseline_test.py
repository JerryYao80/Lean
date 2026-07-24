import numpy as np, pytest
from baseline_test import bootstrap_pvalue

def test_significant_difference():
    np.random.seed(42)
    a = np.random.normal(0.01, 0.02, 252)  # 优化后
    b = np.random.normal(0.0, 0.02, 252)   # 基线
    p = bootstrap_pvalue(a, b, n_bootstrap=1000)
    assert p < 0.1  # 优化后显著优于基线

def test_no_significant_difference():
    np.random.seed(42)
    a = np.random.normal(0.01, 0.02, 252)
    b = a.copy() + np.random.normal(0, 0.001, 252)  # 几乎相同
    p = bootstrap_pvalue(a, b, n_bootstrap=1000)
    assert p > 0.1
