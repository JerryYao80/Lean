import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from Scripts.export_vol_regime_feature_data import (
    garman_klass_rv, rolling_realized_quarticity, signed_jump, regime_probability,
)

def _synthetic_ohlc(n=300, seed=42):
    rng = np.random.default_rng(seed)
    close = 4.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    dates = pd.bdate_range('2020-01-02', periods=n).strftime('%Y%m%d')
    return pd.DataFrame({'trade_date': dates, 'open': open_, 'high': high,
                         'low': low, 'close': close})

def test_garman_klass_positive():
    df = _synthetic_ohlc()
    rv = garman_klass_rv(df)
    assert len(rv) == len(df)
    assert (rv.dropna() > 0).all()

def test_rq_matches_definition():
    df = _synthetic_ohlc(100)
    rets = np.log(df['close']).diff()
    rq = rolling_realized_quarticity(rets, window=5)
    manual = rets.rolling(5).apply(lambda x: np.sum(x**4) / 5, raw=True)
    pd.testing.assert_series_equal(rq, manual, check_names=False)

def test_signed_jump_has_sign():
    df = _synthetic_ohlc(100)
    rets = np.log(df['close']).diff()
    sj = signed_jump(rets, vol_window=22)
    assert sj.notna().any()

def test_regime_probability_in_unit_interval():
    df = _synthetic_ohlc(300)
    log_rv = np.log(garman_klass_rv(df).clip(lower=1e-8))
    p = regime_probability(log_rv.dropna())
    assert p is not None
    assert ((p >= 0) & (p <= 1)).all()

if __name__ == '__main__':
    test_garman_klass_positive(); print('PASS gk')
    test_rq_matches_definition(); print('PASS rq')
    test_signed_jump_has_sign(); print('PASS sj')
    test_regime_probability_in_unit_interval(); print('PASS regime')
