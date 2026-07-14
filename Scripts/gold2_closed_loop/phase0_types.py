from dataclasses import dataclass
from datetime import date
from enum import StrEnum


DateRange = tuple[date, date]


class FeasibilityVerdict(StrEnum):
    PASS = "PASS"
    BLOCKED_INSUFFICIENT_TEST_WINDOWS = "BLOCKED_INSUFFICIENT_TEST_WINDOWS"
    BLOCKED_INSUFFICIENT_DATA = "BLOCKED_INSUFFICIENT_DATA"


@dataclass(frozen=True)
class WindowDefinition:
    window_id: str
    train: DateRange
    review: DateRange
    blind: DateRange


def proof_windows() -> tuple[WindowDefinition, ...]:
    return (
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
