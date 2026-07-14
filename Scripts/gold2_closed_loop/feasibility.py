from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from numbers import Integral, Real

import pandas as pd

from Scripts.gold2_closed_loop.feature_availability import asof_available
from Scripts.gold2_closed_loop.phase0_types import (
    FeasibilityVerdict,
    WindowDefinition,
    proof_windows,
)


REQUIRED_SOURCES = ("518880", "AU", "VIX", "DFII10")
REQUIRED_WINDOW_IDS = ("W1", "W2", "W3", "W4")
CANONICAL_SESSION_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class SourcePolicy:
    maximum_staleness: Real
    maximum_consecutive_unavailable_sessions: int
    publication_lag: pd.Timedelta = pd.Timedelta(0)
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_staleness, Real)
            or not isfinite(self.maximum_staleness)
            or self.maximum_staleness < 0
        ):
            raise ValueError("maximum_staleness must be finite and non-negative")
        if (
            not isinstance(self.maximum_consecutive_unavailable_sessions, Integral)
            or self.maximum_consecutive_unavailable_sessions < 0
        ):
            raise ValueError(
                "maximum_consecutive_unavailable_sessions must be a non-negative integer"
            )
        if self.publication_lag < pd.Timedelta(0):
            raise ValueError("publication_lag must be non-negative")
        if not self.timezone:
            raise ValueError("timezone must be non-empty")


@dataclass(frozen=True)
class WindowEligibility:
    window_id: str
    eligible: bool
    reasons: tuple[str, ...]
    evaluated_range: tuple[date, date] | None = None


@dataclass(frozen=True)
class WindowGateDecision:
    verdict: FeasibilityVerdict
    eligible_blind_window_count: int
    windows: tuple[WindowEligibility, ...]


def _validate_source_keys(values: Mapping[str, object], label: str) -> None:
    if set(values) != set(REQUIRED_SOURCES):
        raise ValueError(f"{label} must define exactly {', '.join(REQUIRED_SOURCES)}")


def _longest_false_run(availability: pd.Series) -> int:
    longest = current = 0
    for available in availability:
        current = 0 if available else current + 1
        longest = max(longest, current)
    return longest


def _window_sessions(
    sessions: pd.DatetimeIndex, window: WindowDefinition
) -> pd.DatetimeIndex:
    start, end = window.train[0], window.blind[1]
    dates = sessions.date
    return sessions[(dates >= start) & (dates <= end)].drop_duplicates().sort_values()


def evaluate_fixed_windows(
    sessions: pd.DatetimeIndex,
    source_releases: Mapping[str, pd.DatetimeIndex],
    source_policies: Mapping[str, SourcePolicy],
) -> tuple[WindowEligibility, ...]:
    """Evaluate required source availability over fixed W1-W4 boundaries."""
    if not isinstance(sessions, pd.DatetimeIndex):
        raise TypeError("sessions must be a pandas DatetimeIndex")
    if sessions.tz is None:
        raise ValueError("sessions must be timezone-aware")
    if sessions.hasnans:
        raise ValueError("sessions must not contain NaT")
    canonical_sessions = sessions.tz_convert(CANONICAL_SESSION_TIMEZONE)
    _validate_source_keys(source_releases, "source_releases")
    _validate_source_keys(source_policies, "source_policies")

    results = []
    for window in proof_windows():
        fixed_range = (window.train[0], window.blind[1])
        relevant_sessions = _window_sessions(canonical_sessions, window)
        reasons = []
        if relevant_sessions.empty:
            reasons.append("NO_SESSIONS_IN_FIXED_WINDOW")
        else:
            for source in REQUIRED_SOURCES:
                releases = source_releases[source]
                if not isinstance(releases, pd.DatetimeIndex):
                    raise TypeError(f"release index for {source} must be a DatetimeIndex")
                if releases.empty:
                    reasons.append(f"MISSING_SOURCE:{source}")
                    continue
                policy = source_policies[source]
                try:
                    normalized_releases = releases.tz_convert(policy.timezone)
                    normalized_sessions = relevant_sessions.tz_convert(policy.timezone)
                except TypeError as error:
                    raise ValueError(f"release index for {source} must be timezone-aware") from error
                availability = asof_available(
                    normalized_sessions,
                    normalized_releases + policy.publication_lag,
                    policy.maximum_staleness,
                )
                gap = _longest_false_run(availability)
                allowed = policy.maximum_consecutive_unavailable_sessions
                if gap > allowed:
                    reasons.append(f"UNAVAILABLE_SESSION_GAP:{source}:{gap}>{allowed}")
        results.append(
            WindowEligibility(window.window_id, not reasons, tuple(reasons), fixed_range)
        )
    return tuple(results)


def decide_window_gate(
    windows: Sequence[WindowEligibility],
) -> WindowGateDecision:
    """PASS only when exactly W1-W4 are supplied and at least three are eligible."""
    by_id = {window.window_id: window for window in windows}
    if len(windows) != 4 or set(by_id) != set(REQUIRED_WINDOW_IDS):
        raise ValueError("window eligibility must contain exactly W1-W4")

    ordered = tuple(by_id[window_id] for window_id in REQUIRED_WINDOW_IDS)
    eligible_count = sum(window.eligible for window in ordered)
    verdict = (
        FeasibilityVerdict.PASS
        if eligible_count >= 3
        else FeasibilityVerdict.BLOCKED_INSUFFICIENT_TEST_WINDOWS
    )
    return WindowGateDecision(verdict, eligible_count, ordered)
