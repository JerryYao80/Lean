from math import isfinite
from numbers import Real

import pandas as pd


def _require_compatible_timezones(
    sessions: pd.DatetimeIndex, releases: pd.DatetimeIndex
) -> None:
    if sessions.tz is None or releases.tz is None:
        raise ValueError("sessions and releases must be timezone-aware")
    if str(sessions.tz) != str(releases.tz):
        raise ValueError("sessions and releases must use the same timezone")


def asof_available(
    sessions: pd.DatetimeIndex,
    releases: pd.DatetimeIndex,
    maximum_staleness: Real,
) -> pd.Series:
    """Return release availability at each session using backward-only as-of lookup.

    ``maximum_staleness`` is measured in exact elapsed 24-hour days and is
    inclusive. Input ordering and duplicate timestamps do not affect lookup;
    output ordering always matches ``sessions``.
    """
    if not isinstance(sessions, pd.DatetimeIndex) or not isinstance(
        releases, pd.DatetimeIndex
    ):
        raise TypeError("sessions and releases must be pandas DatetimeIndex values")
    if not isinstance(maximum_staleness, Real):
        raise TypeError("maximum_staleness must be a real number")
    if not isfinite(maximum_staleness) or maximum_staleness < 0:
        raise ValueError("maximum_staleness must be finite and non-negative")
    _require_compatible_timezones(sessions, releases)
    if sessions.hasnans:
        raise ValueError("sessions must not contain NaT")
    if releases.hasnans:
        raise ValueError("releases must not contain NaT")

    if sessions.empty or releases.empty:
        return pd.Series(False, index=sessions, dtype=bool)

    unique_releases = releases.drop_duplicates().sort_values()
    release_values = unique_releases.asi8
    session_values = sessions.asi8
    positions = release_values.searchsorted(session_values, side="right") - 1
    has_prior_release = positions >= 0

    ages = pd.Series(pd.NaT, index=range(len(sessions)), dtype="timedelta64[ns]")
    if has_prior_release.any():
        prior_positions = positions[has_prior_release]
        ages.loc[has_prior_release] = pd.to_timedelta(
            session_values[has_prior_release] - release_values[prior_positions], unit="ns"
        ).to_numpy()

    limit = pd.Timedelta(days=float(maximum_staleness))
    return pd.Series(has_prior_release & ages.le(limit).to_numpy(), index=sessions, dtype=bool)
