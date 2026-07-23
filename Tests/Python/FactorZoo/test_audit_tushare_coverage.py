"""Tests for the tushare coverage audit script (Phase 1, spec §6)."""
import sys
from pathlib import Path

# allow importing Scripts.factor_zoo when run from repo root
# test file lives at Tests/Python/FactorZoo/test_*.py so parents[3] is repo root
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))

from factor_zoo.audit_tushare_coverage import audit_table, CoverageStatus, _resolve_latest_trade_date


def test_audit_table_missing_dir_returns_not_downloaded(tmp_path):
    """A table dir that does not exist -> NOT_DOWNLOADED."""
    report = audit_table("does_not_exist", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.NOT_DOWNLOADED
    assert report.rows == 0
    assert report.last_date is None


def test_audit_table_empty_dir_returns_empty(tmp_path):
    """A table dir that exists but has no parquet -> EMPTY."""
    (tmp_path / "empty_table").mkdir()
    report = audit_table("empty_table", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.EMPTY


def test_audit_table_stale_returns_stale(tmp_path):
    """A table whose last trade_date < latest - 10 calendar days -> STALE."""
    # build a fake ts_code-partitioned table with one row at 20260101
    import pyarrow as pa, pyarrow.parquet as pq
    d = tmp_path / "daily_basic" / "ts_code=600519.SH"
    d.mkdir(parents=True)
    tbl = pa.table({"ts_code": ["600519.SH"], "trade_date": ["20260101"], "close": [100.0]})
    pq.write_table(tbl, d / "data.parquet")
    report = audit_table("daily_basic", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.STALE
    assert report.last_date == "20260101"
    assert report.rows == 1


def test_audit_table_recent_returns_ok(tmp_path):
    """A table whose last trade_date is within 10 calendar days of latest -> OK.

    Locks the `> 10` threshold direction: 10 days back is OK, >10 is STALE.
    20260712 is exactly 10 days before 20260722 -> OK.
    """
    import pyarrow as pa, pyarrow.parquet as pq
    d = tmp_path / "daily" / "ts_code=600519.SH"
    d.mkdir(parents=True)
    tbl = pa.table({"ts_code": ["600519.SH"], "trade_date": ["20260712"], "close": [100.0]})
    pq.write_table(tbl, d / "data.parquet")
    report = audit_table("daily", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.OK
    assert report.last_date == "20260712"
    assert report.rows == 1


def test_audit_table_skips_corrupt_parquet_without_crashing(tmp_path):
    """A non-parquet file named data.parquet must be skipped, not crash the run."""
    import pyarrow as pa, pyarrow.parquet as pq
    # one good file
    good = tmp_path / "daily" / "ts_code=600519.SH"
    good.mkdir(parents=True)
    pq.write_table(
        pa.table({"ts_code": ["600519.SH"], "trade_date": ["20260720"], "close": [100.0]}),
        good / "data.parquet",
    )
    # one corrupt file (garbage bytes, but named data.parquet)
    bad = tmp_path / "daily" / "ts_code=000001.SZ"
    bad.mkdir(parents=True)
    (bad / "data.parquet").write_bytes(b"not a parquet file at all")
    # must not raise
    report = audit_table("daily", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.OK
    assert report.last_date == "20260720"
    assert report.rows == 1  # only the good file counted
    assert report.partition_files == 1  # corrupt file skipped, not counted


def test_resolve_latest_trade_date_excludes_future_dates(tmp_path):
    """trade_cal is pre-populated for the full year with is_open=1 even on
    FUTURE dates. _resolve_latest_trade_date must filter cal_date <= today
    before taking the max, otherwise it returns a future date (e.g. 20261231)
    and misdiagnoses fresh tables as STALE.

    Synthetic trade_cal has a past open date (20260720) and a future open
    date (20261231). For any run-date in (20260720, 20261231) the function
    must return 20260720 (latest PAST open date), not 20261231.
    """
    import pyarrow as pa, pyarrow.parquet as pq
    d = tmp_path / "trade_cal"
    d.mkdir(parents=True)
    tbl = pa.table({
        "cal_date": ["20260720", "20261231"],
        "is_open": [1, 1],
    })
    pq.write_table(tbl, d / "data.parquet")
    resolved = _resolve_latest_trade_date(str(tmp_path))
    assert resolved == "20260720", (
        f"expected future date 20261231 to be filtered out, got {resolved}"
    )
