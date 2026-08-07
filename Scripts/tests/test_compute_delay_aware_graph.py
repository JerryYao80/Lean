import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from Scripts.compute_delay_aware_graph import (
    lagged_correlation, lead_follow_strength, rolling_slope, build_features,
)

def _panel(n_stocks=10, n_days=80, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2022-01-03', periods=n_days).strftime('%Y%m%d')
    codes = [f'{600000+i:06d}.SH' for i in range(n_stocks)]
    rets = pd.DataFrame(rng.normal(0, 0.01, (n_days, n_stocks)),
                        index=dates, columns=codes)
    return rets

def test_lagged_correlation_symmetric_shape():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    assert C.shape == (10, 10, 5)

def test_lagged_correlation_in_range():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    assert np.nanmin(C) >= -1.01 and np.nanmax(C) <= 1.01

def test_lead_follow_strength_shapes():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    lead, follow = lead_follow_strength(C)
    assert len(lead) == 10 and len(follow) == 10

def test_rolling_slope_finite():
    s = rolling_slope(_panel().iloc[:, 0], window=5)
    assert s.notna().any()

def test_build_features_has_columns():
    df = build_features(_panel(n_days=120), max_lag=5, window=60)
    for col in ['trade_date', 'ts_code', 'lead_strength', 'follow_strength',
                'ret_5d', 'ret_10d', 'ret_20d', 'vol_ratio', 'slope_target']:
        assert col in df.columns, f'missing {col}'

if __name__ == '__main__':
    test_lagged_correlation_symmetric_shape(); print('PASS lagged_shape')
    test_lagged_correlation_in_range(); print('PASS lagged_range')
    test_lead_follow_strength_shapes(); print('PASS lead_follow')
    test_rolling_slope_finite(); print('PASS slope')
    test_build_features_has_columns(); print('PASS features')
