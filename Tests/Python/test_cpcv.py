import pandas as pd, pytest
from cpcv import split

def test_no_overlap():
    days = pd.date_range("2024-01-01", periods=60, freq="D")
    splits = split(days, n_folds=6, purge_bars=2, embargo_bars=2)
    for train_idx, test_idx in splits:
        train_days = set(days[train_idx])
        test_days = set(days[test_idx])
        assert not (train_days & test_days)

def test_n_folds_coverage():
    days = pd.date_range("2024-01-01", periods=60, freq="D")
    splits = split(days, n_folds=6, purge_bars=2, embargo_bars=2)
    all_test = set()
    for _, test_idx in splits:
        all_test.update(days[test_idx])
    assert all_test == set(days)

def test_embargo_applied():
    days = pd.date_range("2024-01-01", periods=60, freq="D")
    splits = split(days, n_folds=6, purge_bars=0, embargo_bars=3)
    for train_idx, test_idx in splits:
        test_max = days[test_idx].max()
        for d in days[train_idx]:
            if d > test_max:
                assert (d - test_max).days > 3
