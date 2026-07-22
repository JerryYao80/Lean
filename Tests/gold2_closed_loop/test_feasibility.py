from datetime import timedelta

import pandas as pd
import pytest

from Scripts.gold2_closed_loop.feasibility import (
    SourcePolicy,
    WindowEligibility,
    decide_window_gate,
    evaluate_fixed_windows,
)
from Scripts.gold2_closed_loop.phase0_types import FeasibilityVerdict, proof_windows


TZ = "Asia/Shanghai"
SOURCES = ("518880", "AU", "VIX", "DFII10")


def annual_sessions() -> pd.DatetimeIndex:
    values = []
    for year in range(2018, 2026):
        values.extend((f"{year}-01-02 15:00", f"{year}-12-31 15:00"))
    return pd.DatetimeIndex(values, tz=TZ)


def releases_for_every_session(sessions: pd.DatetimeIndex) -> dict[str, pd.DatetimeIndex]:
    releases = sessions - timedelta(hours=1)
    return {source: releases for source in SOURCES}


def policies(maximum_gap: int = 0) -> dict[str, SourcePolicy]:
    return {
        source: SourcePolicy(maximum_staleness=0.1, maximum_consecutive_unavailable_sessions=maximum_gap)
        for source in SOURCES
    }


def eligibility(window_id: str, eligible: bool = True) -> WindowEligibility:
    return WindowEligibility(window_id, eligible, () if eligible else ("DATA_INVALID",))


def test_all_required_sources_make_all_fixed_windows_eligible():
    sessions = annual_sessions()

    result = evaluate_fixed_windows(sessions, releases_for_every_session(sessions), policies())

    assert [item.window_id for item in result] == ["W1", "W2", "W3", "W4"]
    assert all(item.eligible and item.reasons == () for item in result)


def test_missing_source_and_internal_gap_have_deterministic_reasons():
    sessions = annual_sessions()
    releases = releases_for_every_session(sessions)
    releases["AU"] = pd.DatetimeIndex([], tz=TZ)
    releases["VIX"] = releases["VIX"].delete(1)

    result = evaluate_fixed_windows(sessions, releases, policies())

    assert result[0].eligible is False
    assert result[0].reasons == (
        "MISSING_SOURCE:AU",
        "UNAVAILABLE_SESSION_GAP:VIX:1>0",
    )


def test_gap_equal_to_policy_is_allowed_and_larger_gap_invalidates_without_boundary_shift():
    sessions = annual_sessions()
    releases = releases_for_every_session(sessions)
    releases["VIX"] = releases["VIX"].delete([1, 10, 11])

    result = evaluate_fixed_windows(sessions, releases, policies(maximum_gap=1))

    assert result[0] == WindowEligibility(
        "W1", True, (), (proof_windows()[0].train[0], proof_windows()[0].blind[1])
    )
    assert result[1] == WindowEligibility(
        "W2",
        False,
        ("UNAVAILABLE_SESSION_GAP:VIX:2>1",),
        (proof_windows()[1].train[0], proof_windows()[1].blind[1]),
    )
    assert result[1].evaluated_range == proof_windows()[1].train[0:1] + proof_windows()[1].blind[1:2]


def test_fixed_boundary_with_no_injected_sessions_is_invalid_not_shifted():
    sessions = annual_sessions()
    sessions = sessions[~((sessions.year >= 2019) & (sessions.year <= 2023))]

    result = evaluate_fixed_windows(sessions, releases_for_every_session(sessions), policies())

    assert result[1] == WindowEligibility(
        "W2",
        False,
        ("NO_SESSIONS_IN_FIXED_WINDOW",),
        (proof_windows()[1].train[0], proof_windows()[1].blind[1]),
    )
    assert result[1].evaluated_range == (
        proof_windows()[1].train[0],
        proof_windows()[1].blind[1],
    )


def test_unsorted_duplicate_sessions_do_not_change_consecutive_gap_result():
    sessions = annual_sessions()
    releases = releases_for_every_session(sessions)
    releases["VIX"] = releases["VIX"].delete([10, 11])
    shuffled = sessions.append(pd.DatetimeIndex([sessions[10]])).sort_values(ascending=False)

    result = evaluate_fixed_windows(shuffled, releases, policies(maximum_gap=1))

    assert result[1].reasons == ("UNAVAILABLE_SESSION_GAP:VIX:2>1",)


def test_source_publication_lag_is_applied_before_availability():
    sessions = pd.DatetimeIndex(["2022-01-02 15:00"], tz=TZ)
    releases = {
        source: pd.DatetimeIndex(["2022-01-02 14:30"], tz=TZ) for source in SOURCES
    }
    delayed = policies()
    delayed["VIX"] = SourcePolicy(
        maximum_staleness=1,
        maximum_consecutive_unavailable_sessions=0,
        publication_lag=pd.Timedelta(hours=1),
        timezone=TZ,
    )

    result = evaluate_fixed_windows(sessions, releases, delayed)

    assert result[0].reasons == ("UNAVAILABLE_SESSION_GAP:VIX:1>0",)


def test_utc_equivalents_use_shanghai_dates_at_exact_w1_boundaries():
    shanghai_boundaries = pd.DatetimeIndex(
        ["2018-01-02 00:00", "2022-12-31 23:59:59"], tz=TZ
    )
    utc_sessions = shanghai_boundaries.tz_convert("UTC")
    releases = {
        source: pd.DatetimeIndex([shanghai_boundaries[-1] - timedelta(minutes=1)])
        for source in SOURCES
    }

    shanghai_result = evaluate_fixed_windows(
        shanghai_boundaries, releases, policies()
    )
    utc_result = evaluate_fixed_windows(utc_sessions, releases, policies())

    assert utc_result == shanghai_result
    assert utc_result[0] == WindowEligibility(
        "W1",
        False,
        ("UNAVAILABLE_SESSION_GAP:518880:1>0", "UNAVAILABLE_SESSION_GAP:AU:1>0", "UNAVAILABLE_SESSION_GAP:VIX:1>0", "UNAVAILABLE_SESSION_GAP:DFII10:1>0"),
        (proof_windows()[0].train[0], proof_windows()[0].blind[1]),
    )


def test_three_eligible_windows_pass_and_two_are_blocked():
    three = [eligibility("W1"), eligibility("W2"), eligibility("W3"), eligibility("W4", False)]
    two = [eligibility("W1"), eligibility("W2"), eligibility("W3", False), eligibility("W4", False)]

    passed = decide_window_gate(three)
    blocked = decide_window_gate(two)

    assert passed.verdict is FeasibilityVerdict.PASS
    assert passed.eligible_blind_window_count == 3
    assert blocked.verdict is FeasibilityVerdict.BLOCKED_INSUFFICIENT_TEST_WINDOWS
    assert blocked.eligible_blind_window_count == 2


@pytest.mark.parametrize(
    "items",
    [
        [eligibility("W1"), eligibility("W2"), eligibility("W3")],
        [eligibility("W1"), eligibility("W2"), eligibility("W3"), eligibility("W5")],
        [eligibility("W1"), eligibility("W2"), eligibility("W3"), eligibility("W3")],
    ],
)
def test_gate_rejects_malformed_window_keys(items):
    with pytest.raises(ValueError, match="exactly W1-W4"):
        decide_window_gate(items)
