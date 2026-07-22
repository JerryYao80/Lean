from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from Scripts.gold2_closed_loop.phase0_types import (
    FeasibilityVerdict,
    WindowDefinition,
    proof_windows,
)


def test_phase0_types_define_required_verdicts_and_frozen_window():
    assert [verdict.value for verdict in FeasibilityVerdict] == [
        "PASS",
        "BLOCKED_INSUFFICIENT_TEST_WINDOWS",
        "BLOCKED_INSUFFICIENT_DATA",
    ]

    window = WindowDefinition(
        window_id="W0",
        train=(date(2018, 1, 1), date(2018, 12, 31)),
        review=(date(2019, 1, 1), date(2019, 12, 31)),
        blind=(date(2020, 1, 1), date(2020, 12, 31)),
    )
    with pytest.raises(FrozenInstanceError):
        window.window_id = "changed"


def test_proof_windows_are_fixed_and_have_unique_blind_ranges():
    windows = proof_windows()

    assert windows == (
        WindowDefinition(
            "W1",
            (date(2018, 1, 2), date(2020, 12, 31)),
            (date(2021, 1, 1), date(2021, 12, 31)),
            (date(2022, 1, 1), date(2022, 12, 31)),
        ),
        WindowDefinition(
            "W2",
            (date(2019, 1, 1), date(2021, 12, 31)),
            (date(2022, 1, 1), date(2022, 12, 31)),
            (date(2023, 1, 1), date(2023, 12, 31)),
        ),
        WindowDefinition(
            "W3",
            (date(2020, 1, 1), date(2022, 12, 31)),
            (date(2023, 1, 1), date(2023, 12, 31)),
            (date(2024, 1, 1), date(2024, 12, 31)),
        ),
        WindowDefinition(
            "W4",
            (date(2021, 1, 1), date(2023, 12, 31)),
            (date(2024, 1, 1), date(2024, 12, 31)),
            (date(2025, 1, 1), date(2025, 12, 31)),
        ),
    )
    assert [window.window_id for window in windows] == ["W1", "W2", "W3", "W4"]
    assert windows[0].train == (date(2018, 1, 2), date(2020, 12, 31))
    assert [window.blind[0].year for window in windows] == [2022, 2023, 2024, 2025]
    assert len({window.blind for window in windows}) == len(windows)
