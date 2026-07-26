# Tests/Python/FactorZoo/test_alpha101_operators.py
"""Operator-level golden values on tiny deterministic inputs."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import operators as op  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows).astype(float)

def test_rank_cross_sectional():
    df = _df({"a": [10, 20], "b": [30, 5]})
    r = op.rank(df)
    # row0: [10,30] -> pct ranks [0.5, 1.0]; row1: [20,5] -> [1.0, 0.5]
    assert r.iloc[0]["a"] == pytest.approx(0.5)
    assert r.iloc[0]["b"] == pytest.approx(1.0)
    assert r.iloc[1]["a"] == pytest.approx(1.0)
    assert r.iloc[1]["b"] == pytest.approx(0.5)

def test_delay_delta():
    df = _df({"x": [1, 2, 4, 8]})
    assert list(op.delay(df, 1)["x"])[1:] == [1.0, 2.0, 4.0]
    assert list(op.delta(df, 1)["x"])[1:] == [1.0, 2.0, 4.0]

def test_correlation_covariance():
    x = _df({"a": [1, 2, 3, 4, 5, 6]})
    y = _df({"a": [2, 4, 6, 8, 10, 12]})
    assert op.correlation(x, y, 4).iloc[-1]["a"] == pytest.approx(1.0)
    assert op.covariance(x, y, 4).iloc[-1]["a"] > 0

def test_scale():
    df = _df({"a": [1, -1], "b": [2, 2]})
    s = op.scale(df, a=1)
    assert s.iloc[0]["a"] == pytest.approx(1/3)
    assert s.iloc[0]["b"] == pytest.approx(2/3)

def test_signedpower():
    df = _df({"a": [2, 3]})
    assert list(op.signedpower(df, 2)["a"]) == [4.0, 9.0]

def test_decay_linear():
    df = _df({"a": [1, 1, 1, 1]})
    assert op.decay_linear(df, 3).iloc[-1]["a"] == pytest.approx(1.0)

def test_ts_min_max_argmax_argmin():
    df = _df({"a": [3, 1, 2, 5, 4]})
    assert op.ts_max(df, 3).iloc[-1]["a"] == 5.0    # max of [2,5,4]=5
    assert op.ts_min(df, 3).iloc[-1]["a"] == 2.0    # min of [2,5,4]=2 (was wrongly 4.0)
    assert op.ts_argmax(df, 3).iloc[-1]["a"] == 1   # 5 at offset-from-end 1
    assert op.ts_argmin(df, 3).iloc[-1]["a"] == 2   # 2 at offset-from-end 2 (was wrongly 0)

def test_ts_rank():
    df = _df({"a": [1, 2, 3, 4, 5]})
    assert op.ts_rank(df, 3).iloc[-1]["a"] == pytest.approx(1.0)

def test_sum_product_stddev():
    df = _df({"a": [1, 2, 3, 4]})
    assert op.sum(df, 2).iloc[-1]["a"] == 7.0
    assert op.product(df, 2).iloc[-1]["a"] == 12.0
    assert op.stddev(df, 4).iloc[-1]["a"] == pytest.approx(np.std([1,2,3,4], ddof=1))

def test_indneutralize_simple_mean():
    df = _df({"a": [10], "b": [20], "c": [30]})
    groups = pd.Series({"a": "g1", "b": "g1", "c": "g2"})
    n = op.indneutralize(df, groups)
    assert n.iloc[0]["a"] == pytest.approx(-5)
    assert n.iloc[0]["b"] == pytest.approx(5)
    assert n.iloc[0]["c"] == pytest.approx(0)

def test_log_abs_sign():
    df = _df({"a": [np.e, -4, 0]})
    assert op.log(df)["a"].iloc[0] == pytest.approx(1.0)
    assert np.isnan(op.log(df)["a"].iloc[1])
    assert op.abs(df)["a"].iloc[1] == 4.0
    assert op.sign(df)["a"].iloc[1] == -1.0
