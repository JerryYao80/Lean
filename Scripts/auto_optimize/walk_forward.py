"""Walk-forward OOS windows. Spec §5.5."""
from datetime import date, timedelta

def split(start, end, train_days: int, test_days: int, step_days: int):
    windows = []
    cur = start
    while cur + timedelta(days=train_days + test_days) <= end:
        train_start = cur
        train_end = cur + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        windows.append(((train_start, train_end), (test_start, test_end)))
        cur = cur + timedelta(days=step_days)
    return windows
