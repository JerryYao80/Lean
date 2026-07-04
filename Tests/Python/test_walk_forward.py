from datetime import date
from walk_forward import split

def test_windows_non_overlapping_test():
    start, end = date(2020,1,1), date(2024,1,1)
    windows = split(start, end, train_days=365, test_days=90, step_days=90)
    assert len(windows) >= 8
    test_windows = [w[1] for w in windows]
    for i in range(len(test_windows)-1):
        assert test_windows[i][1] <= test_windows[i+1][0]
