from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from Scripts.gold2_closed_loop.data_sources import SourceReport, inspect_source


OHLC = {
    "open": [10.0, 11.0, 12.0, 13.0],
    "high": [11.0, 12.0, 13.0, 14.0],
    "low": [9.0, 10.0, 11.0, 12.0],
    "close": [10.5, 11.5, 12.5, 13.5],
}


def _frame(dates):
    return pd.DataFrame({"trade_date": dates, **OHLC})


def test_inspect_parquet_returns_frozen_complete_report(tmp_path):
    path = tmp_path / "518880.parquet"
    _frame(["20231229", "2024-01-02", "2024-01-02T15:00:00", "2025-01-03"]).to_parquet(path)

    report = inspect_source("518880", path, "trade_date")

    assert report == SourceReport(
        logical_name="518880",
        path=str(path.resolve()),
        first_date="2023-12-29",
        last_date="2025-01-03",
        row_count=4,
        distinct_date_count=3,
        duplicate_date_count=1,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        annual_counts={2023: 1, 2024: 2, 2025: 1},
    )
    with pytest.raises(FrozenInstanceError):
        report.row_count = 0


def test_inspect_csv_counts_duplicates_and_years_deterministically(tmp_path):
    path = tmp_path / "prices.csv"
    pd.DataFrame(
        {"date": ["2024-12-31", "20230103", "2023-01-03T09:30:00", "2023-06-01"]}
    ).to_csv(path, index=False)

    report = inspect_source("benchmark", path, "date")

    assert report.first_date == "2023-01-03"
    assert report.last_date == "2024-12-31"
    assert report.row_count == 4
    assert report.distinct_date_count == 3
    assert report.duplicate_date_count == 1
    assert report.annual_counts == {2023: 3, 2024: 1}


@pytest.mark.parametrize(
    ("filename", "writer", "match"),
    [
        ("missing.csv", None, "exist"),
        ("empty.csv", lambda path: pd.DataFrame(columns=["date"]).to_csv(path, index=False), "empty"),
        ("no_date.csv", lambda path: pd.DataFrame({"value": [1]}).to_csv(path, index=False), "date"),
        ("bad_date.csv", lambda path: pd.DataFrame({"date": ["not-a-date"]}).to_csv(path, index=False), "date"),
        ("null_date.csv", lambda path: pd.DataFrame({"date": ["2024-01-02", None]}).to_csv(path, index=False), "date"),
        ("prices.json", lambda path: path.write_text("[]"), "suffix"),
    ],
)
def test_inspect_source_rejects_invalid_inputs(tmp_path, filename, writer, match):
    path = tmp_path / filename
    if writer is not None:
        writer(path)

    with pytest.raises(ValueError, match=match):
        inspect_source("benchmark", path, "date")


def test_fund_nav_cannot_substitute_for_a_tradable_source(tmp_path):
    path = tmp_path / "fund_nav.csv"
    pd.DataFrame({"date": ["2024-01-02"]}).to_csv(path, index=False)

    with pytest.raises(ValueError, match="fund_nav cannot substitute"):
        inspect_source("fund_nav", path, "date")


def test_518880_requires_ohlc_columns(tmp_path):
    path = tmp_path / "518880.csv"
    pd.DataFrame({"date": ["2024-01-02"], "close": [10.0]}).to_csv(path, index=False)

    with pytest.raises(ValueError, match="OHLC"):
        inspect_source("518880", path, "date")


@pytest.mark.parametrize(
    ("logical_name", "relative_path"),
    [
        ("518880_proxy", Path("prices.csv")),
        ("518880", Path("518880_prelisting.csv")),
        ("518880", Path("proxy") / "518880.csv"),
    ],
)
def test_518880_rejects_declared_proxy_or_prelisting_input(
    tmp_path, logical_name, relative_path
):
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _frame(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]).rename(
        columns={"trade_date": "date"}
    ).to_csv(path, index=False)

    with pytest.raises(ValueError, match="proxy|prelisting"):
        inspect_source(logical_name, path, "date")


def test_sha256_is_full_and_deterministic(tmp_path):
    path = tmp_path / "prices.csv"
    pd.DataFrame({"date": ["2024-01-02"]}).to_csv(path, index=False)

    first = inspect_source("benchmark", path, "date")
    second = inspect_source("benchmark", path, "date")

    assert len(first.sha256) == 64
    assert first.sha256 == second.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
