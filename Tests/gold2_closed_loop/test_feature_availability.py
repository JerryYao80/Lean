from datetime import date

import pandas as pd
import pytest

from Scripts.gold2_closed_loop.feature_availability import asof_available


TZ = "Asia/Shanghai"


def index(*values: str, tz: str | None = TZ) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(values, tz=tz)


def test_after_close_release_is_not_visible_same_day_but_is_visible_next_session():
    sessions = index("2024-01-02 15:00", "2024-01-03 15:00")
    releases = index("2024-01-02 16:00")

    assert asof_available(sessions, releases, 5).tolist() == [False, True]


def test_prior_release_is_selected_backward_only():
    sessions = index("2024-01-03 15:00")
    releases = index("2024-01-04 09:00", "2024-01-02 14:00")

    assert asof_available(sessions, releases, 2).tolist() == [True]


def test_staleness_limit_is_inclusive_and_older_release_expires():
    sessions = index("2024-01-07 14:00", "2024-01-07 14:00:01")
    releases = index("2024-01-02 14:00")

    assert asof_available(sessions, releases, 5).tolist() == [True, False]


def test_unsorted_duplicate_inputs_are_deterministic_and_session_order_is_preserved():
    sessions = index("2024-01-04 15:00", "2024-01-02 15:00", "2024-01-03 15:00")
    releases = index("2024-01-03 16:00", "2024-01-01 15:00", "2024-01-01 15:00")

    assert asof_available(sessions, releases, 2).tolist() == [True, True, True]


@pytest.mark.parametrize("maximum_staleness", [-1, -0.1, float("nan"), float("inf")])
def test_invalid_staleness_is_rejected(maximum_staleness):
    with pytest.raises(ValueError, match="finite and non-negative"):
        asof_available(index("2024-01-02 15:00"), index("2024-01-01 15:00"), maximum_staleness)


def test_timezone_aware_consistent_indices_are_required():
    aware = index("2024-01-02 15:00")
    naive = index("2024-01-01 15:00", tz=None)
    utc = index("2024-01-01 07:00", tz="UTC")

    with pytest.raises(ValueError, match="timezone-aware"):
        asof_available(naive, aware, 1)
    with pytest.raises(ValueError, match="same timezone"):
        asof_available(aware, utc, 1)
