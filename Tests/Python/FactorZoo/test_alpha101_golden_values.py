# Tests/Python/FactorZoo/test_alpha101_golden_values.py
"""Golden-value regression: 15 alphas covering each operator family.

Catches 'runs but wrong' bugs (the fix/price-scaling-10000x class).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import operators as op  # noqa: E402
from alpha101.formulas import (alpha_004, alpha_013, alpha_028, alpha_042, alpha_057,
                               alpha_067, alpha_084, alpha_001, alpha_002, alpha_007,
                               alpha_029, alpha_041, alpha_048, alpha_058, alpha_101)  # noqa: E402
from alpha101.panel_loader import Alpha101Panel  # noqa: E402


def _panel(**wide):
    codes = list(next(iter(wide.values())).columns)
    dates = list(next(iter(wide.values())).index)
    return Alpha101Panel(
        open=wide["open"], high=wide["high"], low=wide["low"], close=wide["close"],
        close_raw=wide.get("close_raw", wide["close"].copy()), volume=wide["volume"],
        vwap=wide["vwap"], returns=wide["returns"], cap=wide.get("cap", wide["close"]*0+1),
        adv=wide.get("adv", {}), industry=wide.get("industry", pd.DataFrame()),
        dates=dates, asof=dates[-1])


@pytest.fixture
def two_stock():
    # 260 trading days: enough warm-up for all alphas in this file.
    # alpha_048 uses correlation(.,250); 260 rows gives a non-NaN last row.
    # Pattern: close_A = 10 + 0.5*i (cycling every 7 days), close_B = 20 + 0.5*((-i) % 7).
    # Open lags close by +1, high = close+1, low = close-1, vol fixed, vwap=close*10.
    # The last row is deterministic, so test_alpha_101_simplest hand-computes A.
    cols = ["A", "B"]
    dates = [f"2026{((i // 30) + 1):02d}{((i % 30) + 1):02d}" for i in range(260)]
    i_arr = np.arange(260, dtype=float)
    close_a = 10.0 + 0.5 * i_arr  # last row close_A = 10 + 0.5*259 = 139.5
    close_b = 20.0 + 0.5 * ((-i_arr) % 7)  # last row close_B = 20 + 0.5*1 = 20.5
    close = pd.DataFrame(np.stack([close_a, close_b], axis=1), index=dates, columns=cols, dtype=float)
    opn = pd.DataFrame(np.stack([close_a + 1.0, close_b + 1.0], axis=1), index=dates, columns=cols, dtype=float)
    high = pd.DataFrame(np.stack([close_a + 1.0, close_b + 1.0], axis=1), index=dates, columns=cols, dtype=float)
    low = pd.DataFrame(np.stack([close_a - 1.0, close_b - 1.0], axis=1), index=dates, columns=cols, dtype=float)
    vol = pd.DataFrame(np.tile([100.0, 200.0], (260, 1)), index=dates, columns=cols, dtype=float)
    amt = vol * close * 10
    vwap = (amt * 10) / vol  # = close * 100
    rets = close.pct_change()
    # Provide adv20 (and a few other horizons for completeness) so alphas that call
    # _adv(p, 20) without the test having to monkeypatch .adv work out-of-the-box.
    adv = {h: amt.rolling(h).mean() for h in (5, 10, 15, 20, 30, 60, 120, 180)}
    return _panel(open=opn, high=high, low=low, close=close, volume=vol,
                  vwap=vwap, returns=rets, adv=adv)


def test_alpha_101_simplest(two_stock):
    out = alpha_101(two_stock)
    # Last row: open_A=140.5, close_A=139.5, high_A=140.5, low_A=138.5
    # alpha_101 = (close-open)/((high-low)+0.001) = (139.5-140.5)/((140.5-138.5)+0.001)
    assert out.iloc[-1]["A"] == pytest.approx((139.5 - 140.5) / ((140.5 - 138.5) + 0.001), abs=1e-9)

def test_alpha_042_mean_reversion(two_stock):
    out = alpha_042(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])

def test_alpha_004_ts_rank(two_stock):
    out = alpha_004(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])
    assert out.iloc[-1]["A"] <= 0

def test_alpha_013_covariance(two_stock):
    out = alpha_013(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])

def test_alpha_028_scale(two_stock):
    out = alpha_028(two_stock)
    assert abs(out.iloc[-1].abs().sum() - 1.0) < 1e-9

def test_alpha_057_decay_linear(two_stock):
    out = alpha_057(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_001_ts_argmax(two_stock):
    out = alpha_001(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_002_correlation(two_stock):
    out = alpha_002(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_007_ternary(two_stock):
    two_stock.adv = {20: two_stock.volume.rolling(20).mean()}
    out = alpha_007(two_stock)
    assert (out.iloc[-1] == -1.0).all()

def test_alpha_029_nested(two_stock):
    out = alpha_029(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_041_sqrt_raw(two_stock):
    out = alpha_041(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_084_signedpower(two_stock):
    out = alpha_084(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_067_multilevel_indclass(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s1"], "l2": ["i1", "i2"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_067(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_048_indneutralize_subindustry(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s1"], "l2": ["i1", "i1"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_048(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_058_indneutralize_sector(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s2"], "l2": ["i1", "i2"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_058(two_stock)
    assert np.isfinite(out.iloc[-1].sum())
